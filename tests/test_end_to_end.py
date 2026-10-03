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

#: measured floor for located-on-grid ratio, per project (PLAN SS6.1 collinear axes)
EXPECTED = {
    "CLP":     {"min_records": 390, "min_located_pct": 99.0, "units": "imperial"},
    "WP2":     {"min_records": 1080, "min_located_pct": 99.0, "units": "metric"},
    "LIGREP":  {"min_records": 670, "min_located_pct": 95.0, "units": "metric"},
    "EspCa3B": {"min_records": 640, "min_located_pct": 99.0, "units": "metric"},
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


@pytest.mark.parametrize("project", PROJECTS)
def test_skipped_sheets_are_reported_not_hidden(results, project):
    """A sheet we cannot handle must say so, never count as zero findings."""
    skipped = [s for s in results[project].sheets if s.status == "skipped"]
    assert skipped, "this slice only does columns, so some sheets must be skipped"
    assert all(s.reason for s in skipped)


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
