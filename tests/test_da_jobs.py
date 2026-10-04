"""Running, completed and failed DA jobs persist across navigation and reruns."""
import threading
import json
import pytest

from l2c.da import jobs, dashboard
from l2c.pipeline import ProjectResult, SheetReport
from test_da_dashboard import connected_parsers


def test_worker_retry_after_crash_reuses_committed_file_and_saves_progress(
        tmp_path, monkeypatch, background_jobs, connected_parsers):
    # Exercise the actual worker + dashboard + adapters, with synthetic OCR only.
    from test_da_dashboard import sources
    project = sources(tmp_path)
    inputs = dashboard.configured_inputs(str(project))
    original = dashboard.PARSERS["dalle"]
    def crash(*a, **k):
        raise RuntimeError("worker crashed after the first PDF")
    monkeypatch.setitem(dashboard.PARSERS, "dalle", crash)
    first = background_jobs.ensure(str(project), (), dashboard.PARSER_VERSION, inputs)
    first.process.future.result(timeout=5)
    state = first.snapshot()
    assert state["status"] == "failed" and len(state["saved_files"]) == 1
    assert len(dashboard.checkpoint_inventory(inputs, background_jobs.root / "file_results")) == 1
    monkeypatch.setitem(dashboard.PARSERS, "dalle", original)
    # Simulate losing the in-memory registry (page/server reload).
    restored = jobs.JobManager(background_jobs.root, launcher=background_jobs.launcher)
    retry = restored.ensure(str(project), (), dashboard.PARSER_VERSION, inputs, force=True)
    retry.process.future.result(timeout=5)
    assert retry.result().meta["checkpoint_hits"] == 1
    assert len(retry.snapshot()["saved_files"]) == 5
    assert retry.snapshot()["checkpoint_hits"] == 1
    assert sum(call[0] == "colonne" for call in connected_parsers) == 2
    again = restored.ensure(str(project), (), dashboard.PARSER_VERSION, inputs, force=True)
    again.process.future.result(timeout=5)
    assert again.result().meta["checkpoint_hits"] == 5 and len(connected_parsers) == 6
    assert json.loads((again.directory / "request.json").read_text())["reuse_checkpoints"] is True


def test_external_column_cache_reset_reparses_only_columns_in_same_server(
        tmp_path, connected_parsers, background_jobs):
    from test_da_dashboard import sources
    project = sources(tmp_path)
    inputs = dashboard.configured_inputs(str(project))
    first = background_jobs.ensure(str(project), (), dashboard.PARSER_VERSION, inputs)
    first.process.future.result(timeout=5)
    request = json.loads((first.directory/'request.json').read_text())
    request['invalidated'] = True
    jobs.write_json(first.directory/'request.json', request)
    cache = background_jobs.root/'file_results'
    for path in [*cache.glob('*.pkl'), *(cache/'pages').glob('*.pkl')]:
        import pickle
        saved = pickle.loads(path.read_bytes())
        if saved['fingerprint'][1] == 'colonne':
            path.unlink()
    # Same manager, same source stamp, no force: the invalidated result must go.
    fresh = background_jobs.ensure(str(project), (), dashboard.PARSER_VERSION, inputs)
    fresh.process.future.result(timeout=5)
    assert fresh.directory != first.directory
    assert fresh.result().meta['checkpoint_hits'] == 4
    assert len(connected_parsers) == 8
    assert sum(call[0] != 'colonne' for call in connected_parsers) == 4


@pytest.mark.parametrize('interrupt', ['between_files', 'within_column'])
def test_abrupt_process_exit_keeps_checkpoints_after_manager_reload(tmp_path, interrupt):
    import subprocess
    import sys
    import os
    from test_da_dashboard import sources
    project = sources(tmp_path)
    inputs = {k: v for k, v in dashboard.configured_inputs(str(project)).items()
              if k in ("colonne", "dalle")}
    launches = []
    def launch(directory):
        crash = not launches
        launches.append(directory)
        script = """
import os, sys
from pathlib import Path
from l2c.da import dashboard, jobs
def reader(page, filename):
    assert page.number in (0, 1) if 'COLONNES' in filename else page.number == 0
    if ((sys.argv[2] == 'between_files' and 'DALLE' in filename) or
        (sys.argv[2] == 'within_column' and 'COLONNES' in filename and page.number == 1)):
        os._exit(137)
    return [], {}
dashboard.PARSERS.update(colonne=reader, dalle=reader)
raise SystemExit(jobs.execute_job(Path(sys.argv[1])))
"""
        env = dict(os.environ, PYTHONPATH=str(__import__('pathlib').Path(dashboard.__file__).resolve().parents[2]))
        return subprocess.Popen([sys.executable, "-c", script, str(directory),
                                 interrupt if crash else "resume"], env=env)
    root = tmp_path / "jobs"
    first = jobs.JobManager(root, launcher=launch).ensure(str(project), (), "test", inputs)
    assert first.process.wait(timeout=20) == 137
    assert first.snapshot()["status"] == "failed"
    assert len(first.snapshot()["saved_files"]) == (1 if interrupt == 'between_files' else 0)
    assert len(first.snapshot()["saved_pages"]) == (2 if interrupt == 'between_files' else 1)
    restored = jobs.JobManager(root, launcher=launch)
    retry = restored.ensure(str(project), (), "test", inputs, force=True)
    assert retry.process.wait(timeout=20) == 0
    assert retry.result().meta["checkpoint_hits"] == (1 if interrupt == 'between_files' else 0)
    assert retry.result().meta["page_checkpoint_hits"] == (0 if interrupt == 'between_files' else 1)
    assert len(retry.snapshot()["saved_files"]) == 2


