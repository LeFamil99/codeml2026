"""CLP column schedule (DA "Colonnes") - read from the page IMAGE.

Layout (CLP's fabricator):
- a grid; each vertical strip is one building column, its grid coordinate (``K-6``)
  written in the bottom cell;
- each row is one storey, named at the far left, in the label zone left of the table;
- an empty cell = no column there at that storey; a cell with content = a column at
  (coordinate of its strip, storey of its row), the content being its reinforcement
  (``VERT: 4 25M 25Z12-01``, ``ÉTRI: 6 10M 10ET13X21 @6"`` ...).

How it reads the image:
1. render the page, find the ruled lines with OpenCV (long vertical / horizontal
   strokes) -> the tables, their columns and rows;
2. OCR (PP-OCR via RapidOCR, local) the bottom cell of each strip -> coordinate, and
   the left cell of each row -> storey;
3. every other cell: no ink -> empty; ink -> OCR its text, parse each line as a bar line.

Run (page 1 by default; logs every step with timings):
    PYTHONPATH=src python -m l2c.da.parsers.colonne_clp                 # CLP Partie 1, page 1
    PYTHONPATH=src python -m l2c.da.parsers.colonne_clp FILE.pdf --page 4 --check
    PYTHONPATH=src python -m l2c.da.parsers.colonne_clp --all-pages --quiet
``--check`` also prints, per cell, what the PDF's hidden text layer says - for
validation only, the parser never uses it.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import re
import sys
import time
from dataclasses import asdict, dataclass, field

import cv2
import numpy as np
import pymupdf

if __package__ in (None, ""):                      # allow `python colonne_clp.py`
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from l2c.da.common import parse_bar_line  # noqa: E402

log = logging.getLogger("colonne_clp")

# Partie 3 is the current release; Parties 1 and 2 are older versions of it
DEFAULT_FILE = os.path.expanduser(
    "~/Downloads/l2c-participants/CLP/DA/Colonnes/CLP_COLONNES Partie 3.pdf")
MAX_STRIPS = 2           # TEMPORARY, for fast testing: parse only the first N data strips
LABEL_ZONE = 110.0       # pt left of the table where the storey names are written

GRID_ZOOM = 2.0          # rule detection resolution (px per pt)
DET_ZOOM = 4.0           # text DETECTION resolution (finding where lines are): cheap
                         # (3 was faster but merged CLP's tightly stacked lines)
REC_PX = 44.0            # text RECOGNITION: each line re-rendered so it is ~44 px tall
INSET = 2.5              # pt kept away from cell borders
COORD = re.compile(r"^[A-Z]{1,2}(\.\d)?-\d{1,2}(\.\d{1,2})?$")
LEVEL = re.compile(r"(TOIT APPENTIS|NIVEAU\s*\d+(\s*-?\s*TOIT)?|TOIT|REZ-DE-CHAUSS[ÉE]E|"
                   r"TR[ÉE]FONDS?|SOUS-SOL(\s*S?\d)?|RADIER|EMPATTEMENT[ A-Z]*)", re.I)
ELEVATION = re.compile(r"EL\.?\s*:?\s*(\d{1,3})\s*'\s*-?\s*(\d{1,2}(?:\s+\d/\d)?)\s*\"?")


# ----------------------------------------------------------------- data
@dataclass
class Cell:
    page: int
    coordinate: str | None
    level: str | None
    x0: float
    y0: float
    x1: float
    y1: float
    status: str                      # "empty" | "text" | "ink, no text"
    lines: list[str] = field(default_factory=list)
    bars: list[dict] = field(default_factory=list)
    confidence: float | None = None
    storey_inferred: bool = False    # the storey name was not read, it was inferred
    elevation: str | None = None     # "122' - 0\"" read in the label zone
    summary: str | None = None       # "4-25M · 10M@152mm" when both parts were read
    reason: str | None = None        # why a non-empty cell was discarded
    oracle: list[str] | None = None  # --check only
    oracle_summary: str | None = None


# ----------------------------------------------------------------- image helpers
_DL: dict[tuple, pymupdf.DisplayList] = {}


def render(page: pymupdf.Page, clip: pymupdf.Rect | None, zoom: float) -> np.ndarray:
    """Pixels of a page region. The page is interpreted ONCE into a display list and every
    crop is rendered from it: re-rendering a heavy CAD page per crop was the main cost."""
    key = (id(page.parent), page.number)
    if key not in _DL:
        _DL.clear()
        _DL[key] = page.get_displaylist()
    pix = _DL[key].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip,
                              colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)


def _segments(mask: np.ndarray, horizontal: bool):
    n, _, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for i in range(1, n):
        x, y, w, h, _ = st[i]
        if horizontal and h <= 8:
            out.append(((y + h / 2) / GRID_ZOOM, x / GRID_ZOOM, (x + w) / GRID_ZOOM))
        elif not horizontal and w <= 8:
            out.append(((x + w / 2) / GRID_ZOOM, y / GRID_ZOOM, (y + h) / GRID_ZOOM))
    return out


def _merge(vals: list[float], tol: float) -> list[float]:
    out: list[list[float]] = []
    for v in sorted(vals):
        if out and v - out[-1][-1] <= tol:
            out[-1].append(v)
        else:
            out.append([v])
    return [sum(g) / len(g) for g in out]


@dataclass
class Table:
    xs: list[float]                  # column boundaries, left to right
    ys: list[float]                  # row boundaries, top to bottom


def find_tables(page: pymupdf.Page) -> list[Table]:
    """Tables = families of long vertical rules sharing the same top and bottom; rows =
    horizontal rules crossing most of a family's width (double rules merged)."""
    t = time.time()
    img = render(page, None, GRID_ZOOM)
    log.info("rendered page %d for line detection: %dx%d px in %.1fs",
             page.number + 1, img.shape[1], img.shape[0], time.time() - t)
    ink = (cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) < 160).astype(np.uint8)
    H_px, W_px = ink.shape
    vmask = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (1, int(0.12 * H_px))))
    verts = _segments(vmask, horizontal=False)
    log.info("long vertical rules: %d", len(verts))
    # families: same y-extent within 15 pt
    fams: list[list[tuple]] = []
    for v in sorted(verts, key=lambda v: (round(v[1] / 15), round(v[2] / 15), v[0])):
        for f in fams:
            if abs(f[0][1] - v[1]) < 15 and abs(f[0][2] - v[2]) < 15:
                f.append(v)
                break
        else:
            fams.append([v])
    hmask = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (int(0.05 * W_px), 1)))
    hors = _segments(hmask, horizontal=True)
    log.info("horizontal rules: %d; vertical-rule families (same top/bottom): %d",
             len(hors), len(fams))
    tables = []
    for f in fams:
        xs = _merge([v[0] for v in f], 4.0)
        if len(xs) < 4:
            log.debug("  family of %d verticals at y %.0f-%.0f: too few, ignored",
                      len(xs), f[0][1], f[0][2])
            continue
        x0, x1 = xs[0], xs[-1]
        y0, y1 = min(v[1] for v in f), max(v[2] for v in f)
        ys = [h[0] for h in hors if y0 - 6 <= h[0] <= y1 + 6
              and min(h[2], x1) - max(h[1], x0) >= 0.6 * (x1 - x0)]
        ys = _merge(ys + [y0, y1], 15.0)
        if len(ys) >= 3:
            tables.append(Table(xs, ys))
            log.info("TABLE %d: x %.0f-%.0f, y %.0f-%.0f -> %d strips, %d rows",
                     len(tables), x0, x1, y0, y1, len(xs) - 1, len(ys) - 1)
            log.info("    column boundaries x: %s", " ".join(f"{v:.0f}" for v in xs))
            log.info("    row boundaries    y: %s", " ".join(f"{v:.0f}" for v in ys))
        else:
            log.debug("  family x %.0f-%.0f: only %d row rules, ignored", x0, x1, len(ys))
    log.info("tables found: %d (%.1fs)", len(tables), time.time() - t)
    return tables


