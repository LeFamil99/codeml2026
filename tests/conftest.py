import os
import pytest

CORPUS = os.environ.get("L2C_CORPUS", os.path.expanduser("~/Downloads/l2c-participants"))
PROJECTS = ["CLP", "WP2", "LIGREP", "EspCa3B"]


def plan_path(project: str) -> str:
    return os.path.join(CORPUS, project, f"L2C_PLAN_STR_{project}.pdf")


@pytest.fixture(scope="session")
def corpus() -> str:
    if not os.path.isdir(CORPUS):
        pytest.skip(f"corpus not available at {CORPUS}")
    return CORPUS
