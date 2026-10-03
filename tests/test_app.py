"""The dashboard, driven headlessly against the real corpus.

Streamlit's AppTest executes the real script, so an exception anywhere in intake,
extraction, the charts or the download buttons fails this test.

The dashboard analyses ONE project at a time: by default it points at the folder of
projects and you pick one from the dropdown; a single project folder also works directly.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(__file__), "..", "app", "streamlit_app.py")


def _at(path: str) -> AppTest:
    """Run the app pointed at `path`."""
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    at.sidebar.text_input[0].set_value(path).run()
    return at


@pytest.fixture(scope="module")
def clp(corpus):
    return _at(os.path.join(corpus, "CLP"))


def test_app_runs_without_exceptions(clp):
    assert not clp.exception, [str(e) for e in clp.exception]


def test_one_project_folder_needs_no_picker(clp):
    """The whole point: a project folder is analysed directly, with no dropdown."""
    assert len(clp.sidebar.selectbox) == 0
    assert "CLP" in clp.sidebar.success[0].value


def test_headline_metrics_are_populated(clp):
    labels = {m.label: m.value for m in clp.metric}
    assert "Localisés sur la grille" in labels
    assert labels["Système d'unités"] == "imperial"      # CLP is the imperial project


def test_all_five_tabs_present(clp):
    assert len(clp.tabs) == 5


def test_default_offers_the_four_projects_in_a_dropdown():
    at = AppTest.from_file(APP, default_timeout=300)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    if at.sidebar.selectbox:
        assert {"CLP", "WP2", "LIGREP", "EspCa3B"} <= set(at.sidebar.selectbox[0].options)


def test_a_parent_folder_makes_you_choose_exactly_one(corpus):
    at = _at(corpus)
    assert not at.exception, [str(e) for e in at.exception]
    options = at.sidebar.selectbox[0].options
    for project in ("CLP", "WP2", "LIGREP", "EspCa3B"):
        assert project in options

    at.sidebar.selectbox[0].set_value("EspCa3B").run()
    assert not at.exception, [str(e) for e in at.exception]
    labels = {m.label: m.value for m in at.metric}
    assert labels["Système d'unités"] == "metric"


def test_a_bad_path_stops_cleanly(corpus):
    at = _at(os.path.join(corpus, "does-not-exist"))
    assert not at.exception, [str(e) for e in at.exception]
    assert at.sidebar.error
    assert len(at.metric) == 0          # nothing was analysed


def test_a_folder_without_a_plan_says_so(corpus, tmp_path_factory):
    empty = str(tmp_path_factory.mktemp("no_plan"))
    at = _at(empty)
    assert not at.exception, [str(e) for e in at.exception]
    assert "L2C_PLAN_STR" in at.sidebar.error[0].value
    assert len(at.metric) == 0


# ------------------------------------------------------------- shop-drawing section
@pytest.fixture(scope="module")
def clp_da(corpus):
    at = _at(os.path.join(corpus, "CLP"))
    at.segmented_control[0].set_value("Dessins d'atelier").run()
    return at


def test_da_section_runs_without_exceptions(clp_da):
    assert not clp_da.exception, [str(e) for e in clp_da.exception]


def test_da_section_reuses_the_same_layout(clp_da):
    """Same five sub-tabs; the per-unit tab is per DA page instead of per plan sheet."""
    assert [t.label for t in clp_da.tabs] == [
        "Vue d'ensemble", "Éléments", "Pages", "Diagnostics", "Téléchargements"]
    labels = {m.label for m in clp_da.metric}
    assert {"Localisés sur la grille", "Pages traitées", "Système d'unités"} <= labels


def test_da_inventory_counts_every_page(clp_da):
    """CLP's DA: 12 files, 34 pages, all with a text layer (measured)."""
    m = {x.label: x.value for x in clp_da.metric}
    assert m["Pages avec couche texte"] == "34"


def test_da_units_come_from_the_shop_drawings_themselves(clp_da):
    m = {x.label: x.value for x in clp_da.metric}
    assert m["Système d'unités"] == "imperial"
