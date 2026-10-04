"""CLP radiers: small per-view grids, the spec grammar, and a real ring-anchored read."""

import os

import pymupdf
import pytest

from l2c.da.imageread import PageImage, TextLine
from l2c.da.parsers.dalle_clp import Bubble
from l2c.da.parsers.radier_clp import (
    PageResult, Spec, View, bar_for, build_views, clean_output, parse_spec, read_anchor,
    read_rang, records, summary,
)


def _strip(y, labels):
    return [Bubble(x, y, 22, label, 0.99) for label, x in labels]


def test_small_views_side_by_side_keep_their_own_letters_and_skip_elevations():
    bubbles = []
    # Two plan views sharing a bubble height: columns 7.3/7, and 13/12.7/12/11.
    bubbles += _strip(40, [("7.3", 380), ("7", 460), ("13", 1790), ("12.7", 1870), ("12", 1990), ("11", 2040)])
    bubbles += _strip(700, [("7.3", 380), ("7", 460)])
    # The right view's bottom strip adds an axis the top one lacks.
    bubbles += _strip(520, [("14", 1750), ("13", 1790), ("12.7", 1870), ("12", 1990), ("11", 2040)])
    # Row letters: one side only for I and E; the right view's own letters sit between.
    bubbles += [Bubble(250, 160, 22, "1", 0.6), Bubble(250, 280, 22, "H", 0.99),
                Bubble(690, 280, 22, "H", 0.99), Bubble(250, 650, 22, "E", 0.99),
                Bubble(1630, 110, 22, "H", 0.99), Bubble(2160, 110, 22, "H", 0.99),
                Bubble(1630, 240, 22, "G", 0.99)]
    # An elevation: one strip, no twin below it.
    bubbles += _strip(900, [("12", 490), ("12.7", 610), ("13", 700)])
    left, right = build_views(bubbles)
    assert sorted(left.numbers, key=float) == ["7", "7.3"]
    assert set(left.letters) == {"I", "H", "E"}         # a lone stroke beside a view is the row I
    assert set(right.letters) == {"H", "G"}
    assert right.numbers["12.7"] == 1870 and "14" not in right.numbers
    assert left.locate(390, 270) == "H-7.3"
    assert right.locate(1985, 250) == "G-12"


def test_a_numeric_strip_reads_a_lone_stroke_as_the_column_one():
    bubbles = _strip(100, [("3", 100), ("2", 170), ("|", 200)]) + _strip(400, [("3", 100), ("2", 170), ("1", 200)])
    bubbles += [Bubble(40, 250, 22, "I", 0.9), Bubble(330, 250, 22, "I", 0.9)]
    view, = build_views(bubbles)
    assert set(view.numbers) == {"1", "2", "3"} and set(view.letters) == {"I"}


def test_spec_grammar_keeps_sets_lengths_and_the_fabricators_own_inconsistencies():
    spec = parse_spec('LONG: 2x22 25M 25RL22-00 @8"HAUT')
    assert (spec["label"], spec["sets"], spec["quantite"], spec["diametre"]) == ("LONG", 2, 44, "25M")
    assert (spec["repere"], spec["espacement_mm"], spec["face"], spec["issues"]) == ("25RL22-00", 203.2, "HAUT", [])
    length = parse_spec('LONG: 9 35M 42-09 @8"BAS')
    assert length["repere"] is None and length["longueur_mm"] == round((42 * 12 + 9) * 25.4, 1)
    short = parse_spec('L: 5 25RU8-04 @8"HAUT')
    assert (short["label"], short["quantite"], short["diametre"], short["repaired"]) == ("L", 5, "25M", False)
    # Drawn as 30M with a 35 mark: reported as read, with a note, never corrected.
    odd = parse_spec('LONG: 2x7 30M 35RU22-07 @11"BAS trailing junk')
    assert odd["diametre"] == "30M" and odd["repere"] == "35RU22-07" and odd["issues"] == []
    assert odd["notes"] == ["mark 35RU22-07 carries a different size than 30M"]


