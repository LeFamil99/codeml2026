"""DA jobs outlive Streamlit reruns; PDF/OCR runs in a separate process.

The durable registry is independent of Streamlit caches and sessions. Request,
process identity, progress and results live under .cache/da_jobs. Reloads recover
existing workers; navigating away never cancels or resubmits their processes.
"""
from __future__ import annotations

import json
import fcntl
import os
import pickle
import subprocess
import sys
import threading
import time
import traceback
import tempfile
from pathlib import Path
from uuid import uuid4
from ..record_formats import align_result


def write_pickle(path, value):
    """Atomic durable replacement, also safe for simultaneous cache upgrades."""
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as output:
        temporary = Path(output.name)
        pickle.dump(value, output, protocol=pickle.HIGHEST_PROTOCOL)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def write_json(path: Path, data: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def execute_job(directory: Path, runner=None) -> int:
    """Worker entry; the optional runner is only for deterministic tests."""
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    state = {"status": "running", "started": time.time(), "current": 0,
             "total": sum(len(paths) for paths in request["inputs"].values()), "filename": "",
             "saved_files": [], "checkpoint_hits": 0, "saved_pages": [],
             "page_checkpoint_hits": 0}
    write_json(directory / "state.json", state)

    def progress(current, total, filename):
        state.update(current=current, total=total, filename=filename, current_page=0, file_pages=0)
        write_json(directory / "state.json", state)

    def file_done(current, total, path, reused):
        # Called only after the file checkpoint is committed and fsynced.
        state["saved_files"].append(path)
        state["checkpoint_hits"] += int(reused)
        write_json(directory / "state.json", state)

    def page_progress(filename, number, total, done, reused):
        state.update(filename=filename, current_page=number, file_pages=total)
        if done:
            state['saved_pages'].append(f"{filename}#page{number}")
            state['page_checkpoint_hits'] += int(reused)
        write_json(directory / "state.json", state)

    try:
        if runner is None:
            from .dashboard import run_da
            runner = run_da
        options = {}
        if request.get("checkpoint_dir"):
            options.update(checkpoint_dir=request["checkpoint_dir"],
                           reuse_checkpoints=request.get("reuse_checkpoints", True),
                           file_done=file_done, page_progress=page_progress)
        result = runner(request["project_dir"], inputs=request["inputs"], progress=progress, **options)
        align_result(result)
        # Only our trusted local worker creates this file. Pickle retains full
        # Pydantic debug metadata, which Appendix-A JSON intentionally excludes.
        write_pickle(directory / "result.pkl", result)
        state.update(status="completed", finished=time.time())
        write_json(directory / "state.json", state)
        return 0
    except Exception as error:
        state.update(status="failed", finished=time.time(),
                     error=f"{type(error).__name__}: {error}", traceback=traceback.format_exc())
        write_json(directory / "state.json", state)
        return 1


def launch_worker(directory: Path):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(Path(__file__).resolve().parents[2]), env.get("PYTHONPATH"))))
    with (directory / "worker.log").open("wb") as log:
        return subprocess.Popen([sys.executable, "-m", "l2c.da.jobs", str(directory)],
            cwd=directory, env=env, stdin=subprocess.DEVNULL, stdout=log,
            stderr=subprocess.STDOUT, start_new_session=True)


class Job:
    def __init__(self, directory: Path, process):
        self.directory = directory
        self.process = process
        self._result = None
        self._result_stamp = None

    def snapshot(self) -> dict:
        state = json.loads((self.directory / "state.json").read_text(encoding="utf-8"))
        returncode = self.process.poll()  # Also reap workers that have completed.
        if state["status"] in ("queued", "running") and returncode is not None:
            # Re-read: the worker can commit its final state between read and poll.
            state = json.loads((self.directory / "state.json").read_text(encoding="utf-8"))
            if state["status"] in ("queued", "running"):
                state.update(status="failed", finished=time.time(),
                             error=f"Le processus DA s'est arrêté (code {self.process.returncode}).")
                write_json(self.directory / "state.json", state)
        return {**state, "id": self.directory.name}

    @property
    def running(self) -> bool:
        return self.snapshot()["status"] in ("queued", "running")

    def result(self):
        state = self.snapshot()
        if state["status"] != "completed":
            raise RuntimeError(state.get("error", "DA generation is not complete"))
        result_path = self.directory / "result.pkl"
        stat = result_path.stat()
        stamp = (stat.st_mtime_ns,stat.st_size)
        if self._result is None or getattr(self,"_result_stamp",None) != stamp:
            with result_path.open("rb") as source:
                self._result = pickle.load(source)
            self._result_stamp = stamp
        changed = align_result(self._result)
        if changed:
            write_pickle(result_path, self._result)
            stat = result_path.stat()
            self._result_stamp = (stat.st_mtime_ns,stat.st_size)
        return self._result


class ProcessReference:
    """Observe an existing worker after a module/server reload; never signal it."""
    def __init__(self, pid: int, directory: Path):
        self.pid = pid
        self.directory = directory.resolve()
        self.returncode = None

    def poll(self):
        try:
            proc = Path(f"/proc/{self.pid}")
            command = proc.joinpath("cmdline").read_bytes().split(b"\0")
            status = proc.joinpath("stat").read_text().rsplit(")", 1)[1].split()[0]
            matches = b"l2c.da.jobs" in command and str(self.directory).encode() in command
            if status != "Z" and (matches or command == [b""]):
                return None
        except (FileNotFoundError, ProcessLookupError):
            pass
        self.returncode = 1
        return self.returncode