# ----------------------------------------------------------------- OCR
OCR_THREADS = 4          # onnxruntime threads: capped so the laptop stays usable
                         # (uncapped, one process kept ~15 cores busy)


@functools.lru_cache(maxsize=1)
def _ocr():
    from rapidocr import RapidOCR

    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    return RapidOCR(params={"Global.log_level": "warning", "Global.text_score": 0.4,
                            "Det.thresh": 0.2, "Det.box_thresh": 0.3,
                            "EngineConfig.onnxruntime.intra_op_num_threads": OCR_THREADS,
                            "EngineConfig.onnxruntime.inter_op_num_threads": 1 if OCR_THREADS > 0 else -1})


RETRY_ZOOM = 8.0         # detection zoom for a second look at an incomplete cell


def ocr_region(page: pymupdf.Page, rect: pymupdf.Rect, erase_rules: bool = False,
               det_zoom: float = DET_ZOOM):
    """Text lines in a region, as [(Rect in pt, text, score)], top to bottom.

    Two stages, for speed: DETECT on a low-zoom render (where are the lines), then
    RECOGNISE each line from its own high-zoom crop (what do they say). Detection is
    the expensive model; at zoom 3 instead of 8 it sees ~7x fewer pixels, while every
    line is still read at full resolution."""
    img = render(page, rect, det_zoom)
    r = _ocr()(img, use_det=True, use_cls=False, use_rec=False)
    boxes = getattr(r, "boxes", None)
    if boxes is None:
        return []
    found = []
    for poly in boxes:
        xs = [rect.x0 + p[0] / det_zoom for p in poly]
        ys = [rect.y0 + p[1] / det_zoom for p in poly]
        found.append(pymupdf.Rect(min(xs), min(ys), max(xs), max(ys)))
    # a box ~twice as tall as a typical line is two stacked lines merged by detection
    hs = sorted(b.height for b in found)
    typical = hs[len(hs) // 2] if hs else 0
    split = []
    for b in found:
        if typical and b.height > 1.7 * typical and len(found) > 1:
            k = max(2, round(b.height / typical))
            step = b.height / k
            split += [pymupdf.Rect(b.x0, b.y0 + i * step, b.x1, b.y0 + (i + 1) * step) for i in range(k)]
            log.debug("      split a %.1f-pt box into %d lines (typical line %.1f pt)", b.height, k, typical)
        else:
            split.append(b)
    out = []
    for box in split:
        text, score = recognise(page, box, erase_rules)
        if text:
            out.append((box, text, score))
    out.sort(key=lambda t: (t[0].y0, t[0].x0))
    return out


def recognise(page: pymupdf.Page, box: pymupdf.Rect, erase_rules: bool = False) -> tuple[str, float]:
    h = max(box.height, 1.0)
    z = min(20.0, max(4.0, REC_PX / h))
    clip = pymupdf.Rect(box.x0 - 0.3 * h, box.y0 - 0.15 * h, box.x1 + 0.3 * h, box.y1 + 0.15 * h)
    img = render(page, clip, z)
    if erase_rules:
        from l2c.da.imageread import remove_rules

        img = remove_rules(img, h * z / 1.6)
    try:
        r = _ocr()(img, use_det=False, use_cls=False, use_rec=True)
    except Exception:
        return "", 0.0
    t, sc = getattr(r, "txts", None), getattr(r, "scores", None)
    return (str(t[0]).strip(), float(sc[0])) if t else ("", 0.0)


def read_cell(page: pymupdf.Page, rect: pymupdf.Rect, what: str = "cell",
              det_zoom: float = DET_ZOOM) -> tuple[str, list[str], float | None]:
    """(status, text lines top-to-bottom, mean confidence) for one cell."""
    t0 = time.time()
    inner = pymupdf.Rect(rect.x0 + INSET, rect.y0 + INSET, rect.x1 - INSET, rect.y1 - INSET)
    if inner.is_empty:
        return "empty", [], None
    img = render(page, inner, DET_ZOOM)
    ink = (cv2.cvtColor(img, cv2.COLOR_RGB2GRAY) < 160)
    if ink.mean() < 0.0005:
        log.debug("    %s (%.0f,%.0f)-(%.0f,%.0f): empty (ink %.4f%%)", what, *rect, 100 * ink.mean())
        return "empty", [], None
    items = ocr_region(page, inner, det_zoom=det_zoom)
    if not items:
        log.debug("    %s (%.0f,%.0f)-(%.0f,%.0f): ink but OCR found no text (%.1fs)",
                  what, *rect, time.time() - t0)
        return "ink, no text", [], None
    # boxes on the same row (a label and its value) are one line
    lines: list[list] = []
    for box, t, sc in items:
        cy = (box.y0 + box.y1) / 2
        if lines and abs(lines[-1][0] - cy) < 0.5 * box.height:
            lines[-1][1].append((box.x0, t, sc))
        else:
            lines.append([cy, [(box.x0, t, sc)]])
    out = [" ".join(t for _, t, _ in sorted(parts)).strip() for _, parts in lines]
    out = [t for t in out if t]
    conf = float(np.mean([sc for _, _, sc in items]))
    log.debug("    %s (%.0f,%.0f)-(%.0f,%.0f): %d lines, conf %.2f, %.1fs -> %s",
              what, *rect, len(items), conf, time.time() - t0, " / ".join(out))
    return "text", out, round(conf, 3)


def read_storeys(page: pymupdf.Page, zone: pymupdf.Rect, ys: list[float]):
    """Storey name and elevation per row, from the label strip left of the table.

    Drawing convention (CLP): ``EL.: 112' - 3"`` is written ABOVE the dashed storey
    line and the storey name (``NIVEAU 2``) right UNDER it. So the strip is OCR'd once,
    every elevation label is found, and the name is read directly under it - from a box
    the detector found there, else from a crop right below the elevation with the
    dashed line erased. A name belongs to the row whose bottom rule is nearest."""
    items = ocr_region(page, zone)
    log.debug("    label strip: %d text lines: %s", len(items), " / ".join(t for _, t, _ in items))
    n_rows = len(ys) - 2
    names: list[str | None] = [None] * n_rows
    elevs: list[str | None] = [None] * n_rows
    raw: list[str | None] = [None] * n_rows

    def row_of(y: float) -> int:
        return min(range(n_rows), key=lambda j: abs(ys[j + 1] - y))

    for box, text, _ in items:
        if not ELEVATION.search(text):
            continue
        h = box.height
        under = [(b, t) for b, t, _ in items
                 if b is not box and box.y1 - 0.3 * h <= b.y0 <= box.y1 + 1.5 * h
                 and min(b.x1, box.x1) - max(b.x0, box.x0) > 0.3 * min(b.width, box.width)
                 and not ELEVATION.search(t)]
        name_text = under[0][1] if under else None
        name_box = under[0][0] if under else None
        if not (name_text and _level([name_text])):
            crop = pymupdf.Rect(box.x0 - 4, box.y1, box.x1 + 30, box.y1 + 1.6 * h)
            t2, _ = recognise(page, crop, erase_rules=True)
            if _level([t2]) or not name_text:
                name_text, name_box = t2, crop
        j = row_of((name_box.y0 + name_box.y1) / 2 if name_box else box.y1)
        elevs[j] = _elevation([text])
        if _level([name_text or ""]):
            names[j] = _level([name_text])
        raw[j] = name_text
        log.info("    elevation %-12s at y %.0f -> name under it: %-22s -> row %d",
                 elevs[j], box.y1, repr(name_text), j)
    # a name with no elevation above it (e.g. TOIT APPENTIS)
    for box, text, _ in items:
        lv = _level([text])
        if lv and not ELEVATION.search(text):
            j = row_of((box.y0 + box.y1) / 2)
            if names[j] is None:
                names[j] = lv
    return names, elevs, raw


def _oracle(page: pymupdf.Page, rect: pymupdf.Rect) -> list[str]:
    """--check only: the PDF's hidden text layer inside the cell."""
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            r = pymupdf.Rect(l["bbox"])
            if rect.contains(pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)):
                t = re.sub(r"\s+", " ", " ".join(s["text"] for s in l["spans"])).strip()
                if t:
                    out.append(t)
    return out


