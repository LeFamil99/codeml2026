"""DA pages read AS IMAGES - test-first, one real page at a time.

The reader (``l2c.da.imageread``) receives pixels only: a page renderer, never the PDF's
text or vectors. What SHOULD be found comes from the page's invisible text layer
(render mode 3, present on CLP and LIGREP DA) - used here as the oracle, and only here.
"""

from __future__ import annotations

import ast
import os
import pathlib
import re

import pymupdf
import pytest

from conftest import CORPUS
from l2c.da.common import parse_bar_line

PAGES = {
    # name: (relative path, page index, unit system)
    "clp_columns_k": ("CLP/DA/Colonnes/CLP_COLONNES Partie 3.pdf", 3, "imperial"),
}


def _oracle_lines(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = re.sub(r"\s+", " ", " ".join(s["text"] for s in l["spans"])).strip()
            if t:
                out.append((pymupdf.Rect(l["bbox"]), t))
    return out


@pytest.fixture(scope="module")
def clp_k(corpus):
    from l2c.da import imageread

    rel, idx, system = PAGES["clp_columns_k"]
    doc = pymupdf.open(os.path.join(corpus, rel))
    page = doc[idx]
    if page.rotation:
        page.remove_rotation()
    truth = _oracle_lines(page)
    read = imageread.read(imageread.PageImage(page))
    return truth, read, system, page


def _found(truth_rect, read, pad=3.0):
    """Read lines whose centre falls inside the oracle line's box (padded)."""
    r = pymupdf.Rect(truth_rect.x0 - pad, truth_rect.y0 - pad, truth_rect.x1 + pad, truth_rect.y1 + pad)
    return [l for l in read if r.contains(pymupdf.Point(l.cx, l.cy))]


# ------------------------------------------------------------------- the contract
def test_reader_never_touches_the_pdf_text_or_vectors():
    """Image-only: the reader module may render pixels, nothing else."""
    src = (pathlib.Path(__file__).parents[1] / "src" / "l2c" / "da" / "imageread.py").read_text()
    tree = ast.parse(src)
    forbidden = {"get_text", "get_drawings", "get_cdrawings", "get_texttrace", "read_contents",
                 "get_fonts", "get_text_words", "search_for"}
    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not (used & forbidden), used & forbidden


# ------------------------------------------------------------------- page 1: CLP columns
def test_every_bar_line_is_read_with_identical_values(clp_k):
    """Every VERT / ÉTRI / GOUJ line on the page: same quantity, size, mark, spacing as
    the oracle (marks compared without accents)."""
    truth, read, system, _ = clp_k
    import unicodedata

    fold = lambda t: "".join(c for c in unicodedata.normalize("NFD", t or "")
                             if unicodedata.category(c) != "Mn")
    key = lambda a: (a.quantite, a.diametre, fold(a.repere), a.espacement_mm, a.longueur_mm)
    bars = [(r, t, parse_bar_line(t, system)) for r, t in truth]
    bars = [(r, t, b) for r, t, b in bars if b is not None]
    assert len(bars) > 50, "fixture assumption: this page is a column schedule"
    ok, missed = 0, []
    for r, t, b in bars:
        hits = [parse_bar_line(l.text, system) for l in _found(r, read)]
        if any(h is not None and key(h.armature) == key(b.armature) for h in hits):
            ok += 1
        elif len(missed) < 10:
            missed.append((t, [l.text for l in _found(r, read)]))
    assert ok / len(bars) >= 0.95, f"{ok}/{len(bars)} bar lines; e.g. {missed}"


def test_every_grid_label_is_found_at_its_place(clp_k):
    """The column labels (K-6 ...) are the locators of this page."""
    truth, read, _, _ = clp_k
    labels = [(r, t) for r, t in truth if re.fullmatch(r"[A-Z]{1,2}-\d{1,2}(\.\d)?", t)]
    assert len(labels) >= 20
    missing = [t for r, t in labels if not any(l.text.replace(" ", "") == t for l in _found(r, read))]
    assert not missing, missing


def test_storey_labels_are_found(clp_k):
    truth, read, _, _ = clp_k
    levels = [(r, t) for r, t in truth if re.match(r"^(NIVEAU \d+|REZ-DE-CHAUSS|SOUS-SOL|TOIT)", t)]
    assert levels
    missing = [t for r, t in levels if not any(_norm(l.text) == _norm(t) for l in _found(r, read))]
    assert len(missing) <= 1, missing


def _norm(t: str) -> str:
    import unicodedata

    t = "".join(c for c in unicodedata.normalize("NFD", t) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", "", t.upper())


def test_column_records_from_the_image_match_the_answer_key(clp_k):
    """End to end on pixels: the column parser, fed the image-read lines, yields K-6 /
    RDC @ 2 with verticals 4-25M (answer-key DA value for S-502 K-6)."""
    from l2c.da import columns
    from l2c.da.imageread import as_prepared
    from l2c.da.inventory import DAFile

    truth, read, system, page = clp_k
    f = DAFile(path="", folder="Colonnes", name="CLP_COLONNES Partie 3.pdf",
               type_element="colonne", tiers=[1])
    recs, _ = columns.extract(as_prepared(page, read), system, f)
    k6 = [r for r in recs if r.element == "K-6" and r.debug.niveau == "RDC @ 2"]
    assert k6, sorted({(r.element, r.debug.niveau) for r in recs})[:20]
    assert any(a.quantite == 4 and a.diametre == "25M" for a in k6[0].armature)
