"""CLP slabs: independent detail grids, support ownership, and a real OCR read."""

import os

import cv2
import numpy as np
import pymupdf

from l2c.da.imageread import TextLine
from l2c.da.parsers.dalle_clp import (
    Bubble, PageResult, Support, associate, build_views, grey_rectangles, main,
    outlined_supports, parse_lines, parse_page, records, crop_for, write_csv, clean_output,
)


def test_detail_view_does_not_replace_main_grid_and_ambiguous_letters_use_axis_role():
    bubbles = []
    for x in (20, 500):
        for label, y in (("H", 100), ("G", 180), ("F", 260), ("1", 340), ("0", 420)):
            bubbles.append(Bubble(x, y, 20, label, 0.99))
    for y in (60, 450):
        for label, x in (("13", 100), ("12.7", 180), ("12", 260)):
            bubbles.append(Bubble(x, y, 20, label, 0.99))
    for x in (700, 1100):
        for label, y in (("H", 200), ("G", 280), ("F", 360)):
            bubbles.append(Bubble(x, y, 20, label, 0.99))
    for y in (160, 400):
        for label, x in (("13", 780), ("12.7", 860), ("12", 940)):
            bubbles.append(Bubble(x, y, 20, label, 0.99))
    main, detail = build_views(bubbles)
    assert main.letters["I"] == 340 and main.letters["O"] == 420
    assert main.numbers["12.7"] == 180
    assert detail.numbers["12.7"] == 860
    assert main.locate(pymupdf.Rect(175, 175, 185, 185))[0] == "G-12.7"
    assert detail.locate(pymupdf.Rect(855, 275, 865, 285))[0] == "G-12.7"
    assert main.locate(pymupdf.Rect(855, 275, 865, 285)) is None


def test_crop_cannot_assign_a_neighbours_text_or_an_equal_distance_callout():
    left = Support(1, "main", "J-15", (90, 90, 110, 110))
    right = Support(1, "main", "J-16", (150, 90, 170, 110))
    lines = [TextLine(90, 60, 110, 70, "NUM. 3 15M 11-03", 0.99),
             TextLine(150, 60, 170, 70, "NUM. 8 25M 11-03", 0.99),
             TextLine(125, 60, 135, 70, "3 15M 11-03", 0.99)]
    assert associate(lines, left, [left, right]) == lines[:1]
    assert associate(lines, right, [left, right]) == lines[1:2]


def test_tall_wall_crop_uses_grid_intersection_and_keeps_text_above_close_rows():
    from l2c.da.parsers.dalle_clp import View
    view = View("main", (0, 0, 1000, 1000), {"I": 869, "H": 933}, {"12": 750})
    crop = crop_for(pymupdf.Rect(720, 865, 753, 933), view, (750, 869))
    assert crop.contains(pymupdf.Rect(754, 840, 835, 864))


def test_offset_callout_prefers_visible_support_over_nearby_fractional_axis():
    visible = Support(3, "main", "K-12", (720, 551, 753, 618), anchor=(750, 615))
    unmarked = Support(3, "main", "K-10.8", (791, 613, 795, 617),
                       detection="grid_intersection", anchor=(793, 615))
    line = TextLine(753, 595, 826, 604, "NUM. 2 15M 11-03", 0.99)
    assert associate([line], visible, [visible, unmarked]) == [line]
    assert associate([line], unmarked, [visible, unmarked]) == []


def test_slab_label_spacing_repairs_preserve_count_and_direction():
    lines = [TextLine(10, 10, 100, 20, "NUM.2 15M 11-03", 0.95),
             TextLine(10, 30, 100, 40, "-ALP:2 15M 15J4-09", 0.95),
             TextLine(10, 50, 100, 60, "NUM: 3. 15M 11-030", 0.95)]
    bars = parse_lines(lines)
    assert [(b["label"], b["formatted"]) for b in bars] == [
        ("NUM", "2-15M"), ("ALP", "2-15M"), ("NUM", "3-15M")]
    assert bars[2]["longueur_mm"] is None and bars[2]["issue"] is not None