# ----------------------------------------------------------------- summary
def summarize(bars: list[dict]) -> tuple[str | None, str | None]:
    """(summary, reason). ``4-25M · 10M@152mm``: the verticals (count-size) and the
    spaced ties (size@spacing). Both are needed, else the cell is discarded."""
    vert = next((b for b in bars if b.get("quantite") and b.get("diametre")
                 and not b.get("espacement_mm") and (b.get("label") or "").startswith("VERT")), None)
    vert = vert or next((b for b in bars if b.get("quantite") and b.get("diametre")
                         and not b.get("espacement_mm") and not (b.get("label") or "").startswith(("ÉTRI", "ETRI"))), None)
    ties = next((b for b in bars if b.get("espacement_mm") and b.get("diametre")), None)
    if vert and ties:
        return f"{vert['quantite']}-{vert['diametre']} · {ties['diametre']}@{ties['espacement_mm']:.0f}mm", None
    missing = [n for n, v in (("verticals", vert), ("spaced ties", ties)) if not v]
    return None, "no " + " and no ".join(missing)


def _bars_of(lines: list[str], system: str) -> list[dict]:
    out = []
    for t in lines:
        b = parse_bar_line(t, system)
        if b is not None:
            out.append({"label": b.label, **b.armature.model_dump(exclude_none=True)})
    return out