def freeze(value):
    return tuple(freeze(item) for item in value) if isinstance(value, list) else value


class JobManager:
    def __init__(self, root: Path | None = None, launcher=launch_worker):
        self.root = root or Path(__file__).resolve().parents[3] / ".cache/da_jobs"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.launcher = launcher
        self._jobs = {}
        self._active = {}
        self._lock = threading.RLock()

    def _recover(self):
        """Recover durable job identities before considering any new generation."""
        recovered = []
        invalidated = set()
        for directory in self.root.iterdir():
            if not directory.is_dir() or not (directory / "request.json").is_file():
                continue
            try:
                request = json.loads((directory / "request.json").read_text())
                state = json.loads((directory / "state.json").read_text())
                if request.get("invalidated"):
                    invalidated.add(directory.resolve())
                    continue
                if "stamp" not in request:
                    continue
                key = (request["project_dir"], freeze(request["stamp"]), request["parser_version"])
                current = self._jobs.get(key)
                if current and current.directory.resolve() == directory.resolve():
                    job = current
                elif state["status"] in ("completed", "failed"):
                    job = Job(directory, ProcessReference(0, directory))
                elif (directory / "process.json").is_file():
                    process = json.loads((directory / "process.json").read_text())
                    job = Job(directory, ProcessReference(process["pid"], directory))
                else:
                    # In-process test launchers stay in the original registry.
                    continue
                recovered.append((state["started"], key, job))
            except (OSError, ValueError, KeyError):
                continue
        # An external/selective cache reset must also remove stale references
        # held by a server that has not restarted since the reset.
        self._jobs = {key: job for key, job in self._jobs.items()
                      if job.directory.resolve() not in invalidated}
        self._active = {key: job for key, job in self._active.items()
                        if job.directory.resolve() not in invalidated}
        for _, key, job in sorted(recovered, key=lambda item: item[0]):
            self._jobs[key] = job
            self._active[key[0]] = job

    def _invalidate(self, job):
        request = json.loads((job.directory / "request.json").read_text())
        request["invalidated"] = True
        write_json(job.directory / "request.json", request)

    def lookup(self, project_dir, stamp, parser_version):
        """Read an existing matching job without starting generation."""
        key = (str(Path(project_dir).expanduser().resolve()), stamp, parser_version)
        with self._lock:
            self._recover()
            return self._jobs.get(key)

    def ensure(self, project_dir: str, stamp: tuple, parser_version: str,
               inputs, force=False, reparse=False) -> Job:
        project = str(Path(project_dir).expanduser().resolve())
        key = (project, stamp, parser_version)
        with self._lock, (self.root / ".registry.lock").open("a") as registry:
            fcntl.flock(registry, fcntl.LOCK_EX)
            self._recover()
            active = self._active.get(project)
            # Even regeneration or source changes must not duplicate an active run.
            if active and active.running:
                return active
            if not force and key in self._jobs:
                return self._jobs[key]
            # A new/retried job resumes by default. Only an explicit full cache
            # regeneration bypasses valid file results.
            reuse_checkpoints = not reparse
            if key in self._jobs:
                self._invalidate(self._jobs[key])
            from .dashboard import input_files
            selected = {}
            for kind, path in input_files(inputs):
                selected.setdefault(kind, []).append(str(path.resolve()))
            directory = self.root / uuid4().hex
            directory.mkdir(mode=0o700)
            write_json(directory / "request.json", {"project_dir": project,
                       "inputs": selected, "parser_version": parser_version, "stamp": stamp,
                       "checkpoint_dir": str(self.root.resolve() / "file_results"),
                       "reuse_checkpoints": reuse_checkpoints})
            write_json(directory / "state.json", {"status": "queued", "started": time.time(),
                       "current": 0, "total": sum(map(len, selected.values())), "filename": ""})
            try:
                process = self.launcher(directory)
                if getattr(process, "pid", None):
                    write_json(directory / "process.json", {"pid": process.pid})
            except Exception as error:
                write_json(directory / "state.json", {"status": "failed", "started": time.time(),
                           "error": f"{type(error).__name__}: {error}"})
                raise
            job = self._jobs[key] = self._active[project] = Job(directory, process)
            return job

    def clear_completed(self) -> None:
        """Clear results without cancelling work that is still running."""
        with self._lock, (self.root / ".registry.lock").open("a") as registry:
            fcntl.flock(registry, fcntl.LOCK_EX)
            self._recover()
            for job in self._jobs.values():
                if not job.running:
                    self._invalidate(job)
            self._jobs = {key: job for key, job in self._jobs.items() if job.running}
            self._active = {key: job for key, job in self._active.items() if job.running}


_manager = None
_manager_lock = threading.Lock()


def get_manager() -> JobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = JobManager()
        return _manager


if __name__ == "__main__":
    raise SystemExit(execute_job(Path(sys.argv[1])))
