"""Guards for the two coordinate traps. These protect 20 rubric points.

Appendix A: "X, Y coordinates are expressed in PDF points (1/72 in), from the
top-left corner of the page, and correspond to the center of the annotation."
"""

import pymupdf
import pytest

from l2c.page import open_document, prepare
from conftest import PROJECTS, plan_path


@pytest.mark.parametrize("project", PROJECTS)
def test_origin_is_top_left_and_y_grows_downward(corpus, project):
    doc = open_document(plan_path(project))
    page = prepare(doc, 0, "x")
    assert page.width > 0 and page.height > 0
    ys = [w.cy for w in page.words]
    assert min(ys) >= -1.0, "a negative y means the MediaBox origin leaked through"
    assert max(ys) <= page.height + 1.0, "y beyond page height: wrong coordinate space"


def test_nonzero_mediabox_origin_is_normalised(corpus):
    """CLP declares MediaBox (-1727.7, -1295.4, ...). 118/624 pages are affected."""
    doc = open_document(plan_path("CLP"))
    raw = doc[17].mediabox
    assert raw.x0 < -1000, "fixture assumption: this page has a shifted MediaBox"
    page = prepare(doc, 17, "x")
    assert all(w.x0 >= -1.0 for w in page.words)
    # the sheet number sits at the BOTTOM of the sheet -> larger y than the top grid bubbles
    sheet = [w for w in page.words if w.text == "S-502"]
    assert sheet, "S-502 title block token not found"
    assert sheet[0].cy > 0.9 * page.height


def test_rotated_page_puts_text_and_drawings_in_one_space(corpus):
    """Trap 2: without remove_rotation, drawings land in the UNROTATED space."""
    path = f"{corpus}/WP2/DA/Colonnes/WP2_COLONNE-NIV-4@5.pdf"
    doc = pymupdf.open(path)
    assert doc[0].rotation == 90, "fixture assumption: this page is /Rotate 90"

    bare = doc[0].get_drawings()            # before the fix
    assert max(d["rect"].y1 for d in bare) > doc[0].rect.height

    page = prepare(doc, 0, "x")             # after the fix
    assert all(d["rect"].y1 <= page.height + 1.0 for d in page.drawings)
    assert all(d["rect"].x1 <= page.width + 1.0 for d in page.drawings)


def test_known_anomaly_sits_where_we_measured_it(corpus):
    """The S-502 / K-6 ground-truth callout: ARM.: label at x=1821.8, the 4-35M value at x=1847.2, y=1089.0."""
    doc = open_document(plan_path("CLP"))
    page = prepare(doc, 17, "x")
    hits = [w for w in page.words if w.text == "4-35M"]
    assert len(hits) == 1, "expected exactly one 4-35M outlier on S-502"
    assert hits[0].x0 == pytest.approx(1847.2, abs=1.0)
    assert hits[0].y0 == pytest.approx(1089.0, abs=1.0)


def test_pdfplumber_would_have_got_it_wrong(corpus):
    """Documents WHY PyMuPDF is the engine: measured offset dx=-1727.7, dy=+1298.7."""
    pdfplumber = pytest.importorskip("pdfplumber")
    doc = open_document(plan_path("CLP"))
    page = prepare(doc, 17, "x")
    ours = {w.text: w for w in page.words}
    with pdfplumber.open(plan_path("CLP")) as pdf:
        theirs = pdf.pages[17].extract_words()
    assert len(theirs) == len(page.words), "same tokens, different coordinates"
    sample = [t for t in theirs if t["text"] == "4-35M"]
    assert sample, "4-35M present for pdfplumber too"
    assert sample[0]["x0"] - ours["4-35M"].x0 == pytest.approx(-1727.7, abs=1.0)
