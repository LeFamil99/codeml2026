"""Sheet identity and classification across the whole plan set.

``feuillet`` is a graded key, and the parser a sheet goes to depends on its type, so a
wrong sheet number or a wrong type silently corrupts everything downstream.
"""

from __future__ import annotations

import collections
import re

import pytest

from conftest import PROJECTS, plan_path
from l2c.page import open_document, prepare


@pytest.fixture(scope="module")
def sheets(corpus):
    out = {}
    for project in PROJECTS:
        doc = open_document(plan_path(project))
        out[project] = [prepare(doc, i, "x") for i in range(len(doc))]
    return out


@pytest.mark.parametrize("project", PROJECTS)
def test_every_page_has_a_unique_sheet_number(sheets, project):
    """The largest sheet-id token wins; the old last-token rule picked cross-references
    ("VOIR S-201") and dropped suffixed numbers (S-600A, S-103.a) on ~10 pages."""
    ids = [p.sheet_id for p in sheets[project]]
    assert "UNKNOWN" not in ids
    dup = [k for k, v in collections.Counter(ids).items() if v > 1]
    assert not dup, dup


def test_suffixed_sheet_numbers_are_kept(sheets):
    assert {"S-600A", "S-600B"} <= {p.sheet_id for p in sheets["CLP"]}
    assert {"S-103.a", "S-103.b"} <= {p.sheet_id for p in sheets["EspCa3B"]}


SERIES = [(r"^S-05\d|^S-06\d", "radier"), (r"^S-100$", "semelle"), (r"^S-3\d\d", "poutre"),
          (r"^S-4\d\d", "mur_refend"), (r"^S-5\d\d", "colonne"), (r"^S-6\d\d", "dalle")]


@pytest.mark.parametrize("project", PROJECTS)
def test_type_follows_the_sheet_series(sheets, project):
    """The title-phrase classifier must agree with the numbering in every project.
    (The old keyword scan called WP2's floor plans "colonne" from their ℄ COL. markers.)"""
    for p in sheets[project]:
        for rx, kind in SERIES:
            if re.match(rx, p.sheet_id):
                assert p.type_element == kind, f"{p.sheet_id}: {p.type_element} != {kind}"
        if re.match(r"^S-0[0-4]\d|^S-1(0[1-9]|[1-9]\d)", p.sheet_id):
            assert p.type_element is None, f"{p.sheet_id} should be skipped, got {p.type_element}"


def test_multi_view_sheet_locates_against_its_own_view(sheets):
    """CLP S-050 repeats the grid in five enlarged views; the line grid keeps each
    line's extent, so the answer-key callout resolves to J-10.8 (the label-only grid
    saw letters E-H only)."""
    from l2c.geometry.gridlines import extract_lines

    page = next(p for p in sheets["CLP"] if p.sheet_id == "S-050")
    grid = extract_lines(page)
    assert grid.locate(894, 551)[0] == "J-10.8"
