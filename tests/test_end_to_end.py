"""End-to-end runs against the real corpus.

These are the tests that answer "are the results good?". They assert measured
behaviour on all four development projects, not just the one with an answer key.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from conftest import PROJECTS, plan_path
from l2c import io_json
from l2c.pipeline import run_plan

#: measured on the whole plan set (2026-10-04); floors sit ~5% under the measurement
EXPECTED = {
    "CLP":     {"min_observations": 2350, "min_located_pct": 99.0, "units": "imperial"},
    "WP2":     {"min_observations": 4445, "min_located_pct": 99.0, "units": "metric"},
    "LIGREP":  {"min_observations": 3380, "min_located_pct": 95.0, "units": "metric"},
    "EspCa3B": {"min_observations": 2665, "min_located_pct": 99.0, "units": "metric"},
}

#: Historical observation counts; poutre counts are armatures within beam records.
MEASURED_BY_TYPE = {
    "CLP":     {"radier": 77, "semelle": 75, "poutre": 187, "mur_refend": 119,
                "colonne": 395, "dalle": 1615},
    "WP2":     {"radier": 35, "semelle": 124, "poutre": 283, "mur_refend": 212,
                "colonne": 912, "dalle": 3115},
    "LIGREP":  {"radier": 30, "semelle": 112, "poutre": 240, "mur_refend": 174,
                "colonne": 677, "dalle": 2329},
    "EspCa3B": {"radier": 54, "semelle": 20, "poutre": 212, "mur_refend": 369,
                "colonne": 644, "dalle": 1507},
}


@pytest.fixture(scope="module")
def results(corpus):
    return {p: run_plan(plan_path(p)) for p in PROJECTS}


@pytest.mark.parametrize("project", PROJECTS)
def test_extracts_expected_volume(results, project):
    r = results[project]
    exp = EXPECTED[project]
    volume = sum(len(x.armature) if x.type_element == "poutre" else 1 for x in r.records)
    assert volume >= exp["min_observations"], f"{project}: extraction regressed"


@pytest.mark.parametrize("project", PROJECTS)
def test_every_element_type_is_extracted(results, project):
    """The whole plan set, not just columns: each type present in the project is read."""
    got: dict[str, int] = {}
    for x in results[project].records:
        count = len(x.armature) if x.type_element == "poutre" else 1
        got[x.type_element] = got.get(x.type_element, 0) + count
    for kind, n in MEASURED_BY_TYPE[project].items():
        assert got.get(kind, 0) >= 0.9 * n, f"{project}/{kind}: {got.get(kind, 0)} < 90% of {n}"


@pytest.mark.parametrize("project", PROJECTS)
def test_record_ids_are_unique(results, project):
    ids = [x.id for x in results[project].records]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("project", PROJECTS)
def test_locator_coverage(results, project):
    r = results[project]
    located = sum(1 for x in r.records if x.element != "UNKNOWN")
    pct = 100 * located / len(r.records)
    assert pct >= EXPECTED[project]["min_located_pct"], (
        f"{project}: only {pct:.1f}% of elements got a grid locator"
    )


@pytest.mark.parametrize("project", PROJECTS)
def test_unit_system_autodetected(results, project):
    """CLP is the ONLY imperial project and also the only one with an answer key,
    so a hardcoded unit system would be wrong on three of four (PLAN SS5.6)."""
    assert results[project].unit_system == EXPECTED[project]["units"]


@pytest.mark.parametrize("project", PROJECTS)
def test_every_record_is_schema_valid(results, project, tmp_path):
    path = tmp_path / f"{project}.json"
    io_json.write_records(str(path), results[project].records)
    count, errors = io_json.validate_file(str(path))
    assert errors == [], errors
    assert count == len(results[project].records)


@pytest.mark.parametrize("project", PROJECTS)
def test_output_is_deterministic(results, project, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    io_json.write_records(str(a), results[project].records)
    io_json.write_records(str(b), run_plan(plan_path(project)).records)
    assert a.read_bytes() == b.read_bytes(), f"{project}: output not reproducible"


def test_known_non_conformity_is_derived_from_the_plan(results):
    """Answer-key row 4: S-502 / K-6 / plan = 4-35M.

    The pipeline must reach this independently - grid axes, symbol detection, scale
    calibration and the global callout<->symbol assignment all have to be right.
    """
    k6 = [r for r in results["CLP"].records
          if r.feuillet == "S-502" and r.element == "K-6"]
    assert len(k6) == 1, "expected exactly one K-6 element on S-502"
    bars = k6[0].armature[0]
    assert bars.quantite == 4
    assert bars.diametre == "35M"


def test_scale_calibrates_to_a_standard_architectural_scale(results):
    """CLP S-502 must derive 0.75 pt/inch = 1/8" = 1'-0"."""
    s502 = [s for s in results["CLP"].sheets if s.feuillet == "S-502"][0]
    assert s502.diagnostics["scale_pt_per_inch"] == pytest.approx(0.75, abs=0.01)


def test_the_35M_outlier_is_unique_on_its_sheet(results):
    """Anomaly channel (PLAN SS8.3): the planted error is a singleton."""
    s502 = [r for r in results["CLP"].records if r.feuillet == "S-502"]
    sizes = [a.diametre for r in s502 for a in r.armature if a.quantite]
    assert sizes.count("35M") == 1
    assert sizes.count("25M") > 50


def test_radiers_drawn_on_the_foundations_plan_are_read(results):
    """LIGREP has no S-050: its six mats are on S-100 "PLAN DES FONDATIONS", beside the
    isolated footings. Both are read, the footing schedule rows (``15-25M``) are not
    mistaken for mat bars, and the ordinal layer (``1E RANG``) is kept."""
    rad = [r for r in results["LIGREP"].records if r.type_element == "radier"]
    assert {r.feuillet for r in rad} == {"S-100"}
    assert {r.debug.radier for r in rad} == {f"RADIER #{n}" for n in range(1, 7)}
    assert all(r.element != "UNKNOWN" and r.armature[0].espacement_mm for r in rad)
    layered = [r for r in rad if r.debug.layer]
    assert len(layered) == 8 and {r.debug.layer for r in layered} == {"1", "2"}
    assert sum(r.type_element == "semelle" for r in results["LIGREP"].records) == 112


@pytest.mark.parametrize("project", PROJECTS)
def test_every_titled_beam_has_reinforcement(results, project):
    """Beam rows run into the title-block corner (CLP P112, WP2 P114 / P302 / P054):
    those beams used to come out with no record at all."""
    bare = [w for s in results[project].sheets if s.type_element == "poutre"
            for w in s.diagnostics.get("warnings", []) if "no bar callout" in w]
    assert not bare, bare


@pytest.mark.parametrize("project", PROJECTS)
def test_wall_web_steel_is_read(results, project):
    """Each storey panel's distributed steel (``H.:15M@9"`` over ``V.:10M@8"``) is a
    record of the same élévation / storey element as the panel's boundary zones."""
    walls = [r for r in results[project].records if r.type_element == "mur_refend"]
    web = [r for r in walls if getattr(r.debug, "role", None) == "âme"]
    assert len(web) >= 0.25 * len(walls), f"{project}: {len(web)} web of {len(walls)}"
    assert all(r.element != "UNKNOWN" for r in web)
    assert all(len(r.armature) == 2 for r in web if len(r.debug.raw) == 2)
    boundary = {(r.feuillet, r.element.split(" - ")[0]) for r in walls if r not in web}
    assert {(r.feuillet, r.element.split(" - ")[0]) for r in web} <= boundary