def test_active_job_is_shared_even_when_forced_or_inputs_change(tmp_path, monkeypatch, background_jobs):
    started, release = threading.Event(), threading.Event()
    calls = []
    def blocked(*args, **kwargs):
        calls.append(args)
        kwargs["progress"](1, 4, "first.pdf")
        started.set()
        assert release.wait(10)
        return ProjectResult("CLP", "DA", "imperial", {}, [], [], 0.)
    monkeypatch.setattr(dashboard, "run_da", blocked)
    project = str(tmp_path / "CLP")
    try:
        first = background_jobs.ensure(project, (), "v1", {})
        assert started.wait(3)
        assert first.snapshot()["filename"] == "first.pdf"
        for force in (False, True):
            assert background_jobs.ensure(project, ("changed",), "v2", {}, force=force) is first
        background_jobs.clear_completed()
        assert background_jobs.ensure(project, (), "v1", {}) is first
        assert len(calls) == 1
    finally:
        release.set()
    first.process.future.result(timeout=5)
    assert first.result().project == "CLP"
    assert background_jobs.ensure(project, (), "v1", {}) is first


def test_failure_is_visible_and_only_explicit_regeneration_retries(tmp_path, monkeypatch, background_jobs):
    calls = []
    def failed(*args, **kwargs):
        calls.append(1)
        raise ValueError("Unread input")
    monkeypatch.setattr(dashboard, "run_da", failed)
    project = str(tmp_path / "CLP")
    first = background_jobs.ensure(project, (), "v1", {})
    first.process.future.result(timeout=5)
    assert first.snapshot()["status"] == "failed"
    assert "Unread input" in first.snapshot()["error"]
    assert background_jobs.ensure(project, (), "v1", {}) is first and len(calls) == 1
    second = background_jobs.ensure(project, (), "v1", {}, force=True)
    second.process.future.result(timeout=5)
    assert second is not first and len(calls) == 2


def test_real_worker_process_preserves_result_metadata(tmp_path):
    manager = jobs.JobManager(tmp_path / "jobs")
    project = str(tmp_path / "CLP")
    # No OCR required: verify process launch, atomic status, result and reattachment.
    job = manager.ensure(project, (), "test", {"semelle": tmp_path / "missing.pdf"})
    try:
        assert job.process.wait(timeout=20) == 0
        assert job.snapshot()["status"] == "completed"
        result = job.result()
        assert len(result.sheets) == 1 and result.sheets[0].status == "unread"
        assert result.meta["last_page_only"] and result.meta["pending_types"] == []
        assert manager.ensure(project, (), "test", {"semelle": "unused.pdf"}) is job
    finally:
        if job.process.poll() is None:
            job.process.terminate()
            job.process.wait(timeout=5)


def test_new_manager_recovers_live_worker_and_completed_result_without_launching(tmp_path):
    root = tmp_path / "jobs"
    manager = jobs.JobManager(root)
    project = str(tmp_path / "CLP")
    inputs = {"semelle": tmp_path / "missing.pdf"}
    job = manager.ensure(project, (("file", 123),), "test", inputs)
    def forbidden_launch(directory):
        raise AssertionError("A recovered job must never be restarted")
    try:
        restored = jobs.JobManager(root, launcher=forbidden_launch)
        attached = restored.ensure(project, (("file", 123),), "test", inputs)
        assert attached.directory == job.directory
        assert attached.process.pid == job.process.pid or attached.snapshot()["status"] == "completed"
        assert job.process.wait(timeout=20) == 0
        # Another reload after completion must recover the saved result too.
        again = jobs.JobManager(root, launcher=forbidden_launch)
        completed = again.ensure(project, (("file", 123),), "test", inputs)
        assert completed.directory == job.directory and completed.result().project == "CLP"
        assert completed.result().sheets[0].status == "unread"
    finally:
        if job.process.poll() is None:
            job.process.terminate()
            job.process.wait(timeout=5)


def test_two_managers_deduplicate_simultaneous_starts(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    root = tmp_path / "jobs"
    managers = [jobs.JobManager(root), jobs.JobManager(root)]
    project = str(tmp_path / "CLP")
    def start(manager):
        return manager.ensure(project, (), "test", {"semelle": tmp_path / "missing.pdf"})
    with ThreadPoolExecutor(max_workers=2) as pool:
        attached = list(pool.map(start, managers))
    assert attached[0].directory == attached[1].directory
    original = next(job for job in attached if hasattr(job.process, "wait"))
    try:
        assert original.process.wait(timeout=20) == 0
    finally:
        if original.process.poll() is None:
            original.process.terminate()
            original.process.wait(timeout=5)


