"""The dashboard, driven headlessly against the real corpus.

Streamlit's AppTest executes the real script, so an exception anywhere in intake,
extraction, the charts or the download buttons fails this test.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(__file__), "..", "app", "streamlit_app.py")


@pytest.fixture(scope="module")
def app(corpus):
    at = AppTest.from_file(APP, default_timeout=180)
    at.run()
    return at


def test_app_runs_without_exceptions(app):
    assert not app.exception, [str(e) for e in app.exception]


def test_it_discovers_the_projects_and_selects_one(app):
    options = app.sidebar.selectbox[0].options
    for project in ("CLP", "WP2", "LIGREP", "EspCa3B"):
        assert project in options


def test_headline_metrics_are_populated(app):
    labels = {m.label: m.value for m in app.metric}
    assert "Localisés sur la grille" in labels
    assert "Système d'unités" in labels
    assert labels["Système d'unités"] == "imperial"      # CLP is selected first


def test_all_five_tabs_present(app):
    assert len(app.tabs) == 5


def test_switching_project_reruns_cleanly(app):
    app.sidebar.selectbox[0].set_value("EspCa3B").run()
    assert not app.exception, [str(e) for e in app.exception]
    labels = {m.label: m.value for m in app.metric}
    assert labels["Système d'unités"] == "metric"