def test_wall_callouts_follow_the_drawn_wall_not_the_nearest_title(results):
    """WP2 S-400: view B's right-end callouts sit closer to title C; the drawn wall
    extent (682-1270 pt) settles it."""
    b = [r for r in results["WP2"].records
         if r.feuillet == "S-400" and r.element.startswith("élévation B ")]
    assert any(1290 < r.x < 1330 for r in b)
    assert not any(1290 < r.x < 1330 for r in results["WP2"].records
                   if r.feuillet == "S-400" and r.element.startswith("élévation C "))


def test_views_reaching_the_right_margin_are_read(results):
    """The plan can run past the notes-column cut: WP2's RADIER #6 view (grid X) and
    LIGREP's east wing, whose grid lines are numbered 16E / 16.9E / 17.9E."""
    wp2 = [r for r in results["WP2"].records if r.type_element == "radier"]
    assert any(r.element.startswith("X-") for r in wp2)
    east = {"16E", "16.9E", "17.9E"}
    for kind in ("colonne", "dalle"):
        got = {r.element.rsplit("-", 1)[-1] for r in results["LIGREP"].records
               if r.type_element == kind}
        assert east <= got, f"LIGREP {kind}: {east - got} never used as a locator"


def test_wall_web_pairs_list_horizontal_bars_first(results):
    """CLP S-400 view C writes ``V.:`` over ``H.:``; the pair is still one record, H first."""
    web = [r for r in results["CLP"].records if r.feuillet == "S-400"
           and r.element.startswith("élévation C ") and getattr(r.debug, "role", None) == "âme"]
    assert len(web) == 7
    assert all(r.debug.raw[0].startswith("H.") and len(r.armature) == 2 for r in web)
    assert all("_âme" in r.id for r in web)