def test_bounded_ocr_repairs_are_flagged_and_broken_marks_never_pass():
    for text in ('LONG: 24 35M 42-09 07"BAS', 'LONG: 11 25M 25RU12-09 a10"BAS', "TRAN: 16 30M 30RL17-00 @8.\"BAS",
                 'LONG: 44 35M 35RL40-00 @7"BA5', 'L0NG: 9 30M 26-03 @8"HAUT',
                 'TRAN: 44 25 25RU24-10 @8"BAS', 'LONG: 20 25M 255U16-06 @9"BAS'):
        spec = parse_spec(text)
        assert spec and spec["issues"] == [] and spec["repaired"], text
    glued = parse_spec('TRAN: 20 30M 30RL23-09@8"HAUT')           # a lost space changes nothing
    assert glued["espacement_mm"] == 203.2 and glued["issues"] == [] and not glued["repaired"]
    assert parse_spec('LONG: 24 35M 42-09 07"BAS')["espacement_mm"] == 177.8
    assert parse_spec('LONG: 20 25M 255U16-06 @9"BAS')["repere"] == "25SU16-06"
    for text in ('TRAN: 20 25M 25RL16 06 @8"BAS', 'LONG: 10 25M 25RU21-0.0 @8"HAUT'):
        spec = parse_spec(text)
        assert spec["repere"] is None and spec["longueur_mm"] is None and spec["issues"], text
    assert parse_spec('TRAN: 12 25M 25RU19-09')["issues"] == ["spacing unread", "face (HAUT/BAS) unread"]
    assert parse_spec("COLONNE J.5-12, 10X30:") is None and parse_spec("ATT: 22 15M 7-00 @8\"BAS") is None


def test_bar_is_the_stroke_on_the_baseline_side_of_its_spec():
    strokes = [(False, 277.0, 310.0, 870.0, 1.3), (False, 261.0, 310.0, 870.0, 1.3),
               (True, 416.0, 120.0, 400.0, 1.3), (True, 399.0, 120.0, 400.0, 1.3)]
    assert bar_for(TextLine(501, 269, 605, 275, "", 1.0), strokes)[1] == 277.0
    assert bar_for(TextLine(408, 161, 414, 259, "", 1.0, True), strokes)[1] == 416.0
    assert bar_for(TextLine(900, 269, 990, 275, "", 1.0), strokes) is None


def test_outputs_mirror_the_plan_radier_record_and_omit_unknown_fields():
    spec = Spec(1, "view-1", "RADIER #1", "J-13", "vertical", 'TRAN: 24 30M 30RU19-09 @11"BAS',
                (408, 161, 414, 259), label="TRAN", rang=2, rang_source="circle", face="BAS",
                quantite=24, diametre="30M", espacement_mm=279.4, repere="30RU19-09",
                span=["J.5", "J"], status="read", confidence=0.97)
    result = PageResult("CLP_RADIERS.pdf", 1, [View("view-1", (0, 0, 1, 1), {"J": 0}, {"13": 0})], [spec], [], [], 1.0)
    assert summary(spec) == 'RANG 2: 30M@11"'
    row, = clean_output([result])
    assert row["coordinate"] == "J-13" and row["rang"] == 2 and row["summary"] == 'RANG 2: 30M@11"'
    assert "issues" not in row and "full_check" not in row and "longueur_mm" not in row["reinforcement"][0]
    record, = records(result, "CLP_RADIERS.pdf")
    assert record.type_element == "radier" and record.source == "atelier" and record.element == "J-13"
    assert record.armature[0].model_dump() == {"repere": "30RU19-09", "diametre": "30M", "quantite": 24,
                                               "espacement_mm": 279.4, "longueur_mm": None}
    assert record.debug.layer == "2" and record.debug.direction == "vertical"


def test_real_spec_is_read_from_its_ring_using_pixels_only(corpus, monkeypatch):
    path = os.path.join(corpus, "CLP", "DA", "Fondations", "CLP_RADIERS.pdf")
    if not os.path.exists(path):
        pytest.skip("CLP radier DA not available")
    with pymupdf.open(path) as doc:
        page = doc[0]
        src = PageImage(page)
        monkeypatch.setattr(pymupdf.Page, "get_text", lambda *a, **k: pytest.fail("text layer used"))
        ring = (410.5, 265.8, 7.5)                      # radier #1, the bar left of axis 13
        line, fields, _ = read_anchor(src, ring, vertical=True)
        assert (fields["label"], fields["quantite"], fields["diametre"]) == ("TRAN", 24, "30M")
        assert (fields["repere"], fields["espacement_mm"], fields["face"]) == ("30RU19-09", 279.4, "BAS")
        assert line.vertical and read_rang(src, ring, True)[0] == 2
