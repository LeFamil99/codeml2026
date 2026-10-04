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

#: measured on the whole plan set (2026-10-03); floors sit ~5% under the measurement
EXPECTED = {
    "CLP":     {"min_records": 1925, "min_located_pct": 99.0, "units": "imperial"},
    "WP2":     {"min_records": 3475, "min_located_pct": 99.0, "units": "metric"},
    "LIGREP":  {"min_records": 2455, "min_located_pct": 95.0, "units": "metric"},
    "EspCa3B": {"min_records": 2160, "min_located_pct": 99.0, "units": "metric"},
}

#: per-type record counts measured on the whole plan set (radier: LIGREP's are on S-100)
MEASURED_BY_TYPE = {
    "CLP":     {"radier": 74, "semelle": 75, "poutre": 177, "mur_refend": 80,
                "colonne": 395, "dalle": 1226},
    "WP2":     {"radier": 31, "semelle": 124, "poutre": 244, "mur_refend": 134,
                "colonne": 912, "dalle": 2214},
    "LIGREP":  {"radier": 30, "semelle": 112, "poutre": 218, "mur_refend": 108,
                "colonne": 677, "dalle": 1470},
    "EspCa3B": {"radier": 54, "semelle": 20, "poutre": 183, "mur_refend": 241,
                "colonne": 644, "dalle": 1134},
}


@pytest.fixture(scope="module")
def results(corpus):
    return {p: run_plan(plan_path(p)) for p in PROJECTS}


@pytest.mark.parametrize("project", PROJECTS)
def test_extracts_expected_volume(results, project):
    r = results[project]
    exp = EXPECTED[project]
    assert len(r.records) >= exp["min_records"], f"{project}: extraction regressed"


@pytest.mark.parametrize("project", PROJECTS)
def test_every_element_type_is_extracted(results, project):
    """The whole plan set, not just columns: each type present in the project is read."""
    got: dict[str, int] = {}
    for x in results[project].records:
        got[x.type_element] = got.get(x.type_element, 0) + 1
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
