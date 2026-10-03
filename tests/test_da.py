"""Shop-drawing (DA) side against the real corpus. Measured 2026-10-03."""

from __future__ import annotations

import os
import pathlib

import pytest

from conftest import CORPUS
from l2c import answer_key, io_json
from l2c.da.inventory import inventory
from l2c.da.pipeline import run_da
from l2c.units import BAR_DESIGNATORS

#: files, pages, text-layer pages - the census DA_PLAN.md is built on
CENSUS = {"CLP": (12, 34, 34), "WP2": (29, 72, 0), "LIGREP": (42, 191, 47), "EspCa3B": (54, 124, 0)}


@pytest.fixture(scope="module")
def clp(corpus):
    return run_da(os.path.join(corpus, "CLP"))


@pytest.fixture(scope="module")
def ligrep(corpus):
    return run_da(os.path.join(corpus, "LIGREP"))


@pytest.mark.parametrize("project", list(CENSUS))
def test_inventory_matches_the_census(corpus, project):
    files = inventory(os.path.join(corpus, project))
    n_files, n_pages, n_text = CENSUS[project]
    assert len(files) == n_files
    assert sum(f.pages for f in files) == n_pages
    assert sum(t == 1 for f in files for t in f.tiers) == n_text
    assert all(f.type_element for f in files), "every DA folder maps to an element type"


def test_every_page_is_reported(clp):
    """34 pages in, 34 page reports out - read, empty or unread, never dropped."""
    assert len(clp.sheets) == 34
    assert all(s.status in ("extracted", "no_callouts", "unread") for s in clp.sheets)
    assert all(s.reason for s in clp.sheets if s.status != "extracted")


def test_clp_reads_every_element_type_it_has(clp):
    kinds = {r.type_element for r in clp.records}
    assert {"colonne", "dalle", "semelle", "radier", "poutre"} <= kinds
    assert len(clp.records) > 7000


def test_clp_answer_key_values_are_found_in_the_shop_drawings(clp, corpus):
    """K-6 4-25M, I-13 10M@6", L-13 11-25M: read from the DA, blind to the plan.
    (The other three rows are explained in DA_PLAN.md: no CLP wall DA in the corpus;
    S-603 is an aggregate; S-050's DA has no 10.8 line and no 25M@11".)"""
    path = os.path.join(corpus, "CLP", "CLP_dismatch.xlsx")
    if not os.path.isfile(path):
        pytest.skip("answer key not available")
    found = {(v.row.feuillet, v.row.localisation) for v in
             answer_key.check_atelier(clp.records, answer_key.load(path), clp.unit_system)
             if v.found}
    assert {("S-502", "K-6"), ("S-504", "I-13"), ("S-100", "L-13")} <= found


def test_clp_plan_view_records_are_placed_on_the_grid(clp):
    pv = [r for r in clp.records if r.type_element in ("dalle", "semelle", "radier")]
    assert sum(r.element != "UNKNOWN" for r in pv) / len(pv) > 0.98


def test_ligrep_panels_carry_verticals_and_ties(ligrep):
    cols = [r for r in ligrep.records if r.type_element == "colonne"]
    assert len(cols) >= 320
    assert all(len(r.armature) >= 2 for r in cols)
    assert {r.debug.niveau for r in cols} >= {"2 @ 3", "RDC @ 2", "FONDATION @ RDC"}


def test_units_come_from_the_shop_drawings(clp, ligrep):
    assert clp.unit_system == "imperial"
    assert ligrep.unit_system == "metric"


@pytest.mark.parametrize("which", ["clp", "ligrep"])
def test_da_records_are_schema_valid_with_unique_ids(which, request, tmp_path):
    result = request.getfixturevalue(which)
    path = tmp_path / "elements_atelier.json"
    io_json.write_records(str(path), result.records)
    count, errors = io_json.validate_file(str(path))
    assert errors == [] and count == len(result.records)
    ids = [r.id for r in result.records]
    assert len(ids) == len(set(ids))
    assert all(r.source == "atelier" for r in result.records)


def test_no_designator_outside_the_closed_set(clp, ligrep):
    for r in clp.records + ligrep.records:
        for a in r.armature:
            assert a.diametre is None or a.diametre in BAR_DESIGNATORS


def test_da_reader_is_blind_to_the_plan():
    """PLAN SS5.13: the DA reader must never see the plan's per-element values. It may
    share geometry code, but it must not import or call the plan pipeline's runner, or
    touch the answer key. Checked on the code itself (imports and names), not on text,
    so a docstring that mentions run_plan is fine."""
    import ast

    src = pathlib.Path(__file__).parents[1] / "src" / "l2c" / "da"
    for f in src.glob("*.py"):
        tree = ast.parse(f.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "answer_key" not in mod, f"{f.name} imports {mod}"
                names = {a.name for a in node.names}
                assert "run_plan" not in names, f"{f.name} imports run_plan"
                if mod.endswith("answer_key") or mod == "":
                    assert not (names & {"answer_key"}), f"{f.name} imports answer_key"
            elif isinstance(node, ast.Import):
                assert not any("answer_key" in a.name for a in node.names), f.name
            elif isinstance(node, ast.Name) and node.id == "run_plan":
                raise AssertionError(f"{f.name} references run_plan")


def test_ligrep_da_is_deterministic(corpus, ligrep, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    io_json.write_records(str(a), ligrep.records)
    io_json.write_records(str(b), run_da(os.path.join(corpus, "LIGREP")).records)
    assert a.read_bytes() == b.read_bytes()