# ----------------------------------------------------------------- parallel workers
_WORKER: dict = {}


def _worker_init(path: str, page_no: int, threads: int) -> None:
    """Each worker process opens the page and loads the OCR model once."""
    global OCR_THREADS
    OCR_THREADS = threads
    _ocr.cache_clear()
    _DL.clear()
    doc = pymupdf.open(path)
    page = doc[page_no]
    if page.rotation:
        page.remove_rotation()
    _WORKER.update(doc=doc, page=page)


def read_data_cell(page: pymupdf.Page, rect: pymupdf.Rect, what: str, system: str):
    """One data cell: read, summarise, and give it a second look if half-read."""
    status, lines, conf = read_cell(page, rect, what)
    bars = _bars_of(lines, system)
    summary, reason = summarize(bars) if status == "text" else (None, status)
    retried = False
    if status == "text" and summary is None and bars:
        # half a summary (verticals without ties, or the reverse): detection missed a
        # line - look again at this cell only, at higher zoom
        retried = True
        s2, l2, c2 = read_cell(page, rect, f"{what} retry", RETRY_ZOOM)
        b2 = _bars_of(l2, system)
        sm2, rs2 = summarize(b2) if s2 == "text" else (None, s2)
        if sm2:
            status, lines, conf, bars, summary, reason = s2, l2, c2, b2, sm2, None
    return status, lines, conf, bars, summary, reason, retried