def test_whole_grid_ocr_boxes_can_be_saved_as_json(monkeypatch):
    import json
    from dataclasses import asdict
    from types import SimpleNamespace
    import l2c.da.parsers.dalle_clp as parser
    from l2c.da.imageread import PageImage
    # ONNX detection returns NumPy scalars; the diagnostics must contain ordinary
    # Python values even when the engine's polygons use those scalar types.
    polygon = np.array([[[10, 10], [200, 10], [200, 30], [10, 30]]], dtype=np.float32)
    monkeypatch.setattr(parser.ocr, "_ocr", lambda: lambda *a, **k: SimpleNamespace(boxes=polygon))
    def recognise(src, line):
        line.text, line.confidence = "NUM. 3 15M 11-03", 0.99
        return line
    monkeypatch.setattr(parser, "recognise_line", recognise)
    with pymupdf.open() as doc:
        page = doc.new_page(width=100, height=100)
        page.insert_text((10, 15), "callout")
        lines = parser.scan_grid(PageImage(page), parser.View("main", (0, 0, 100, 100),
                                                            {"J": 50}, {"15": 50}))
    assert len(lines) == 1
    assert json.loads(json.dumps(asdict(lines[0])))["vertical"] is False
    assert parse_lines(lines)[0]["formatted"] == "3-15M"


def test_final_csv_keeps_directions_unknown_values_and_unread_supports(tmp_path):
    import csv
    support = Support(3, "main", "J-15", (10, 10, 20, 20), status="partial")
    support.bars = parse_lines([TextLine(10, 10, 100, 20, "NUM. 22 15M 11-038", 0.95)])
    unread = Support(3, "main", "K-15", (10, 30, 20, 40), reason="no line read")
    path = tmp_path / "final.csv"
    write_csv(str(path), [PageResult("drawing.pdf", 3, [], [support, unread], [], 0)])
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["reinforcement"] == "22-15M" and rows[0]["role"] == "NUM"
    assert rows[0]["longueur_mm"] == "" and rows[0]["status"] == "partial"
    assert rows[1]["coordinate"] == "K-15" and rows[1]["status"] == "unread"


def test_final_json_removes_empty_and_unlocated_rows_but_keeps_partial_values():
    support = Support(3, "main", "J-15", (10, 10, 20, 20), status="partial",
                      check_equal=False, confidence=0.0)
    support.bars = parse_lines([TextLine(10, 10, 100, 20, "NUM. 22 15M 11-038", 0.95)])
    unread = Support(3, "main", "K-15", (10, 30, 20, 40))
    unlocated = Support(3, None, None, (10, 50, 20, 60), bars=support.bars)
    invalid = Support(3, "main", "L-15", (10, 70, 20, 80),
                      bars=[{"quantite": None, "diametre": "15M"}])
    output = clean_output([PageResult("drawing.pdf", 3, [], [support, unread, unlocated, invalid], [], 0)])
    assert len(output) == 1
    row = output[0]
    assert row["coordinate"] == "J-15" and row["status"] == "partial"
    assert row["confidence"] == 0.0 and row["full_check"] is False
    bar = row["reinforcement"][0]
    assert bar["formatted"] == "22-15M" and bar["role"] == "NUM"
    assert "longueur_mm" not in bar and "layer" not in row
    assert all(v is not None for v in row.values())
    assert all(v is not None for v in bar.values())


def test_grey_support_interrupted_by_grid_lines_is_detected_without_black_text():
    gray = np.full((400, 400), 255, dtype=np.uint8)
    cv2.rectangle(gray, (100, 100), (130, 124), 145, -1)
    cv2.line(gray, (115, 70), (115, 155), 20, 2)
    cv2.line(gray, (70, 112), (155, 112), 20, 2)
    cv2.putText(gray, "12345", (190, 120), cv2.FONT_HERSHEY_SIMPLEX, 0.5, 20, 1, cv2.LINE_AA)
    rects = grey_rectangles(gray)
    assert len(rects) == 1
    assert abs((rects[0].x0 + rects[0].x1) / 2 - 57.5) < 2