@pytest.mark.parametrize("project", PROJECTS)
def test_no_grid_line_is_named_after_a_bar_size(results, project):
    """``16E`` is a grid label (LIGREP's east wing); ``15M`` beside a line end is not."""
    import re
    bad = {r.element for r in results[project].records
           if r.type_element != "mur_refend" and re.search(r"\d{2}M\b", r.element)}
    assert not bad, bad


def test_two_line_footing_schedule_cell_is_read(results):
    """WP2 TYPE D: ``10-20M`` over ``+ ÉP. 15M@300`` in both ARM. columns."""
    d = [r for r in results["WP2"].records
         if r.type_element == "semelle" and r.debug.raw[0] == "TYPE D"]
    assert d
    for r in d:
        assert [(a.quantite, a.diametre, a.espacement_mm) for a in r.armature] == [
            (10, "20M", None), (None, "15M", 300.0), (10, "20M", None), (None, "15M", 300.0)]
    assert not [r.id for p in PROJECTS for r in results[p].records
                if r.type_element == "semelle" and not r.armature]


@pytest.mark.parametrize("project", PROJECTS)
def test_a_column_callout_is_never_paired_with_a_far_symbol(results, project):
    """Every true pair on the corpus is under 90 pt apart; WP2 S-501 used to shift a whole
    bay by one row (19 pairs 113-425 pt apart) because one row's symbols are in a wall."""
    far = [(r.feuillet, r.element, r.debug.assoc_distance) for r in results[project].records
           if r.type_element == "colonne" and (r.debug.assoc_distance or 0) > 100]
    assert not far, far


def test_column_callout_variants_are_read(results):
    """Round sections (``COL. 500mmØ``) find their disc; split tokens (``LIG.`` ``:``
    ``10M`` ``@100``) still give bars and ties."""
    for p in ("LIGREP", "EspCa3B"):
        rnd = [r for r in results[p].records
               if r.type_element == "colonne" and r.debug.raw[0].endswith("Ø")]
        assert rnd and all(r.debug.locator_kind == "grid" for r in rnd), p
    wp2 = [r for r in results["WP2"].records if r.type_element == "colonne"]
    assert all(len(r.armature) == 2 for r in wp2)


@pytest.mark.parametrize("project", PROJECTS)
def test_skipped_sheets_are_reported_not_hidden(results, project):
    """Sheets without element reinforcement (typical details, general plans) must say
    why, never count as zero findings - and no typed sheet may be skipped."""
    for s in results[project].sheets:
        if s.status == "skipped":
            assert s.reason
            assert s.type_element is None, f"{s.feuillet} is {s.type_element} but skipped"


def test_every_answer_key_row_is_derived_from_the_plan(results, corpus):
    """All six CLP_dismatch.xlsx rows - one per element type except beams - must be
    reachable: a record on that sheet, at that locator, stating the plan value.
    The key is read at run time, never copied into the code."""
    import os

    from l2c import answer_key

    path = os.path.join(corpus, "CLP", "CLP_dismatch.xlsx")
    if not os.path.isfile(path):
        pytest.skip("answer key not available")
    verdicts = answer_key.check(results["CLP"].records, answer_key.load(path), "imperial")
    assert len(verdicts) == 6
    failed = [(v.row.feuillet, v.row.localisation, v.row.plan) for v in verdicts if not v.found]
    assert not failed, failed


def test_cli_run_and_validate(corpus, tmp_path):
    import os
    out = tmp_path / "out"
    r = subprocess.run(
        [sys.executable, "-m", "l2c.cli", "run",
         os.path.join(corpus, "CLP"), "--out", str(out), "--quiet"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    produced = out / "CLP" / "elements_plan.json"
    assert produced.is_file()
    payload = json.loads(produced.read_text())
    assert isinstance(payload, list) and payload

    v = subprocess.run(
        [sys.executable, "-m", "l2c.cli", "validate", str(produced)],
        capture_output=True, text=True,
    )
    assert v.returncode == 0, v.stdout + v.stderr
    assert "VALID" in v.stdout
