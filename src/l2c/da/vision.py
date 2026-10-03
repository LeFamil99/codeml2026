"""AI reader for DA pages: an OCR model on rendered line crops (DA_PLAN phase 2).

Why pixels: a fabricator's CAD may write a text layer, outline every glyph, or the
drawing may be a scan; rendered, all three are the same image, so nothing here depends
on how a given company's PDF encodes its text (the template decoder in ``decode.py``
did, and is kept only as a measured baseline).

Split to fit a laptop CPU (full-page OCR of a CLP sheet at the needed resolution is
~21,000 x 14,000 px):
1. WHERE: text lines from vector geometry - every fabricator's glyphs are small filled
   paths; grouping them into lines uses position and size only, never shape or font;
2. WHAT: the OCR recognition model reads one high-resolution image per line, with the
   cap height scaled to ``CAP_PX``. On vector pages the image holds ONLY that line's own
   glyph shapes (no neighbour, leader line or hatch can intrude); on scans it is the
   rendered page region;
3. word boxes: the recognised characters are aligned to the glyph boxes when the counts
   agree, else spread proportionally - flagged in ``Line.aligned``.
Blind to the plan.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass

import numpy as np
import pymupdf

from . import decode

CAP_PX = 36.0          # rendered cap height; PP-OCR's recogniser works on 48-px-high input
MIN_CONF = 0.5         # below this a line is reported, not parsed


@dataclass
class Box:
    """A filled glyph-sized path: geometry for grouping, items for the clean render."""
    x0: float
    y0: float
    x1: float
    y1: float
    items: list | None = None
    stroke: float | None = None     # line width for a stroked glyph (CAD stroke fonts)
    solo: bool = False              # one straight segment: may JOIN a line, never seed one

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class ReadLine:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float                 # cap height in points
    confidence: float           # the recogniser's score
    words: list                 # (x0, y0, x1, y1, text)
    aligned: bool               # word boxes from exact char<->glyph alignment
    vertical: bool = False

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


# ----------------------------------------------------------------- recogniser
@functools.lru_cache(maxsize=1)
def _engine():
    import logging

    from rapidocr import RapidOCR

    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    return RapidOCR()


def recognise(img: np.ndarray) -> tuple[str, float]:
    try:
        r = _engine()(img, use_det=False, use_cls=False, use_rec=True)
    except Exception:              # degenerate crop: reported as unread, never a crash
        return "", 0.0
    txts, scores = getattr(r, "txts", None), getattr(r, "scores", None)
    if not txts:
        return "", 0.0
    return str(txts[0]).strip(), float(scores[0])


MODEL_INFO = {"recogniser": "RapidOCR PP-OCRv6 rec small (ONNX)", "licence": "Apache-2.0"}


# ----------------------------------------------------------------- geometry
def _dark(c) -> bool:
    return c is not None and min(c) < 0.85


def ink_boxes(page: pymupdf.Page) -> list[Box]:
    """Glyph-sized ink: filled outlines (most fabricators) AND stroked paths (CAD stroke
    fonts such as ``txt``/``simplex`` - LIGREP's bar table is drawn that way). A single
    straight stroked segment is usually a tick or hatch, but is also a stroke-font
    ``1 - / l``: it is kept as ``solo`` and may only join a line it sits inside."""
    out, seen = [], set()
    for d in page.get_drawings():
        r, kind = d["rect"], d.get("type")
        if not (max(r.height, r.width) <= 60 and max(r.height, r.width) >= 0.3):
            continue
        filled = kind in ("f", "fs") and _dark(d.get("fill"))
        stroked = kind in ("s", "fs") and _dark(d.get("color")) and not filled
        if not (filled or stroked):
            continue
        if filled and min(r.height, r.width) < 0.3 and max(r.height, r.width) < 1:
            continue
        key = (kind, round(r.x0 * 2), round(r.y0 * 2), round(r.x1 * 2), round(r.y1 * 2))
        if key in seen:
            continue
        seen.add(key)
        items = d["items"]
        solo = stroked and len(items) == 1 and items[0][0] == "l"
        out.append(Box(r.x0, r.y0, r.x1, r.y1, items,
                       stroke=(d.get("width") or 0.3) if stroked else None, solo=solo))
    return out


def width_factor(line: list[Box]) -> float:
    """Horizontal stretch that brings this line's characters to ordinary proportions.

    CAD text carries a width factor (CLP's fabricator condenses Times to ~36 %); an OCR
    model trained on ordinary print reads condensed glyphs worse. Measured from the
    line's own full-height characters (median w/h, ordinary print ~0.6), so it undoes
    ANY width factor without knowing the font. Never squeezes, at most 2.5x."""
    chars = _characters(line)
    H = max(c.h for c in chars)
    full = [c.w / c.h for c in chars if c.h >= 0.8 * H and c.w / max(c.h, 1e-6) > 0.25]
    if len(full) < 2:
        return 1.0
    return float(min(2.5, max(1.0, 0.6 / float(np.median(full)))))


def clean_render(line: list[Box], z: float, pad: float, sx: float = 1.0) -> np.ndarray:
    """Only this line's own glyph shapes, black on white, at zoom ``z``: neighbouring
    callouts, leader lines and hatching cannot enter the crop. Shapes are the drawing's
    own (even-odd filled), so nothing font-specific is assumed."""
    from PIL import Image, ImageChops, ImageDraw

    from .glyphs import _subpaths

    x0, y0 = min(b.x0 for b in line) - pad, min(b.y0 for b in line) - pad
    x1, y1 = max(b.x1 for b in line) + pad, max(b.y1 for b in line) + pad
    W, H = max(8, int((x1 - x0) * z * sx)), max(8, int((y1 - y0) * z))
    ink = Image.new("1", (W, H), 0)
    for b in line:
        g = Image.new("1", (W, H), 0)
        d = ImageDraw.Draw(g)
        if b.stroke is not None:                        # stroke font: draw the strokes
            lw = max(2, int(round(max(b.stroke, 0.08 * (b.y1 - b.y0)) * z)))
            for poly in _subpaths(b.items or []) or []:
                d.line([((x - x0) * z * sx, (y - y0) * z) for x, y in poly], fill=1, width=lw)
            for it in b.items or []:
                if it[0] == "l":
                    d.line([((it[1].x - x0) * z * sx, (it[1].y - y0) * z),
                            ((it[2].x - x0) * z * sx, (it[2].y - y0) * z)], fill=1, width=lw)
            ink = ImageChops.logical_or(ink, g)
            continue
        for poly in _subpaths(b.items or []):
            m = Image.new("1", (W, H), 0)
            ImageDraw.Draw(m).polygon([((x - x0) * z * sx, (y - y0) * z) for x, y in poly], fill=1)
            g = ImageChops.logical_xor(g, m)
        ink = ImageChops.logical_or(ink, g)
    img = ImageChops.invert(ink.convert("L"))
    return np.asarray(img.convert("RGB"))


def _characters(boxes: list[Box]) -> list[Box]:
    """Stacked parts (``:`` ``É`` ``i``) are one character: they overlap horizontally
    while sitting apart vertically; side-by-side touching characters do not."""
    out: list[Box] = []
    for b in sorted(boxes, key=lambda b: b.x0):
        if out:
            p = out[-1]
            ov = min(p.x1, b.x1) - max(p.x0, b.x0)
            vov = (min(p.y1, b.y1) - max(p.y0, b.y0)) / max(min(p.h, b.h), 1e-6)
            inside = p.x0 - 0.2 <= b.x0 and b.x1 <= p.x1 + 0.2 and p.y0 - 0.2 <= b.y0 and b.y1 <= p.y1 + 0.2
            if ov >= 0.5 * min(p.w, b.w) and (vov <= 0.3 or inside):
                out[-1] = Box(min(p.x0, b.x0), min(p.y0, b.y0), max(p.x1, b.x1), max(p.y1, b.y1))
                continue
        out.append(b)
    return out


def _word_boxes(text: str, chars: list[Box], x0, y0, x1, y1):
    words = text.split()
    letters = [c for c in text if not c.isspace()]
    if len(letters) == len(chars):                      # exact: one glyph per character
        out, k = [], 0
        for w in words:
            cs = chars[k:k + len(w)]
            k += len(w)
            out.append((cs[0].x0, min(c.y0 for c in cs), cs[-1].x1, max(c.y1 for c in cs), w))
        return out, True
    total = max(1, len(text))                            # proportional fallback
    out, pos = [], 0
    for w in words:
        start = text.index(w, pos)
        pos = start + len(w)
        out.append((x0 + (x1 - x0) * start / total, y0, x0 + (x1 - x0) * pos / total, y1, w))
    return out, False


# ----------------------------------------------------------------- page
def _rot(b: Box) -> Box:
    """Page box -> the frame of text running bottom-to-top (a 90 deg turn)."""
    return Box(-b.y1, b.x0, -b.y0, b.x1)


def _groups(boxes: list[Box]) -> list[tuple[bool, list[int]]]:
    """Lines in both orientations; each glyph joins the orientation where its line is
    longer (35 % of CLP slab callouts are vertical, read bottom-to-top)."""
    horiz = decode._lines(boxes)
    vert = decode._lines([_rot(b) for b in boxes])
    size_h, size_v = {}, {}
    for g in horiz:
        for i in g:
            size_h[i] = len(g)
    for g in vert:
        for i in g:
            size_v[i] = len(g)
    is_v = {i: size_v.get(i, 1) > size_h.get(i, 1) for i in range(len(boxes))}
    out = []
    for g in horiz:
        keep = [i for i in g if not is_v[i]]
        if keep:
            out.append((False, keep))
    for g in vert:
        keep = [i for i in g if is_v[i]]
        if keep:
            out.append((True, keep))
    return out


def _is_text_like(line: list[Box]) -> bool:
    """Drop hatch ticks and arrowheads before paying for OCR: a lone mark that is far
    from glyph proportions is not text (a real 1-character line - a grid bubble letter
    or a single count - keeps glyph proportions)."""
    if len(line) > 1:
        return True
    b = line[0]
    return 0.15 <= b.w / max(b.h, 1e-6) <= 1.6 and b.h >= 1.0


def _split_long(groups, boxes):
    """A line wider than 40x its height is two or more lines that the wide-gap linking
    joined: split it at gaps above 1.3 h (the normal letter/word reach)."""
    out = []
    for vertical, idx in groups:
        frame = {i: (_rot(boxes[i]) if vertical else boxes[i]) for i in idx}
        order = sorted(idx, key=lambda i: frame[i].x0)
        H = float(np.median([frame[i].h for i in order]))
        width = frame[order[-1]].x1 - frame[order[0]].x0
        if width <= 40 * H:
            out.append((vertical, idx))
            continue
        cur = [order[0]]
        for a, b in zip(order, order[1:]):
            if frame[b].x0 - frame[a].x1 > 1.3 * H or (frame[b].x1 - frame[cur[0]].x0) > 40 * H:
                out.append((vertical, cur))
                cur = []
            cur.append(b)
        out.append((vertical, cur))
    return out


def read_page(page: pymupdf.Page, progress=None) -> list[ReadLine]:
    boxes = ink_boxes(page)
    groups = _split_long(_groups(boxes), boxes)
    dl = page.get_displaylist()
    out: list[ReadLine] = []
    for gi, (vertical, idx) in enumerate(groups):
        line = [boxes[i] for i in idx]
        if not _is_text_like([_rot(b) for b in line] if vertical else line):
            continue
        frame = [_rot(b) for b in line] if vertical else line
        H, _ = decode._cap(frame, list(range(len(frame))))
        if H <= 0:
            continue
        x0, y0 = min(b.x0 for b in line), min(b.y0 for b in line)
        x1, y1 = max(b.x1 for b in line), max(b.y1 for b in line)
        z = min(16.0, max(2.0, CAP_PX / H))
        if all(b.items for b in line):                        # vector page: own glyphs only
            if vertical:
                img = clean_render(line, z, pad=0.8 * H)
            else:
                img = clean_render(line, z, pad=0.8 * H, sx=width_factor(line))
        else:                                                 # scan: the page itself
            px, py = (0.35 * H, 0.6 * H) if vertical else (0.6 * H, 0.35 * H)
            clip = pymupdf.Rect(x0 - px, y0 - py, x1 + px, y1 + py)
            pix = dl.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip,
                                colorspace=pymupdf.csRGB, alpha=False)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
        if vertical:       # bottom-to-top is the norm; try both turns, keep the surer read
            reads = [recognise(np.ascontiguousarray(np.rot90(img, k))) for k in (-1, 1)]
            (text, conf), k = max(zip(reads, (-1, 1)), key=lambda t: t[0][1])
        else:
            text, conf = recognise(img)
        if progress:
            progress(gi + 1, len(groups))
        if not text:
            continue
        if vertical:
            chars = sorted(_characters(frame), key=lambda c: c.x0)
            rw, _ = _word_boxes(text, chars, *_bounds(frame))
            # back to page coordinates
            words = [(c[1], -c[2], c[3], -c[0], c[4]) for c in rw]
            aligned = len([c for c in text if not c.isspace()]) == len(chars)
        else:
            chars = _characters(line)
            words, aligned = _word_boxes(text, chars, x0, y0, x1, y1)
        out.append(ReadLine(x0, y0, x1, y1, text, H, round(conf, 3), words, aligned, vertical))
    out.sort(key=lambda l: (round(l.y0 / 4), l.x0))
    return out


def _bounds(bs: list[Box]):
    return min(b.x0 for b in bs), min(b.y0 for b in bs), max(b.x1 for b in bs), max(b.y1 for b in bs)