def test_outlined_column_in_grey_background_requires_four_edges():
    from l2c.da.parsers.dalle_clp import View

    gray = np.full((240, 240), 190, dtype=np.uint8)
    cv2.line(gray, (0, 100), (239, 100), 20, 1)
    cv2.line(gray, (100, 0), (100, 239), 20, 1)
    view = View("main", (0, 0, 120, 120), {"J": 50}, {"15": 50})
    assert outlined_supports(gray, [view], []) == []
    cv2.rectangle(gray, (92, 88), (108, 112), 20, 1)
    found = outlined_supports(gray, [view], [])
    assert len(found) == 1
    assert view.locate(found[0])[0] == "J-15"


def test_real_clp_j15_uses_pixels_and_preserves_both_directions(corpus, monkeypatch):
    path = os.path.join(corpus, "CLP/DA/Dalles/CLP_DALLE NIV 3.pdf")
    def forbidden(*args, **kwargs):
        raise AssertionError("image parser must not extract PDF text or vector paths")
    monkeypatch.setattr(pymupdf.Page, "get_text", forbidden)
    monkeypatch.setattr(pymupdf.Page, "get_drawings", forbidden)
    monkeypatch.setattr(pymupdf.Page, "get_texttrace", forbidden)
    with pymupdf.open(path) as doc:
        result = parse_page(doc[2], os.path.basename(path), coordinates=["J-15"])
    assert len(result.views) == 2, "main view and inset must have separate axes"
    assert result.views[0].letters.get("I") is not None
    support = next(s for s in result.supports if s.coordinate == "J-15")
    assert support.layer == "intégrité" and support.level == "NIVEAU 3"
    assert {(b["label"], b["quantite"], b["diametre"], b["longueur_mm"]) for b in support.bars} == {
        ("NUM", 3, "15M", 3429.0), ("ALP", 3, "15M", 3429.0),
    }
    assert support.oracle is None
    assert support.summary == "NUM: 3-15M · ALP: 3-15M"
    record = records(result, os.path.basename(path))[0]
    assert record.debug.roles == ["NUM", "ALP"]
    assert record.debug.symbol_bbox is not None
    assert record.x > 490 and 680 < record.y < 720
    assert "debug" not in record.to_schema()


def test_truncated_length_stays_unknown_instead_of_becoming_a_fabrication_mark():
    line = TextLine(10, 10, 100, 20, "NUM. 3 15M 11-038", 0.95)
    bar = parse_lines([line])[0]
    assert bar["quantite"] == 3 and bar["diametre"] == "15M"
    assert bar["formatted"] == "3-15M"
    assert bar["repere"] is None and bar["longueur_mm"] is None
    assert bar["issue"] is not None
    bad_inches = parse_lines([TextLine(10, 10, 100, 20, "NUM. 3 15M 11-99", 0.99)])[0]
    assert bad_inches["longueur_mm"] is None and bad_inches["issue"] is not None


def test_folder_cli_reads_only_last_pages_and_annotates_only_those(tmp_path, monkeypatch):
    import json
    import l2c.da.parsers.dalle_clp as parser

    folder = tmp_path / "Dalles"
    folder.mkdir()
    for name, count in (("a.pdf", 2), ("b.pdf", 4)):
        with pymupdf.open() as doc:
            for _ in range(count):
                doc.new_page()
            doc.save(folder / name)
    called = []
    def fake_parse(page, filename, *args):
        called.append((filename, page.number + 1))
        return PageResult(filename, page.number + 1, [], [], [], 0.0)
    monkeypatch.setattr(parser, "parse_page", fake_parse)
    out = tmp_path / "records.json"
    diag = tmp_path / "diagnostics.json"
    reviews = tmp_path / "reviews"
    assert main([str(folder), "--json", str(out), "--diagnostics", str(diag),
                 "--summary-json", str(tmp_path / "summaries.json"),
                 "--output-json", str(tmp_path / "final.json"),
                 "--csv", str(tmp_path / "output.csv"),
                 "--annotated", str(reviews)]) == 0
    assert called == [("a.pdf", 2), ("b.pdf", 4)]
    assert json.loads(out.read_text()) == []
    assert json.loads((tmp_path / "final.json").read_text()) == []
    assert [r["page"] for r in json.loads(diag.read_text())] == [2, 4]
    for review in reviews.glob("*.pdf"):
        with pymupdf.open(review) as doc:
            assert len(doc) == 1
