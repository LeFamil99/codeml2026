import os
import pytest
import time

CORPUS = os.environ.get("L2C_CORPUS", os.path.expanduser("~/Downloads/l2c-participants"))
PROJECTS = ["CLP", "WP2", "LIGREP", "EspCa3B"]


def plan_path(project: str) -> str:
    return os.path.join(CORPUS, project, f"L2C_PLAN_STR_{project}.pdf")


@pytest.fixture(scope="session")
def corpus() -> str:
    if not os.path.isdir(CORPUS):
        pytest.skip(f"corpus not available at {CORPUS}")
    return CORPUS


def wait_for_da(at, timeout=10):
    """AppTest has no browser timer; explicitly poll a background job."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        assert not at.exception, [str(e) for e in at.exception]
        assert not at.error, [e.value for e in at.error]
        if any(metric.label == "Pages traitées" for metric in at.metric):
            return at
        time.sleep(.01)
        at.run()
    raise AssertionError("DA job did not finish before the test deadline")


@pytest.fixture
def background_jobs(tmp_path, monkeypatch):
    """Mock runners execute off-page; production always uses an isolated process."""
    from concurrent.futures import ThreadPoolExecutor
    import l2c.da.jobs as jobs
    import l2c.da.dashboard as dashboard
    executor = ThreadPoolExecutor(max_workers=1)
    class Process:
        def __init__(self, future):
            self.future = future
            self.returncode = None
        def poll(self):
            if self.future.done():
                self.returncode = self.future.result()
            return self.returncode
    def launch(directory):
        return Process(executor.submit(jobs.execute_job, directory, dashboard.run_da))
    manager = jobs.JobManager(tmp_path / "jobs", launcher=launch)
    monkeypatch.setattr(jobs, "get_manager", lambda: manager)
    yield manager
    executor.shutdown(wait=True)