def _worker_task(args):
    kind, rect, what, system = args
    page = _WORKER["page"]
    r = pymupdf.Rect(*rect)
    if kind == "bottom":
        return read_cell(page, r, what)
    return read_data_cell(page, r, what, system)


# ----------------------------------------------------------------- the parser
def _coordinate(lines: list[str]) -> str | None:
    for t in lines:
        for tok in t.replace(" -", "-").replace("- ", "-").split():
            tok = tok.rstrip(".,;:")                     # OCR adds stray dots ("A-15.8.")
            if COORD.match(tok):
                return tok
    return None


def _level(lines: list[str]) -> str | None:
    """The LAST storey name in the zone: the name sits at the bottom of its row, right on
    the row's bottom line (CLP: "NIVEAU 4" at y 695, row 537-697)."""
    found = None
    for t in lines:
        for m in LEVEL.finditer(t):
            found = re.sub(r"\s+", " ", m.group(1).upper()).replace("TREFOND", "TRÉFOND")
    if found:
        found = re.sub(r"^NIVEAU\s*(\d)", r"NIVEAU \1", found)  # "NIVEAU4" -> "NIVEAU 4"
        found = found.replace("CHAUSSEE", "CHAUSSÉE")
    if found and found.startswith("NIVEAU") and "TOIT" in found:
        found = re.sub(r"\s*-?\s*TOIT", "", found)        # "NIVEAU 5 - TOIT" is NIVEAU 5
    return found


def _elevation(lines: list[str]) -> str | None:
    els = [m for t in lines for m in ELEVATION.finditer(t)]
    return f"{els[-1].group(1)}' - {els[-1].group(2)}\"" if els else None


def _rank(level: str) -> float | None:
    """Height order of a storey name (bigger = higher)."""
    if level.startswith("TOIT APPENTIS"):
        return 1001.0
    if level.startswith("TOIT"):
        return 1000.0
    if m := re.match(r"NIVEAU (\d+)", level):
        return float(m.group(1))
    if level.startswith("REZ"):
        return 1.0
    if level.startswith("TRÉFOND"):
        return 0.5
    if level.startswith("SOUS-SOL"):
        m = re.search(r"S?(\d)$", level)
        return -float(m.group(1)) + 1 if m else 0.0
    return None


def _between(upper: float, lower: float) -> list[str]:
    """Standard storeys strictly between two ranks, top to bottom."""
    out = []
    if upper > 1000 or upper == 1000:            # under a roof: floors unknown from here
        return []
    k = int(upper) - 1
    while k >= 2 and k > lower:
        out.append(f"NIVEAU {k}")
        k -= 1
    if upper > 1 > lower:
        out.append("REZ-DE-CHAUSSÉE")
    return out


