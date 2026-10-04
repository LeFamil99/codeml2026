"""CLP mat-foundation (radier) DA: grid bubbles -> spec lines -> rang circle -> bar stroke.

Reads rendered pixels only. The PDF text layer is used ONLY by ``--check``.
Run: PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.radier_clp FILE.pdf
Every page is read. A sheet holds several plan views, each with its own grid; the
elevations between them carry a single bubble strip and are skipped.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from l2c.da.common import parse_bar_line
from l2c.da.imageread import PageImage, TextLine, remove_rules
from l2c.da.parsers import colonne_clp as ocr
from l2c.da.parsers.dalle_clp import Bubble, LETTER, NUMBER, fold, read_region
from l2c.model import Armature, Debug, ElementRecord

log = logging.getLogger("radier_clp")
ZOOM = 2.0
BAR_ZOOM = 3.0
SPEC_START = 4.0        # points from a ring's centre to the first letter of its spec
SPEC_LENGTH = 125.0     # longest spec measured: 104 points
RING = 7.5              # points: outer diameter of a rang ring
RANG_R = 2.6            # points: the digit inside a rang ring, ring excluded
DEFAULT_FILE = os.path.expanduser(
    "~/Downloads/l2c-participants/CLP/DA/Fondations/CLP_RADIERS.pdf")
# "TRAN: 24 30M 30RU19-09 @11"BAS", "LONG: 2x22 25M 25RL22-00 @8"HAUT",
# "L: 5 25RU8-04 @8"HAUT". Read from the rang ring onward, so no ring in the text.
SPEC = re.compile(r"^\W*(?P<label>TRAN|L[O0]NG|L)\s*[:;.][\s.,]*"
                  r"(?:(?P<sets>\d+)\s*[xX×]\s*)?(?P<body>\d.*)$")
# A spec ENDS with its face; whatever the crop holds beyond it belongs to something else.
# Only two words exist, so a clipped last letter is still unambiguous.
FACE = re.compile(r"HAU[T1I]?|BA[S5]")
# The spacing token directly before the face. Bounded OCR repairs: '@' read as a, 0 or
# a copyright sign (a spacing never has a leading zero), a lost or altered inch mark.
SPACING = re.compile(r"(?:^|\s|(?=@))(?P<at>[@a©®]|0(?=[1-9]))?\s*(?P<sp>[1-9]\d?)\s*"
                     r"(?P<inch>[.,]?(?:\"|''|'|”|“|°|\*))?\s*$")
# "44 25 25RU24-10": the M of the size lost, the mark repeating the same size.
LOST_M = re.compile(r"^(\d+) (10|15|20|25|30|35|45|55) (?=\2[A-Z])")
SPEC_LIKE = re.compile(r"TRAN|L[O0]NG|\d{2}M\b|@\d|HAUT|BAS")
TITLE = re.compile(r"RADIER\s*#?\s*(\d+)")
# Radier bars are bent bars whose mark ends in a length: 30RU19-09, 25SU16-06.
MARK = re.compile(r"(\d{2})[A-Z]{1,3}\d{1,2}-\d{2}")
LENGTH = re.compile(r"\d{1,3}-\d{2}")


@dataclass
class View:
    id: str
    bbox: tuple[float, float, float, float]
    letters: dict[str, float]
    numbers: dict[str, float]
    radier: str | None = None
    warnings: list[str] = field(default_factory=list)

    def locate(self, x: float, y: float) -> str:
        letter = min(self.letters, key=lambda k: abs(self.letters[k] - y))
        number = min(self.numbers, key=lambda k: abs(self.numbers[k] - x))
        return f"{letter}-{number}"

    def span(self, a: float, b: float, vertical: bool) -> list[str]:
        """Axes crossed by a bar running from ``a`` to ``b``, in drawing order."""
        axes = self.letters if vertical else self.numbers
        return [k for k, v in sorted(axes.items(), key=lambda kv: kv[1]) if a - 4 <= v <= b + 4]


@dataclass
class Spec:
    page: int
    view: str
    radier: str | None
    coordinate: str
    direction: str                      # "horizontal" | "vertical", as drawn
    raw: str
    text_bbox: tuple[float, float, float, float]
    label: str | None = None
    rang: int | None = None
    rang_source: str | None = None      # "circle" | "view" (inferred, see parse_page)
    face: str | None = None             # HAUT / BAS
    sets: int | None = None             # the "2x" of "2x22": lapped bars per position
    quantite: int | None = None
    diametre: str | None = None
    espacement_mm: float | None = None
    repere: str | None = None
    longueur_mm: float | None = None
    bar_bbox: tuple[float, float, float, float] | None = None
    span: list[str] = field(default_factory=list)
    status: str = "unread"
    issues: list[str] = field(default_factory=list)     # what could not be read
    notes: list[str] = field(default_factory=list)      # read, but worth a look
    confidence: float = 0.0
    oracle: str | None = None
    oracle_bbox: tuple[float, float, float, float] | None = None
    check_equal: bool | None = None


@dataclass
class PageResult:
    fichier: str
    page: int
    views: list[View]
    specs: list[Spec]
    unread: list[dict]
    warnings: list[str]
    seconds: float


def grid_bubbles(src: PageImage, gray: np.ndarray, zoom: float = ZOOM) -> list[Bubble]:
    """Grid bubbles by Hough transform, then their labels.

    A contour test loses every bubble whose label touches the outline (``14.4``,
    ``12.7``): the circle is then no longer a closed round shape. A Hough vote is not
    affected by what is written inside.
    """
    found = cv2.HoughCircles(cv2.GaussianBlur(gray, (3, 3), 0), cv2.HOUGH_GRADIENT, dp=1,
                             minDist=15 * zoom, param1=120, param2=30,
                             minRadius=round(10 * zoom), maxRadius=round(12.5 * zoom))
    if found is None:
        return []
    radius = float(np.median(found[0][:, 2])) / zoom
    out = []
    for x, y, r in found[0] / zoom:
        r = min(float(r), radius)
        img = src.render(pymupdf.Rect(x - r, y - 0.6 * r, x + r, y + 0.6 * r), 10.0).copy()
        # Whiten the outline: its curved ends otherwise become brackets ("(11").
        yy, xx = np.ogrid[:img.shape[0], :img.shape[1]]
        img[(xx - img.shape[1] / 2)**2 + (yy - img.shape[0] / 2)**2 > (r * 10 * 0.88)**2] = 255
        result = ocr._ocr()(img, use_det=False, use_cls=False, use_rec=True)
        texts, scores = getattr(result, "txts", None), getattr(result, "scores", None)
        text = unicodedata.normalize("NFKC", str(texts[0])).upper().replace(" ", "") if texts else ""
        text = re.sub(r"^[^0-9A-Z|]+|[^0-9A-Z|']+$", "", text)
        b = Bubble(float(x), float(y), 2 * r)
        if (LETTER.fullmatch(text) or NUMBER.fullmatch(text) or text == "|") and scores[0] >= 0.5:
            b.label, b.confidence = text, float(scores[0])
        out.append(b)
    return sorted(out, key=lambda b: (b.y, b.x))


def _rows(bubbles: list[Bubble], tol: float = 3.0) -> list[list[Bubble]]:
    rows: list[list[Bubble]] = []
    for b in sorted(bubbles, key=lambda b: b.y):
        if rows and abs(b.y - rows[-1][-1].y) <= tol:
            rows[-1].append(b)
        else:
            rows.append([b])
    return rows


def build_views(bubbles: list[Bubble]) -> list[View]:
    """A plan view = two numeric bubble strips, top and bottom, with the same labels at
    the same x, plus the letter bubbles between them.

    Radier views are small: two columns (7.3, 7) or a single row letter (I) are valid,
    and a row may be labelled on one side only. An elevation has one strip and no twin.
    """
    def number(b):          # in a numeric strip, a lone stroke is the digit one
        return "1" if b.label in ("I", "|") else b.label

    strips = []
    for row in _rows([b for b in bubbles if b.label and NUMBER.fullmatch(number(b))]):
        row.sort(key=lambda b: b.x)     # two views side by side share a bubble height
        strips.append([row[0]])
        for b in row[1:]:
            strips[-1].append(b) if b.x - strips[-1][-1].x < 350 else strips.append([b])
    strips = [s for s in strips if len({number(b) for b in s}) >= 2
              and sum(b.label not in ("I", "|") for b in s) >= 1]
    views, used, numeric = [], set(), set()
    for i, top in enumerate(strips):
        if i in used:
            continue
        for j in range(i + 1, len(strips)):
            bottom = strips[j]
            if j in used or bottom[0].y - top[0].y < 80:
                continue
            pairs = [(a, b) for a in top for b in bottom
                     if number(a) == number(b) and abs(a.x - b.x) < 8]
            # The twin strip repeats the same columns, though either side may add an
            # intermediate axis; another view's strip further down shares far fewer.
            if len(pairs) >= 2 and len(pairs) >= 0.6 * max(len(top), len(bottom)):
                used.update((i, j))
                numeric.update(id(b) for b in top + bottom)
                views.append(View(f"view-{len(views) + 1}",
                                  (min(a.x for a, _ in pairs), top[0].y,
                                   max(a.x for a, _ in pairs), bottom[0].y), {},
                                  {number(a): (a.x + b.x) / 2 for a, b in pairs}))
                break
    for b in bubbles:
        if id(b) in numeric or not b.label:
            continue
        label = {"0": "O", "1": "I", "|": "I"}.get(b.label, b.label)
        if not LETTER.fullmatch(label):
            continue
        inside = [v for v in views if v.bbox[1] < b.y < v.bbox[3]]
        if not inside:
            continue
        # Distance to the view's COLUMNS: its bbox grows as letters are added.
        gap = lambda v: max(min(v.numbers.values()) - b.x, b.x - max(v.numbers.values()), 0)
        v = min(inside, key=gap)
        if gap(v) > 250:
            continue
        if label in v.letters and abs(v.letters[label] - b.y) > 4:
            v.warnings.append(f"row {label} labelled at two heights; first kept")
            continue
        v.letters[label] = (v.letters[label] + b.y) / 2 if label in v.letters else b.y
        v.bbox = (min(v.bbox[0], b.x), v.bbox[1], max(v.bbox[2], b.x), v.bbox[3])
    return [v for v in views if v.letters]


def _annulus(inner: float, outer: float, size: int) -> np.ndarray:
    yy, xx = np.ogrid[:size, :size]
    radius = np.hypot(xx - size // 2, yy - size // 2)
    return ((radius >= inner) & (radius <= outer)).astype(np.float32)


def rang_circles(src: PageImage, box: pymupdf.Rect, zoom: float = 6.0):
    """Rang rings as (x, y, diameter) in page points: (clean, crossed).

    CLP draws the ring as a ragged hatched band, 3.4 to 4.1 points from its centre,
    around an empty gap that separates it from the digit. Both are measured by
    template correlation: how much of the band is inked, how much of the gap.
    ``clean`` rings have an empty gap. ``crossed`` rings have a bar, a revision cloud
    or a wall fill running through them; those looser tests also pass non-rings, which
    the caller filters by position and grammar.
    """
    gray = cv2.cvtColor(src.render(box, zoom), cv2.COLOR_RGB2GRAY)
    ink = (gray < 170).astype(np.float32)
    size = 2 * round(4.4 * zoom) + 1
    band, gap = _annulus(3.4 * zoom, 4.1 * zoom, size), _annulus(2.25 * zoom, 2.9 * zoom, size)
    if min(ink.shape) <= size:
        return [], []
    covered = cv2.matchTemplate(ink, band, cv2.TM_CCORR) / band.sum()
    filled = cv2.matchTemplate(ink, gap, cv2.TM_CCORR) / gap.sum()

    def peaks(limit: float) -> list[tuple[float, float, float]]:
        score = np.where(filled <= limit, covered, 0).astype(np.float32)
        out = []
        while True:
            _, value, _, (px, py) = cv2.minMaxLoc(score)
            if value < 0.6:
                return out
            out.append((box.x0 + (px + size // 2) / zoom, box.y0 + (py + size // 2) / zoom, RING))
            cv2.circle(score, (px, py), round(4 * zoom), 0, -1)

    clean = peaks(0.15)
    crossed = peaks(0.45)
    # A ring over a wall's grey fill has no empty gap at all. A Hough vote ignores
    # what is inside a circle, at the price of voting for every O, 0, 8 and @.
    found = cv2.HoughCircles(cv2.GaussianBlur(gray, (5, 5), 0), cv2.HOUGH_GRADIENT, dp=1,
                             minDist=6 * zoom, param1=120, param2=18,
                             minRadius=round(2.2 * zoom), maxRadius=round(4.2 * zoom))
    if found is not None:
        crossed += [(box.x0 + x / zoom, box.y0 + y / zoom, RING) for x, y, _ in found[0]]
    loose = []
    for c in crossed:
        if not any(math.hypot(c[0] - e[0], c[1] - e[1]) < 3 for e in clean + loose):
            loose.append(c)
    return clean, loose


def read_rang(src: PageImage, circle, vertical: bool) -> tuple[int | None, float]:
    x, y, _ = circle
    img = src.render(pymupdf.Rect(x - RANG_R, y - RANG_R, x + RANG_R, y + RANG_R), 20.0).copy()
    yy, xx = np.ogrid[:img.shape[0], :img.shape[1]]
    img[(xx - img.shape[1] / 2)**2 + (yy - img.shape[0] / 2)**2 > (RANG_R * 20 * 0.96)**2] = 255
    if vertical:                        # vertical specs read bottom-to-top
        img = np.rot90(img, -1)
    img = cv2.copyMakeBorder(np.ascontiguousarray(img), 30, 30, 30, 30,
                             cv2.BORDER_CONSTANT, value=(255, 255, 255))
    r = ocr._ocr()(img, use_det=False, use_cls=False, use_rec=True)
    texts, scores = getattr(r, "txts", None), getattr(r, "scores", None)
    text = str(texts[0]).strip() if texts else ""
    # One digit, 1-4. A ring fragment or a crossing bar reads as a leading stroke.
    text = {"I": "1", "l": "1", "|": "1", "T": "1", "Z": "2", "B": "3", "A": "4"}.get(text, text)
    m = re.fullmatch(r"[1Il|]?([1-4])", text)
    return (int(m.group(1)), float(scores[0])) if m else (None, 0.0)


def bar_strokes(src: PageImage, view: View, zoom: float = BAR_ZOOM):
    """Heavy long strokes inside a view: (vertical, across, start, end, thickness) in
    points. Bars are drawn much heavier than grid, outline and dimension lines."""
    box = pymupdf.Rect(view.bbox) + (-10, -10, 10, 10)
    gray = cv2.cvtColor(src.render(box, zoom), cv2.COLOR_RGB2GRAY)
    ink = (gray < 110).astype(np.uint8)
    heavy = cv2.morphologyEx(ink, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    out = []
    for vertical in (False, True):
        length = int(30 * zoom)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, length) if vertical else (length, 1))
        n, _, stats, _ = cv2.connectedComponentsWithStats(
            cv2.morphologyEx(heavy, cv2.MORPH_OPEN, kernel), connectivity=8)
        for x, y, w, h, _ in stats[1:]:
            thick = w if vertical else h
            if thick / zoom > 5:        # a filled wall or column, not a bar
                continue
            if vertical:
                out.append((True, box.x0 + (x + w / 2) / zoom, box.y0 + y / zoom,
                            box.y0 + (y + h) / zoom, thick / zoom))
            else:
                out.append((False, box.y0 + (y + h / 2) / zoom, box.x0 + x / zoom,
                            box.x0 + (x + w) / zoom, thick / zoom))
    return out


def bar_for(line: TextLine, strokes) -> tuple | None:
    """The stroke on the baseline side of a spec: below horizontal text, to the right of
    vertical (bottom-to-top) text, overlapping it along its length."""
    best = None
    for vertical, across, start, end, _ in strokes:
        if vertical != line.vertical:
            continue
        a0, a1 = (line.y0, line.y1) if vertical else (line.x0, line.x1)
        if min(a1, end) - max(a0, start) < 0.5 * (a1 - a0):
            continue
        gap = across - (line.x1 if vertical else line.y1)
        if -5 <= gap <= 9 and (best is None or gap < best[0]):
            best = (gap, (vertical, across, start, end))
    return best[1] if best else None


def _rank(read) -> tuple:
    line, fields = read
    return fields is not None, -len(fields["issues"]) if fields else 0, line.confidence


def read_anchor(src: PageImage, circle, vertical: bool):
    """(text line, fields, ring) for the spec starting at a ring, or None.

    A ring's centre can be a point or two off, more where a bar crosses it, and the
    text may start right against the ring. Where the first read looks like a spec
    without being a complete one, small shifts across the text and an earlier start
    are tried, and the most complete read is kept. The shifts are far smaller than
    the 16 points between the two specs of a pair.
    """
    best = None
    shifts = [(across, along) for along in (0.0, 2.5) for across in (0.0, 1.5, -1.5, 3.0, -3.0)]
    for i, (across, along) in enumerate(shifts):
        x, y, d = circle
        ring = (x + across, y + along, d) if vertical else (x - along, y + across, d)
        read = read_spec(src, ring, vertical)
        if read is not None and (best is None or _rank(read) > _rank(best[:2])):
            best = (*read, ring)
        if read is not None and read[1] and not read[1]["issues"] and read[0].confidence >= 0.9:
            break
        if i == 0 and not (read is not None and SPEC_LIKE.search(read[0].text)):
            break
    return best


def parse_spec(text: str) -> dict | None:
    """One spec line -> fields. Unknown fields stay None; nothing is guessed."""
    t = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).strip())
    m = SPEC.match(t)
    if not m:
        return None
    body = m.group("body")
    face = FACE.search(body)
    head = body[:face.start()] if face else body
    spacing = SPACING.search(head)
    core = (head[:spacing.start()] if spacing else head).strip()
    core, lost_m = LOST_M.subn(r"\1 \2M ", core)
    core, s_for_5 = re.subn(r"(?<=\s\d{2})5(?=[A-Z]\d{1,2}-\d{2}$)", "S", core)
    lost_m += s_for_5
    tokens = core.split()
    # "9 3QM 30RU19-09": a size that is no designator, beside a mark that states one.
    if len(tokens) == 3 and not re.fullmatch(r"\d{2}M", tokens[1]) and MARK.fullmatch(tokens[2]) \
            and tokens[1][:1] == tokens[2][:1] and len(tokens[1]) <= 3:
        core, lost_m = f"{tokens[0]} {tokens[2][:2]}M {tokens[2]}", 1
    label = m.group("label").replace("0", "O")
    b = parse_bar_line(f"{label}: {core}", "imperial")
    if not b or b.armature.diametre is None or b.armature.quantite is None:
        return None
    issues = []
    a = b.armature
    sets = int(m.group("sets")) if m.group("sets") else None
    mark = a.repere
    # The last token AS WRITTEN must be a whole mark or length: "25RL16 06" and
    # "25RU21-0.0" must not pass as the marks 25RL16 and 25RU21-0.
    written = core.split()[-1] if len(core.split()) in (2, 3) else ""
    if not (MARK.fullmatch(written) or (LENGTH.fullmatch(written) and a.longueur_mm is not None)):
        issues.append(f"unread length or fabrication mark: {' '.join(core.split()[1:])}")
        mark, a.longueur_mm = None, None
    if not spacing:
        issues.append("spacing unread")
    if not face:
        issues.append("face (HAUT/BAS) unread")
    notes = []
    if mark and f"{MARK.fullmatch(mark).group(1)}M" != a.diametre:
        notes.append(f"mark {mark} carries a different size than {a.diametre}")
    repaired = (b.size_from_mark and label != "L") or "0" in m.group("label") or bool(lost_m) \
        or bool(face and face.group(0) not in ("HAUT", "BAS")) \
        or bool(spacing and (spacing.group("at") != "@" or spacing.group("inch") != '"'))
    return dict(label=b.label, sets=sets, quantite=a.quantite * (sets or 1),
                diametre=a.diametre, repere=mark, longueur_mm=a.longueur_mm,
                espacement_mm=round(int(spacing.group("sp")) * 25.4, 1) if spacing else None,
                face=("HAUT" if face.group(0)[0] == "H" else "BAS") if face else None,
                issues=issues, notes=notes,
                repaired=repaired)


def read_spec(src: PageImage, circle, vertical: bool) -> tuple[TextLine, dict | None] | None:
    """Read the text that starts at a rang ring: rightward, or upward for vertical text.

    The crop is anchored on the ring, so it never includes it and never starts midway
    through a line. A taller band is rendered first so that strokes CROSSING the text
    (grid lines, perpendicular bars) are long enough to be recognised as rules.
    """
    x, y, _ = circle
    z, half, margin = 10.0, 4.2, 9.0
    if vertical:
        band = pymupdf.Rect(x - margin, y - SPEC_START - SPEC_LENGTH, x + margin, y - SPEC_START)
    else:
        band = pymupdf.Rect(x + SPEC_START, y - margin, x + SPEC_START + SPEC_LENGTH, y + margin)
    if not pymupdf.Rect(0, 0, src.width, src.height).contains(band):
        return None
    raw = src.render(band, z)
    if vertical:
        raw = np.ascontiguousarray(np.rot90(raw, -1))
    rows = slice(round((margin - half) * z), round((margin + half) * z))
    if (raw[rows, :round(25 * z)] < 140).mean() < 0.02:
        return None                     # nothing written after this ring
    # Rules longer than a letter is tall: the bar under the text, lines crossing it.
    clean = remove_rules(raw, 7.0 * z / 2.5)
    dark = cv2.cvtColor(clean, cv2.COLOR_RGB2GRAY) < 140
    # A ring's centre sits a little off the text's own centre line. Take the rows that
    # carry ink at the start of the line and contain the ring's centre row.
    inked = dark[:, :round(60 * z)].mean(axis=1) > 0.03
    top = bottom = dark.shape[0] // 2
    while top > 0 and inked[top - 1]:
        top -= 1
    while bottom < len(inked) - 1 and inked[bottom + 1]:
        bottom += 1
    if inked[dark.shape[0] // 2] and 3.5 * z <= bottom - top <= 7.5 * z:
        pad = round(1.8 * z)
        rows = slice(max(0, top - pad), min(dark.shape[0], bottom + pad))
    # The text ends at the first wide blank; a spec is never shorter than ~40 points.
    ink = dark[rows].any(axis=0)
    end, blank = len(ink), 0
    for i in range(len(ink)):
        blank = 0 if ink[i] else blank + 1
        if blank >= 9 * z and i > 40 * z:
            end = i - blank + round(2 * z)
            break
    if vertical:
        box = pymupdf.Rect(band.x0 + rows.start / z, band.y1 - end / z, band.x0 + rows.stop / z, band.y1)
    else:
        box = pymupdf.Rect(band.x0, band.y0 + rows.start / z, band.x0 + end / z, band.y0 + rows.stop / z)

    def recognise(img: np.ndarray, trust: float = 1.0):
        r = ocr._ocr()(np.ascontiguousarray(img), use_det=False, use_cls=False, use_rec=True)
        texts, scores = getattr(r, "txts", None), getattr(r, "scores", None)
        text = str(texts[0]).strip() if texts else ""
        return (text, float(scores[0]) * trust, parse_spec(text)) if text else None

    def render(zoom: float) -> np.ndarray:
        img = src.render(box, zoom)
        return np.rot90(img, -1) if vertical else img

    first = recognise(raw[rows, :end])
    if first is None:
        return None
    if not SPEC_LIKE.search(first[0]):
        return TextLine(*box, first[0], first[1], vertical), None
    # One recognition of a line crossed by drawing strokes is right about 95 % of the
    # time (measured here: a dashed grid line through "16" read as "1", at full
    # confidence). The same crop is therefore read at three resolutions and the
    # readings must agree; the rule-erased crop only breaks a three-way split,
    # because erasing a line along a digit's stem erases the digit too.
    reads = [first] + [r for r in (recognise(render(8.0)), recognise(render(12.0))) if r]
    key = lambda read: spec_key(read[2]) if read[2] else None
    votes = {k: [r for r in reads if key(r) == k] for k in {key(r) for r in reads} if k}
    if not any(len(v) >= 2 for v in votes.values()):
        if extra := recognise(clean[rows, :end], 0.9):
            reads.append(extra)
            votes = {k: [r for r in reads if key(r) == k] for k in {key(r) for r in reads} if k}
    if not votes:
        text, confidence, _ = max(reads, key=lambda r: r[1])
        return TextLine(*box, text, confidence, vertical), None
    # Most votes first; then the fewest unread fields; then the surest reading.
    winners = max(votes.values(), key=lambda v: (len(v), -len(v[0][2]["issues"]), max(r[1] for r in v)))
    text, confidence, fields = max(winners, key=lambda r: r[1])
    if len(winners) < 2:
        fields["notes"].append("single reading: the other readings of this line differ or failed")
        confidence *= 0.6
    return TextLine(*box, text, confidence, vertical), fields


def _oracle(page: pymupdf.Page, rect: pymupdf.Rect) -> list[TextLine]:
    """Validation ONLY. Called after the image result is final, never used for parsing."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            box = pymupdf.Rect(line["bbox"])
            if rect.contains(pymupdf.Point((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2)):
                text = " ".join(s["text"] for s in line["spans"]).strip()
                out.append(TextLine(*box, text, 1.0, abs(line["dir"][1]) > 0.5))
    return out


def _overlap(a, b) -> float:
    """Shared area of two boxes; zero when they are apart."""
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def _area(a) -> float:
    return (a[2] - a[0]) * (a[3] - a[1])


def spec_key(d: dict | Spec):
    get = d.get if isinstance(d, dict) else lambda k: getattr(d, k)
    return (get("label"), get("sets"), get("quantite"), get("diametre"), get("espacement_mm"),
            fold(get("repere") or ""), get("longueur_mm"), get("face"))


def parse_page(page: pymupdf.Page, filename: str = "", check: bool = False) -> PageResult:
    started = time.time()
    if page.rotation:
        page.remove_rotation()
    src = PageImage(page)
    full = pymupdf.Rect(0, 0, src.width, src.height)
    gray = cv2.cvtColor(src.render(full, ZOOM), cv2.COLOR_RGB2GRAY)
    bubbles = grid_bubbles(src, gray)
    views = build_views(bubbles)
    log.info("page %d: %d bubble candidates, %d plan views", page.number + 1, len(bubbles), len(views))
    warnings = [f"{v.id}: {w}" for v in views for w in v.warnings]
    if not views:
        warnings.append("grid unread: no twin numeric strips with a row letter")
    specs, unread = [], []
    for v in views:
        box = pymupdf.Rect(v.bbox)
        below = pymupdf.Rect(box.x0 - 40, box.y1 + 8, box.x1 + 40, box.y1 + 190) & full
        titles = [(l.y0, m.group(1)) for l in read_region(src, below)
                  if (m := TITLE.search(fold(l.text)))]
        if titles:
            v.radier = f"RADIER #{min(titles)[1]}"
        else:
            warnings.append(f"{v.id}: radier title unread")
        log.info("%s %s: rows %s, columns %s", v.id, v.radier, sorted(v.letters), sorted(v.numbers, key=float))
        strokes = bar_strokes(src, v)
        exact, crossed = rang_circles(src, box)
        first = len(specs)
        truth = [l for l in _oracle(page, box) if parse_spec(l.text)] if check else []
        # Every spec starts at a rang ring. Clean rings are tried in both directions.
        # The crossed-ring test is looser, so such a ring is only tried off text
        # already read and where a bar stroke runs beside it.
        def beside_bar(c, vertical: bool) -> bool:
            probe = TextLine(c[0] - 4, c[1] - 34, c[0] + 4, c[1] - 4, "", 0, True) if vertical \
                else TextLine(c[0] + 4, c[1] - 4, c[0] + 34, c[1] + 4, "", 0, False)
            return bar_for(probe, strokes) is not None

        anchors = []
        for c in exact:
            ways = [vertical for vertical in (False, True) if beside_bar(c, vertical)]
            anchors += [(c, vertical, False) for vertical in ways or (False, True)]
        anchors += [(c, vertical, True) for c in crossed for vertical in (False, True)]
        for circle, vertical, loose in anchors:
            if loose:
                point = pymupdf.Point(circle[0], circle[1])
                if any((pymupdf.Rect(o.text_bbox) + (-2, -2, 2, 2)).contains(point) for o in specs[first:]):
                    continue
                if not beside_bar(circle, vertical):
                    continue
            read = read_anchor(src, circle, vertical)
            if read is None:
                continue
            line, fields, shifted = read
            if not fields:
                if re.match(r"\W*(?:TRAN|L[O0]NG|L)\s*[:;.]", line.text):
                    unread.append({"view": v.id, "raw": line.text, "bbox": tuple(line.rect),
                                   "confidence": line.confidence,
                                   "reason": "spec label read but bar grammar incomplete"})
                continue
            # The same spec reached from two candidates: keep the more complete read.
            twin = next((o for o in specs[first:] if o.direction == ("vertical" if vertical else "horizontal")
                         and _overlap(o.text_bbox, tuple(line.rect))
                         > 0.3 * min(_area(o.text_bbox), _area(tuple(line.rect)))), None)
            if twin:
                if (len(twin.issues), -twin.confidence) <= (len(fields["issues"]), -line.confidence):
                    continue
                specs.remove(twin)
            issues, notes, repaired = fields.pop("issues"), fields.pop("notes"), fields.pop("repaired")
            s = Spec(page.number + 1, v.id, v.radier, v.locate(line.cx, line.cy),
                     "vertical" if line.vertical else "horizontal", line.text,
                     tuple(line.rect), issues=issues, notes=notes,
                     confidence=line.confidence, **fields)
            for ring in (circle, shifted):
                s.rang, score = read_rang(src, ring, line.vertical)
                if s.rang is not None:
                    s.rang_source = "circle"
                    s.confidence = min(s.confidence, score)
                    break
            if bar := bar_for(line, strokes):
                _, across, start, end = bar
                s.bar_bbox = (across, start, across, end) if line.vertical else (start, across, end, across)
                s.span = v.span(start, end, line.vertical)
            else:
                s.issues.append("no bar stroke found beside the spec")
            if repaired:
                s.confidence *= 0.9
            if check:
                near_truth = [t for t in truth if t.vertical == line.vertical
                              and _overlap(tuple(t.rect), tuple(line.rect)) > 0.4 * _area(tuple(t.rect))]
                if near_truth:
                    t = max(near_truth, key=lambda t: _overlap(tuple(t.rect), tuple(line.rect)))
                    s.oracle, s.oracle_bbox = t.text, tuple(t.rect)
                    s.check_equal = spec_key(parse_spec(t.text)) == spec_key(s)
                else:
                    s.check_equal = False
            specs.append(s)
        mine = specs[first:]
        unread[:] = [u for u in unread if u["view"] != v.id or not any(
            _overlap(o.text_bbox, u["bbox"]) > 0 for o in mine)]
        # Within one view a rang is one (direction, face): rangs 1 & 4 run one way,
        # 2 & 3 the other. An unread ring takes the view's own unanimous reading,
        # marked as inferred; a split or absent reading leaves it unknown.
        for s in mine:
            if s.rang is None and s.face:
                seen = {o.rang for o in mine if o.rang_source == "circle"
                        and (o.direction, o.face) == (s.direction, s.face)
                        and (o.rang in (1, 2)) == (o.face == "BAS")}
                if len(seen) == 1:
                    s.rang, s.rang_source = seen.pop(), "view"
                    s.confidence *= 0.9
            if s.rang is None:
                s.issues.append("rang circle unread")
            elif s.face and (s.rang in (1, 2)) != (s.face == "BAS"):
                s.notes.append(f"rang {s.rang} disagrees with face {s.face}")
            s.status = "partial" if s.issues else "read"
            if s.issues:
                s.confidence *= 0.5
            s.confidence = round(s.confidence, 3)
            log.info("%s %s %s: %s%s", v.id, s.coordinate, s.direction, summary(s),
                     f" [check={s.check_equal}]" if check else "")
        if check:
            for t in truth:
                if not any(s.oracle_bbox == tuple(t.rect) for s in mine):
                    unread.append({"view": v.id, "oracle": t.text, "bbox": tuple(t.rect),
                                   "reason": "spec present in the hidden text, not read from pixels"})
    specs.sort(key=lambda s: (s.view, round(s.text_bbox[1]), s.text_bbox[0]))
    return PageResult(filename, page.number + 1, views, specs, unread, warnings,
                      round(time.time() - started, 2))


def summary(s: Spec) -> str:
    """The plan's own radier grammar: ``RANG 2: 30M@11"``."""
    size = s.diametre + (f'@{s.espacement_mm / 25.4:g}"' if s.espacement_mm else "")
    return (f"RANG {s.rang}: " if s.rang else "") + size


def records(result: PageResult, filename: str) -> list[ElementRecord]:
    """Appendix-A records shaped like ``parse/radier.py``: one per spec, layer in debug."""
    out, seen = [], {}
    feuillet = f"{Path(filename).stem} p{result.page}"
    for s in export_specs(result):
        key = f"{s.view}_{s.coordinate}"
        seen[key] = seen.get(key, 0) + 1
        x, y = (s.text_bbox[0] + s.text_bbox[2]) / 2, (s.text_bbox[1] + s.text_bbox[3]) / 2
        out.append(ElementRecord(
            id=f"{feuillet}_{key}_{seen[key]}_atelier", source="atelier", fichier=filename,
            feuillet=feuillet, page=result.page, x=x, y=y, type_element="radier",
            element=s.coordinate,
            armature=[Armature(repere=s.repere, diametre=s.diametre, quantite=s.quantite,
                               espacement_mm=s.espacement_mm, longueur_mm=s.longueur_mm)],
            debug=Debug(raw=[s.raw] + ([f"RANG {s.rang}"] if s.rang else []),
                        confidence=s.confidence, decode_path="ocr", locator_kind="grid",
                        layer=str(s.rang) if s.rang else None, layer_from_legend=s.rang_source == "view",
                        direction=s.direction, face=s.face, view=s.view, radier=s.radier,
                        label=s.label, bar_bbox=s.bar_bbox, span=s.span,
                        issues=s.issues, notes=s.notes, status=s.status)))
    from ...record_formats import align_records
    return align_records(out)


def export_specs(result: PageResult) -> list[Spec]:
    """Drop empty/unlocated rows and exact repeats, retaining independent bar strokes."""
    selected = {}
    for spec in result.specs:
        if (not spec.coordinate or spec.coordinate == "UNKNOWN" or not spec.diametre):
            continue
        identity = (spec.view, spec.coordinate, spec.rang, spec.direction, spec.face,
                    tuple(spec.bar_bbox or spec.text_bbox), spec.diametre, spec.quantite,
                    spec.espacement_mm, spec.longueur_mm, spec.repere)
        if identity not in selected or selected[identity].confidence < spec.confidence:
            selected[identity] = spec
    return list(selected.values())


def clean_output(results: list[PageResult]) -> list[dict]:
    """Final review JSON: one object per spec, optional unknown fields omitted."""
    output = []
    for result in results:
        for s in export_specs(result):
            bar = dict(label=s.label, formatted=summary(s).split(": ")[-1], diametre=s.diametre,
                       espacement_mm=s.espacement_mm, quantite=s.quantite, sets=s.sets,
                       repere=s.repere, longueur_mm=s.longueur_mm, raw=s.raw)
            row = dict(fichier=result.fichier, page=s.page, view=s.view, radier=s.radier,
                       coordinate=s.coordinate, direction=s.direction, rang=s.rang,
                       rang_source=s.rang_source, face=s.face,
                       summary=summary(s),
                       reinforcement=[{k: v for k, v in bar.items() if v is not None}],
                       span=s.span or None, status=s.status, confidence=s.confidence,
                       issues=s.issues or None, notes=s.notes or None,
                       full_check=s.check_equal)
            output.append({k: v for k, v in row.items() if v is not None})
    return output


def annotate(page: pymupdf.Page, result: PageResult) -> None:
    shape = page.new_shape()
    for v in result.views:
        for y in v.letters.values():
            shape.draw_line((v.bbox[0], y), (v.bbox[2], y))
        for x in v.numbers.values():
            shape.draw_line((x, v.bbox[1]), (x, v.bbox[3]))
        shape.finish(color=(0, 0.6, 1), width=0.4)
        shape.insert_text((v.bbox[0], v.bbox[1] - 16), f"{v.id} {v.radier or ''}",
                          fontsize=8, color=(0, 0.6, 1))
    for s in result.specs:
        color = (0, 0.65, 0) if s.status == "read" else (1, 0.5, 0)
        shape.draw_rect(pymupdf.Rect(s.text_bbox))
        shape.finish(color=color, width=0.6)
        if s.bar_bbox:
            shape.draw_line(s.bar_bbox[:2], s.bar_bbox[2:])
            shape.finish(color=color, width=2.5, stroke_opacity=0.45)
        shape.insert_text((s.text_bbox[2] + 2, s.text_bbox[1] + 4),
                          f"{s.coordinate} {summary(s)}", fontsize=4, color=color)
    for u in result.unread:
        shape.draw_rect(pymupdf.Rect(u["bbox"]))
        shape.finish(color=(1, 0, 0), width=0.8)
    shape.commit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", default=DEFAULT_FILE)
    ap.add_argument("--page", type=int, help="1-based page; default every page")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--check", action="store_true", help="compare image read with hidden PDF text, validation only")
    ap.add_argument("--json", default="out/radier_clp.json", help="Appendix-A records")
    ap.add_argument("--diagnostics", default="out/radier_clp_diagnostics.json", help="views, every spec, unread candidates")
    ap.add_argument("--output-json", default="out/radier_clp_output.json", help="final clean review JSON")
    ap.add_argument("--annotated", help="review PDF path")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    ocr.OCR_THREADS = max(1, args.threads)
    ocr._ocr.cache_clear()
    cv2.setNumThreads(1)
    file = Path(args.file).expanduser()
    outputs = [args.json, args.diagnostics, args.output_json] + ([args.annotated] if args.annotated else [])
    if any(Path(p).resolve() == file.resolve() for p in outputs):
        ap.error("outputs must differ from the input PDF")
    results, all_records = [], []
    with pymupdf.open(file) as doc:
        pages = [doc[args.page - 1]] if args.page else list(doc)
        for page in pages:
            result = parse_page(page, file.name, args.check)
            results.append(result)
            all_records.extend(records(result, file.name))
        for path, data in ((args.json, [r.to_schema() for r in all_records]),
                           (args.diagnostics, [asdict(r) for r in results]),
                           (args.output_json, clean_output(results))):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if args.annotated:
            with pymupdf.open() as review:
                for result in results:
                    page = doc[result.page - 1]
                    annotate(page, result)
                    review.insert_pdf(doc, from_page=page.number, to_page=page.number)
                Path(args.annotated).parent.mkdir(parents=True, exist_ok=True)
                review.save(args.annotated)
    specs = [s for r in results for s in r.specs]
    print(f"{len(specs)} radier specs in {sum(len(r.views) for r in results)} views; "
          f"{sum(s.status == 'partial' for s in specs)} partial, "
          f"{sum(len(r.unread) for r in results)} unread; {sum(r.seconds for r in results):.1f}s")
    if args.check:
        print(f"check: {sum(bool(s.check_equal) for s in specs)}/{len(specs)} equal to the hidden text")
    for r in results:
        for w in r.warnings:
            print("warning:", w)
    print(f"records: {args.json}\ndiagnostics: {args.diagnostics}")
    print(f"FINAL OUTPUT: {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
