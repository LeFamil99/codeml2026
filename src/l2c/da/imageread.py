"""DA pages read AS IMAGES: OCR detection + recognition on rendered pixels.

The reader sees pixels only - ``PageImage.render`` is its one door to the page - so it
works the same whether a fabricator's PDF holds a text layer, outlined glyphs, stroke
fonts or a scan. (Reading text or vectors out of the PDF was abandoned: it depended on
how each company's CAD encodes text.) A test enforces that this module calls nothing
that extracts text or vectors.

Two passes, sized for a laptop CPU:
1. DETECT - the page rendered in overlapping tiles at ``DET_ZOOM`` (CLP's 3.3-pt text
   becomes ~16 px), PP-OCR text DETECTION only; blank tiles skipped. A line crossing a
   tile edge is cut in both tiles, so fragments on one row (or one column, for vertical
   text) are joined back into one box - measured: without it 20 % of CLP's bar lines
   came back truncated (``ÉTRI: 1 10M 10E``).
3. LOOK AGAIN - where drafting convention says a label must be (the end of a long
   storey rule) and nothing was read, detect again on a small crop (``_look_again``).
2. READ - each joined box rendered again so its text is ~``REC_PX`` tall, long straight
   rules erased (``remove_rules``), and read by the recogniser; the surer of the reads
   with and without rule removal is kept. A tall narrow box is vertical text: both
   quarter turns are tried.
Blind to the plan.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field

import numpy as np
import pymupdf

DET_ZOOM = 5.0
TILE = 1280
OVERLAP = 192
REC_PX = 40.0
MIN_CONF = 0.5         # a read below this is noise (hatching, symbols), not text

MODEL_INFO = {"detector": "PP-OCRv6 det small (ONNX, via RapidOCR)",
              "recogniser": "PP-OCRv6 rec small (ONNX, via RapidOCR)", "licence": "Apache-2.0"}


class PageImage:
    """Pixels of one page, rendered on demand. Coordinates are PDF points, top-left."""

    def __init__(self, page: pymupdf.Page):
        self._dl = page.get_displaylist()
        self.width, self.height = page.rect.width, page.rect.height

    def render(self, clip: pymupdf.Rect, zoom: float) -> np.ndarray:
        pix = self._dl.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip,
                                  colorspace=pymupdf.csRGB, alpha=False)
        return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)


@dataclass
class TextLine:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    confidence: float
    vertical: bool = False
    words: list = field(default_factory=list)      # (x0, y0, x1, y1, text), proportional

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def rect(self) -> pymupdf.Rect:
        return pymupdf.Rect(self.x0, self.y0, self.x1, self.y1)


@functools.lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR

    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    # lower detection thresholds than the defaults (0.3 / 0.5): labels sitting ON a storey
    # line score low in a dense tile and were dropped (CLP: NIVEAU 2, REZ-DE-CHAUSSÉE,
    # L-16); the extra junk boxes cost a read each and are filtered by confidence
    return RapidOCR(params={"Global.log_level": "warning", "Global.text_score": 0.3,
                            "Det.thresh": 0.2, "Det.box_thresh": 0.3})


# ----------------------------------------------------------------- pass 1: detect
def _tiles(width: float, height: float):
    step = (TILE - OVERLAP) / DET_ZOOM
    size = TILE / DET_ZOOM
    y = 0.0
    while y < height:
        x = 0.0
        while x < width:
            yield pymupdf.Rect(x, y, min(width, x + size), min(height, y + size))
            x += step
        y += step


def _detect(src: PageImage) -> list[TextLine]:
    found: list[TextLine] = []
    for clip in _tiles(src.width, src.height):
        img = src.render(clip, DET_ZOOM)
        if img.min() > 200:                       # nothing drawn here
            continue
        r = _engine()(img, use_det=True, use_cls=False, use_rec=False)
        boxes = getattr(r, "boxes", None)
        if boxes is None:
            continue
        for poly in boxes:
            xs = [clip.x0 + p[0] / DET_ZOOM for p in poly]
            ys = [clip.y0 + p[1] / DET_ZOOM for p in poly]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            vertical = (y1 - y0) > 1.5 * (x1 - x0)
            found.append(TextLine(x0, y0, x1, y1, "", 0.0, vertical=vertical))
    return _join(found)


def _join(boxes: list[TextLine]) -> list[TextLine]:
    """Union boxes that are pieces of one line: same orientation, same band (>= 60 %
    overlap across the line), touching or overlapping along it. Distinct items on a row
    are separated by far more than a text height, so they stay apart."""
    def along(b):
        return (b.y0, b.y1) if b.vertical else (b.x0, b.x1)

    def across(b):
        return (b.x0, b.x1) if b.vertical else (b.y0, b.y1)

    changed = True
    boxes = list(boxes)
    while changed:
        changed = False
        boxes.sort(key=lambda b: (b.vertical, along(b)[0]))
        out: list[TextLine] = []
        for b in boxes:
            merged = False
            for k in range(len(out) - 1, max(-1, len(out) - 40), -1):
                a = out[k]
                if a.vertical != b.vertical:
                    continue
                (a0, a1), (b0, b1) = across(a), across(b)
                band = min(a1, b1) - max(a0, b0)
                if band < 0.6 * min(a1 - a0, b1 - b0):
                    continue
                (s0, s1), (t0, t1) = along(a), along(b)
                if t0 - s1 > 0.3 * min(a1 - a0, b1 - b0) or s0 - t1 > 0.3 * min(a1 - a0, b1 - b0):
                    continue
                out[k] = TextLine(min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1),
                                  "", 0.0, a.vertical)
                merged = changed = True
                break
            if not merged:
                out.append(b)
        boxes = out
    return boxes


# ----------------------------------------------------------------- pass 2: re-read
def remove_rules(img: np.ndarray, text_px: float) -> np.ndarray:
    """Erase long straight strokes (storey lines, grid lines, table rules) from a crop.

    Labels often sit ON a line (CLP's ``NIVEAU 2`` on its storey line) and the reader
    then fails. Morphological opening with a kernel much longer than the text is tall
    keeps only strokes that long - text strokes are not - and those pixels are whitened.
    Classic vision, no assumption about fonts or fabricators."""
    import cv2

    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    ink = (gray < 160).astype(np.uint8)
    L = max(15, int(2.5 * text_px))
    h = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
    v = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, L)))
    rules = cv2.dilate(h | v, np.ones((3, 3), np.uint8))
    if not rules.any():
        return img
    out = img.copy()
    out[rules.astype(bool)] = 255
    return out


def _rec(img: np.ndarray) -> tuple[str, float]:
    try:
        r = _engine()(img, use_det=False, use_cls=False, use_rec=True)
    except Exception:
        return "", 0.0
    t, s = getattr(r, "txts", None), getattr(r, "scores", None)
    return (str(t[0]).strip(), float(s[0])) if t else ("", 0.0)


def _reread(src: PageImage, l: TextLine) -> TextLine:
    thick = (l.x1 - l.x0) if l.vertical else (l.y1 - l.y0)    # the det box: ~1.6x text height
    if thick <= 0:
        return l
    z = float(min(24.0, max(DET_ZOOM, REC_PX * 1.6 / thick)))
    pad = 0.25 * thick
    clip = pymupdf.Rect(l.x0 - pad, l.y0 - pad, l.x1 + pad, l.y1 + pad) & pymupdf.Rect(0, 0, src.width, src.height)
    img = src.render(clip, z)
    raw = img
    img = remove_rules(img, thick * z / 1.6)
    cands = [img, raw] if img is not raw else [img]       # with and without rule removal
    if l.vertical:
        reads = [_rec(np.ascontiguousarray(np.rot90(c, k))) for c in cands for k in (-1, 1)]
    else:
        reads = [_rec(c) for c in cands]
    text, conf = max(reads, key=lambda t: t[1])
    return TextLine(l.x0, l.y0, l.x1, l.y1, text, conf, l.vertical)


def _words(l: TextLine) -> list:
    t, out, pos = l.text, [], 0
    n = max(1, len(t))
    for w in t.split():
        a = t.index(w, pos)
        pos = a + len(w)
        if l.vertical:            # bottom-to-top
            out.append((l.x0, l.y1 - (l.y1 - l.y0) * pos / n, l.x1, l.y1 - (l.y1 - l.y0) * a / n, w))
        else:
            out.append((l.x0 + (l.x1 - l.x0) * a / n, l.y0, l.x0 + (l.x1 - l.x0) * pos / n, l.y1, w))
    return out


# ----------------------------------------------------------------- pass 3: look again
def long_rules(src: PageImage, min_len_pt: float = 250.0, zoom: float = 1.5):
    """Long horizontal rules (storey lines, table rules) on a cheap low-res render:
    morphological opening keeps only horizontal strokes >= ``min_len_pt``."""
    import cv2

    img = src.render(pymupdf.Rect(0, 0, src.width, src.height), zoom)
    ink = (cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) < 160).astype(np.uint8)
    L = int(min_len_pt * zoom)
    h = cv2.morphologyEx(ink, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (L, 1)))
    n, _, stats, _ = cv2.connectedComponentsWithStats(h, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, hh, _ = stats[i]
        if hh <= 6:
            out.append((x / zoom, (y + hh / 2) / zoom, (x + w) / zoom))
    return out


def _look_again(src: PageImage, lines: list[TextLine]) -> list[TextLine]:
    """Drafting convention: a level / storey label sits at the END of its line. Where a
    long rule ends with nothing read beside it, detect again on a small crop there - the
    detector finds in a local crop what it missed in a dense tile (measured on CLP:
    ``NIVEAU 2``, ``REZ-DE-CHAUSSÉE``)."""
    added: list[TextLine] = []
    for x0, y, x1 in long_rules(src):
        for ex, side in ((x0, 1), (x1, -1)):
            zone = (pymupdf.Rect(ex - 15, y - 14, ex + 160, y + 5) if side == 1
                    else pymupdf.Rect(ex - 160, y - 14, ex + 15, y + 5))
            zone &= pymupdf.Rect(0, 0, src.width, src.height)
            if zone.is_empty or any(zone.contains(pymupdf.Point(l.cx, l.cy)) for l in lines + added):
                continue
            clip = pymupdf.Rect(zone.x0 - 20, zone.y0 - 20, zone.x1 + 20, zone.y1 + 20) & pymupdf.Rect(0, 0, src.width, src.height)
            img = src.render(clip, DET_ZOOM)
            r = _engine()(img, use_det=True, use_cls=False, use_rec=False)
            boxes = getattr(r, "boxes", None)
            if boxes is None:
                continue
            for poly in boxes:
                xs = [clip.x0 + p[0] / DET_ZOOM for p in poly]
                ys = [clip.y0 + p[1] / DET_ZOOM for p in poly]
                b = TextLine(min(xs), min(ys), max(xs), max(ys), "", 0.0,
                             (max(ys) - min(ys)) > 1.5 * (max(xs) - min(xs)))
                if zone.contains(pymupdf.Point(b.cx, b.cy)):
                    added.append(b)
    return _join(added)


def read(src: PageImage, progress=None) -> list[TextLine]:
    lines = _detect(src)
    lines += _look_again(src, lines)
    out = []
    for i, l in enumerate(lines):
        l2 = _reread(src, l)
        l2.words = _words(l2)
        if l2.text and l2.confidence >= MIN_CONF:
            out.append(l2)
        if progress:
            progress(i + 1, len(lines))
    out.sort(key=lambda l: (round(l.y0 / 4), l.x0))
    return out


# ----------------------------------------------------------------- hand-off to parsers
def as_prepared(page: pymupdf.Page, lines: list[TextLine], fichier: str = ""):
    """A ``PreparedPage`` whose text comes from the image read, so the existing DA parsers
    run unchanged on pixels."""
    from ..page import Line, PreparedPage, Word

    p = PreparedPage(index=page.number, width=page.rect.width, height=page.rect.height,
                     fichier=fichier, _page=page)
    p.__dict__["lines"] = [Line(l.x0, l.y0, l.x1, l.y1, l.text, (l.y1 - l.y0) / 1.6, l.vertical)
                           for l in lines]
    p.__dict__["words"] = [Word(*w[:4], w[4]) for l in lines for w in l.words]
    return p