def _upward(lower: float, n: int) -> list[str]:
    """``n`` standard storeys counted UP from ``lower`` (e.g. above TRÉFONDS: RDC,
    NIVEAU 2, NIVEAU 3 ...), returned top to bottom."""
    out = []
    k = int(lower) + 1 if lower >= 1 else 1
    while len(out) < n:
        out.append("REZ-DE-CHAUSSÉE" if k == 1 else f"NIVEAU {k}")
        k += 1
    return out[::-1]


def _hint_digit(text: str | None) -> int | None:
    """A storey number visible in a garbled read ("NIVE AL 2" -> 2)."""
    if not text or not re.search(r"N?I?VE|IVE|EAU|AL", text.upper()):
        return None
    m = re.search(r"(\d{1,2})\s*$", text)
    return int(m.group(1)) if m else None


def infer_levels(read: list[str | None], hints: list[str | None] | None = None
                 ) -> tuple[list[str | None], list[bool]]:
    """Fill storeys the OCR could not read from the ones it did read (rows top to bottom).

    - Between two read storeys: filled when the number of missing rows equals the number
      of standard storeys between them.
    - Under a roof (TOIT above, floor numbers unknown from there): counted UP from the
      storey read below (TRÉFONDS -> RDC, NIVEAU 2, ...).
    - Otherwise: the next storeys down from the one above, marked uncertain with '?'.
    A storey number visible in a garbled read (``hints``) must agree, else '?'."""
    hints = hints or [None] * len(read)
    levels, inferred = list(read), [False] * len(read)
    j = 0
    while j < len(levels):
        if levels[j] is not None:
            j += 1
            continue
        k = j
        while k < len(levels) and levels[k] is None:
            k += 1
        above = next((levels[i] for i in range(j - 1, -1, -1) if levels[i]), None)
        below = levels[k] if k < len(levels) else None
        ra = _rank(above) if above else None
        rb = _rank(below) if below else None
        fill = _between(ra, rb if rb is not None else -99) if ra is not None else []
        n = k - j
        if len(fill) == n and rb is not None:
            names = fill
        elif (ra is None or ra >= 1000) and rb is not None and rb < 1000:
            names = _upward(rb, n)                       # under the roof: count up
        else:
            names = [f"{x} ?" for x in fill[:n]] + [None] * max(0, n - len(fill))
        for i, name in enumerate(names):                 # garbled reads must agree
            d = _hint_digit(hints[j + i])
            if name and d is not None and not name.endswith("?"):
                m = re.match(r"NIVEAU (\d+)", name)
                if not m or int(m.group(1)) != d:
                    names[i] = f"{name} ?"
        for i, name in enumerate(names):
            levels[j + i] = name
            inferred[j + i] = name is not None
        j = k
    return levels, inferred


def parse_page(page: pymupdf.Page, system: str = "imperial", check: bool = False,
               on_cell=None, max_strips: int | None = MAX_STRIPS, workers: int = 1,
               path: str | None = None) -> list[Cell]:
    t_page = time.time()
    cells: list[Cell] = []
    pool = None
    if workers > 1 and path:
        import multiprocessing as mp

        threads = max(1, OCR_THREADS // workers)        # the same CPU budget, split
        log.info("starting %d OCR worker processes (%d threads each)", workers, threads)
        t = time.time()
        pool = mp.get_context("fork").Pool(workers, _worker_init, (path, page.number, threads))
        log.info("workers ready in %.1fs", time.time() - t)

    def run(tasks):
        if pool is None:
            for kind, rect, what, sysm in tasks:
                r = pymupdf.Rect(*rect)
                yield read_cell(page, r, what) if kind == "bottom" else read_data_cell(page, r, what, sysm)
        else:
            yield from pool.imap(_worker_task, tasks)

    try:
        for ti, table in enumerate(find_tables(page), 1):
            xs, ys = table.xs, table.ys
            strips = list(range(len(xs) - 1))             # every strip is a building column
            if max_strips:
                strips = strips[:max_strips]
                log.info("TEMPORARY LIMIT: only the first %d of %d strips are parsed",
                         len(strips), len(xs) - 1)
            t = time.time()
            log.info("table %d - step 1/3: reading the coordinate in the bottom cell of %d strips",
                     ti, len(strips))
            tasks = [("bottom", tuple(pymupdf.Rect(xs[i], ys[-2], xs[i + 1], ys[-1])),
                      f"strip {i} bottom", system) for i in strips]
            coords = {}
            for i, (_, lines, _) in zip(strips, run(tasks)):
                coords[i] = _coordinate(lines)
                log.info("    strip %2d (x %.0f-%.0f): coordinate %s   [bottom cell: %s]",
                         i, xs[i], xs[i + 1], coords[i] or "NOT FOUND", " / ".join(lines) or "-")
            log.info("    step 1 took %.1fs", time.time() - t)
            # storey names are written LEFT of the table, at the level of each row
            t = time.time()
            zx0 = max(0.0, xs[0] - LABEL_ZONE)
            log.info("table %d - step 2/3: reading the storey of %d rows in the label zone x %.0f-%.0f",
                     ti, len(ys) - 2, zx0, xs[0])
            zone = pymupdf.Rect(zx0, ys[0], xs[0], ys[-2])
            read_levels, elevations, hints = read_storeys(page, zone, ys)
            for j in range(len(ys) - 2):
                log.info("    row %2d (y %.0f-%.0f): storey %s, elevation %s",
                         j, ys[j], ys[j + 1], read_levels[j] or "NOT READ", elevations[j] or "-")
            names, inferred = infer_levels(read_levels, hints)
            levels = dict(enumerate(names))
            for j, (r, nm, inf) in enumerate(zip(read_levels, names, inferred)):
                if inf:
                    log.info("    row %2d: storey INFERRED as %s (between the storeys read above and below)",
                             j, nm)
                elif r is None:
                    log.info("    row %2d: storey could not be read nor inferred", j)
            log.info("    step 2 took %.1fs", time.time() - t)
            t = time.time()
            grid = [(i, j) for i in strips for j in range(len(ys) - 2)]
            n = len(grid)
            log.info("table %d - step 3/3: reading %d data cells (empty ones are skipped)%s", ti, n,
                     f", {workers} in parallel" if pool else "")
            tasks = [("data", tuple(pymupdf.Rect(xs[i], ys[j], xs[i + 1], ys[j + 1])),
                      f"cell {k}/{n}", system) for k, (i, j) in enumerate(grid, 1)]
            for k, ((i, j), res) in enumerate(zip(grid, run(tasks)), 1):
                status, lines, conf, bars, summary, reason, retried = res
                rect = pymupdf.Rect(xs[i], ys[j], xs[i + 1], ys[j + 1])
                if retried and summary:
                    log.info("    cell %d/%d: recovered on second look -> %s", k, n, summary)
                oracle = _oracle(page, rect) if check else None
                c = Cell(page.number + 1, coords[i], levels[j], round(rect.x0, 1),
                         round(rect.y0, 1), round(rect.x1, 1), round(rect.y1, 1),
                         status, lines, bars, conf, inferred[j], elevations[j], summary, reason,
                         oracle, summarize(_bars_of(oracle, system))[0] if check else None)
                cells.append(c)
                if on_cell:
                    on_cell(c, k, n)
            log.info("    step 3 took %.1fs (%.2fs per cell)", time.time() - t, (time.time() - t) / max(1, n))
    finally:
        if pool is not None:
            pool.close()
            pool.join()
    log.info("page %d done: %d cells, %d with content, %.0fs", page.number + 1, len(cells),
             sum(c.status != "empty" for c in cells), time.time() - t_page)
    return cells


def parse(path: str, pages: list[int] | None = None, check: bool = False, on_cell=None,
          max_strips: int | None = MAX_STRIPS, workers: int = 1) -> list[Cell]:
    doc = pymupdf.open(path)
    log.info("file %s: %d pages; parsing %s", os.path.basename(path), len(doc),
             "all" if not pages else "page(s) " + ", ".join(map(str, pages)))
    out = []
    for i in range(len(doc)):
        if pages and i + 1 not in pages:
            continue
        page = doc[i]
        if page.rotation:
            page.remove_rotation()
        log.info("--- page %d (%.0f x %.0f pt)", i + 1, page.rect.width, page.rect.height)
        out += parse_page(page, check=check, on_cell=on_cell, max_strips=max_strips,
                          workers=workers, path=path)
    return out


# ----------------------------------------------------------------- CLI
def _fmt_bars(c: Cell) -> str:
    return "; ".join(f"{b.get('label') or ''} {b.get('quantite', '')}-{b.get('diametre', '')}"
                     + (f" @{b['espacement_mm']:.0f}mm" if b.get("espacement_mm") else "")
                     + (f" [{b['repere']}]" if b.get("repere") else "") for b in c.bars)


def _print_cell(c: Cell, check: bool) -> None:
    if c.status == "empty":
        return
    lvl = f"{c.level}{' (inferred)' if c.storey_inferred else ''}"
    where = f"{c.coordinate or '?':7} storey={lvl[:28]:28}"
    if c.summary:
        verdict = ""
        if check:
            verdict = ("   CHECK OK" if c.summary == c.oracle_summary
                       else f"   CHECK DIFF (hidden layer: {c.oracle_summary or 'no summary'})")
        print(f"SAVED      {where} {c.summary}{verdict}", flush=True)
    else:
        extra = f"   (hidden layer: {c.oracle_summary})" if check and c.oracle_summary else ""
        print(f"DISCARDED  {where} {c.reason}   [read: {' / '.join(c.lines) or '-'}]{extra}", flush=True)


def main(argv=None) -> int:
    global OCR_THREADS
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", nargs="?", default=DEFAULT_FILE)
    ap.add_argument("--page", type=int, action="append", help="1-based, repeatable (default: 1)")
    ap.add_argument("--all-pages", action="store_true")
    ap.add_argument("--check", action="store_true", help="compare with the hidden text layer")
    ap.add_argument("--json", default="out/colonne_clp.json",
                    help="where the SAVED results go (default out/colonne_clp.json)")
    ap.add_argument("--all-cells-json", help="also dump every cell, saved or not, for debugging")
    ap.add_argument("--quiet", action="store_true", help="step logs only, no per-cell logs")
    ap.add_argument("--workers", type=int, default=1,
                    help="parallel OCR processes (default 1; measured: 6 was only ~20%% faster)")
    ap.add_argument("--threads", type=int, default=4,
                    help="CPU threads for the OCR model (default 4)")
    ap.add_argument("--max-strips", type=int, default=MAX_STRIPS,
                    help=f"TEMPORARY: parse only the first N data strips (default {MAX_STRIPS}, 0 = all)")
    args = ap.parse_args(argv)
    OCR_THREADS = max(1, args.threads)
    try:
        os.nice(10)                    # low priority: the desktop always comes first
    except OSError:
        pass
    cv2.setNumThreads(1)
    logging.basicConfig(level=logging.INFO if args.quiet else logging.DEBUG,
                        format="%(asctime)s %(levelname)-5s %(message)s", datefmt="%H:%M:%S",
                        stream=sys.stderr)
    for noisy in ("RapidOCR", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    pages = None if args.all_pages else (args.page or [1])
    t = time.time()
    cells = parse(args.file, pages, args.check, on_cell=lambda c, k, n: _print_cell(c, args.check),
                  max_strips=args.max_strips or None, workers=args.workers)
    full = [c for c in cells if c.status != "empty"]
    saved = [c for c in cells if c.summary]
    print(f"\nSUMMARY  {len(cells)} cells, {len(full)} with content, {len(saved)} saved, "
          f"{len(full) - len(saved)} discarded, {time.time() - t:.0f}s")
    if args.check:
        ok = sum(c.summary == c.oracle_summary for c in saved)
        lost = sum(1 for c in full if not c.summary and c.oracle_summary)
        print(f"CHECK    {ok}/{len(saved)} saved summaries equal the hidden layer's; "
              f"{lost} discarded cells did have a summary in the hidden layer")
    os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
    with open(args.json, "w", encoding="utf-8") as fh:
        json.dump([{"page": c.page, "coordinate": c.coordinate, "storey": c.level,
                    "storey_inferred": c.storey_inferred, "elevation": c.elevation,
                    "summary": c.summary, "x": round((c.x0 + c.x1) / 2, 1),
                    "y": round((c.y0 + c.y1) / 2, 1), "confidence": c.confidence,
                    "lines": c.lines} for c in saved], fh, ensure_ascii=False, indent=2)
    print(f"wrote {len(saved)} saved results -> {args.json}")
    if args.all_cells_json:
        with open(args.all_cells_json, "w", encoding="utf-8") as fh:
            json.dump([asdict(c) for c in cells], fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
