"""CLP slab DA: grid bubbles -> grey support rectangles -> local OCR callouts.

Reads rendered pixels only. The PDF text layer is used ONLY by ``--check``.
Run: PYTHONPATH=src .venv/bin/python -m l2c.da.parsers.dalle_clp FILE.pdf
Only the LAST page is read. A directory processes the last page of each PDF.
Use --coordinate J-15 for a small run and --annotated for a review PDF.
Unread supports remain in the output with a reason.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

import cv2
import numpy as np
import pymupdf

from l2c.da.common import parse_bar_line
from l2c.da.imageread import PageImage, TextLine, remove_rules, _join
from l2c.da.parsers import colonne_clp as ocr
from l2c.model import Debug, ElementRecord
from l2c.units import BAR_DESIGNATORS, parse_spacing
from l2c.da.parsers.output import (
    deduplicate, reinforcement_key, view_correspondences, grid_identity, grid_quality,
)

log = logging.getLogger("dalle_clp")
ZOOM = 2.0
DEFAULT_FILE = os.path.expanduser(
    "~/Downloads/l2c-participants/CLP/DA/Dalles/CLP_DALLE NIV 3.pdf")
LETTER = re.compile(r"^[A-Z]{1,2}(?:\.\d{1,2})?'?$")
NUMBER = re.compile(r"^\d{1,2}(?:\.\d{1,2})?$")
MARK = re.compile(r"\d{2}[A-Z]+\d[0-9A-Z]*(?:[-/][0-9A-Z]+)*")
SPACING = re.compile(r'^@\s*\d+(?:\s+\d/\d)?\s*(?:"|mm)?\s*$')


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text.upper())
                   if unicodedata.category(c) != "Mn")


@dataclass
class Bubble:
    x: float
    y: float
    diameter: float
    label: str | None = None
    confidence: float = 0.0


@dataclass
class View:
    id: str
    bbox: tuple[float, float, float, float]
    letters: dict[str, float]
    numbers: dict[str, float]
    warnings: list[str] = field(default_factory=list)

    def locate(self, rect: pymupdf.Rect) -> tuple[str, float] | None:
        if not pymupdf.Rect(self.bbox).contains(pymupdf.Point(*center(rect))):
            return None
        x, y = center(rect)
        letter = min(self.letters, key=lambda k: abs(self.letters[k] - y))
        number = min(self.numbers, key=lambda k: abs(self.numbers[k] - x))
        dx, dy = abs(self.numbers[number] - x), abs(self.letters[letter] - y)
        # Drafted supports can straddle an axis rather than being centred on it.
        if dx > rect.width / 2 + 8 or dy > rect.height / 2 + 8:
            return None
        return f"{letter}-{number}", math.hypot(dx, dy)


@dataclass
class Support:
    page: int
    view: str | None
    coordinate: str | None
    symbol_bbox: tuple[float, float, float, float]
    detection: str = "filled_rectangle"
    crop_bbox: tuple[float, float, float, float] | None = None
    status: str = "unread"
    level: str | None = None
    layer: str | None = None
    lines: list[str] = field(default_factory=list)
    text_boxes: list[dict] = field(default_factory=list)
    bars: list[dict] = field(default_factory=list)
    summary: str | None = None
    confidence: float = 0.0
    reason: str | None = None
    oracle: list[str] | None = None
    check_equal: bool | None = None
    check_count_size_equal: bool | None = None
    anchor: tuple[float, float] | None = None
    duplicate_conflict: bool = False


@dataclass
class PageResult:
    fichier: str
    page: int
    views: list[View]
    supports: list[Support]
    warnings: list[str]
    seconds: float
    deduplication: dict = field(default_factory=dict)


def center(rect):
    return (rect.x0 + rect.x1) / 2, (rect.y0 + rect.y1) / 2


def circle_candidates(gray: np.ndarray, zoom: float = ZOOM) -> list[Bubble]:
    contours, _ = cv2.findContours((gray < 180).astype(np.uint8) * 255,
                                    cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if not (12 <= w / zoom <= 32 and 0.85 < w / h < 1.18):
            continue
        area, perimeter = cv2.contourArea(c), cv2.arcLength(c, True)
        if not perimeter or 4 * math.pi * area / perimeter**2 < 0.75:
            continue
        if not 0.65 < area / (w * h) < 0.85:
            continue
        b = Bubble((x + w / 2) / zoom, (y + h / 2) / zoom, max(w, h) / zoom)
        candidates.append(b)
    out = []
    for b in sorted(candidates, key=lambda b: -b.diameter):
        if not any(math.hypot(b.x - a.x, b.y - a.y) < 3 for a in out):
            out.append(b)
    return sorted(out, key=lambda b: (b.y, b.x))


def grey_rectangles(gray: np.ndarray, zoom: float = ZOOM) -> list[pymupdf.Rect]:
    # Open BEFORE close: otherwise antialiased letters are joined into false rectangles.
    mask = cv2.inRange(gray, 70, 210)
    size = max(3, round(2.5 * zoom))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((size, size), np.uint8))
    size = max(3, round(4.5 * zoom))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((size, size), np.uint8))
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = []
    for x, y, w, h, area in stats[1:]:
        if 5 <= w / zoom <= 80 and 5 <= h / zoom <= 80 and area / (w * h) >= 0.70:
            out.append(pymupdf.Rect(x / zoom, y / zoom, (x + w) / zoom, (y + h) / zoom))
    return out


def outlined_supports(gray: np.ndarray, views: list[View], filled: list[pymupdf.Rect],
                      zoom: float = ZOOM) -> list[pymupdf.Rect]:
    """Niveau 5's depot has a grey BACKGROUND and dashed column outlines.

    Look for four short rectangle sides near each grid crossing in that background.
    Exclude each side's middle so crossing grid/rebar strokes cannot invent a box.
    Ordinary white regions continue to use the filled-rectangle detector.
    """
    out = []
    dimensions = {(round(r.width * zoom), round(r.height * zoom)) for r in filled
                  if 6 < r.width < 22 and 6 < r.height < 22}
    dimensions.update({(round(w * zoom), round(h * zoom))
                       for w, h in ((8.5, 12.5), (12.5, 8.5), (9, 13), (13, 9))})
    ink = (gray < 100).astype(np.float32)
    for view in views:
        for y in view.letters.values():
            for x in view.numbers.values():
                if any(r.contains(pymupdf.Point(x, y)) for r in filled + out):
                    continue
                cx, cy = round(x * zoom), round(y * zoom)
                radius = round(16 * zoom)
                x0, y0 = max(0, cx - radius), max(0, cy - radius)
                patch = gray[y0:cy + radius + 1, x0:cx + radius + 1]
                if patch.size == 0 or np.mean((patch > 70) & (patch < 220)) < 0.70:
                    continue
                dark = ink[y0:cy + radius + 1, x0:cx + radius + 1]
                best = None
                for w, h in sorted(dimensions):
                    if w >= dark.shape[1] or h >= dark.shape[0]:
                        continue
                    masks = []
                    for side in range(4):
                        mask = np.zeros((h, w), np.float32)
                        if side < 2:
                            mask[0 if side == 0 else -1, :] = 1
                            mask[:, w // 2 - 3:w // 2 + 4] = 0
                        else:
                            mask[:, 0 if side == 2 else -1] = 1
                            mask[h // 2 - 3:h // 2 + 4, :] = 0
                        masks.append(cv2.matchTemplate(dark, mask, cv2.TM_CCORR) / mask.sum())
                    score = np.minimum.reduce(masks)
                    _, value, _, (px, py) = cv2.minMaxLoc(score)
                    candidate = pymupdf.Rect((x0 + px) / zoom, (y0 + py) / zoom,
                                            (x0 + px + w) / zoom, (y0 + py + h) / zoom)
                    bx, by = center(candidate)
                    if value >= 0.35 and abs(bx - x) < 5 and abs(by - y) < 5:
                        if best is None or value > best[0]:
                            best = (value, candidate)
                if best:
                    out.append(best[1])
    return out


def read_bubbles(src: PageImage, bubbles: list[Bubble]) -> None:
    for b in bubbles:
        # Keep the centre of the circle, excluding its outline. Label text is tiny;
        # its own high-resolution crop is cheap, unlike OCR of the entire plan.
        rx, ry = b.diameter * 0.43, b.diameter * 0.28
        img = src.render(pymupdf.Rect(b.x - rx, b.y - ry, b.x + rx, b.y + ry), 10.0)
        # Whitening the curved ends prevents circle fragments becoming extra glyphs.
        yy, xx = np.ogrid[:img.shape[0], :img.shape[1]]
        outside = (xx - img.shape[1] / 2)**2 + (yy - img.shape[0] / 2)**2 > (b.diameter * 10 * 0.45)**2
        img = img.copy()
        img[outside] = 255
        result = ocr._ocr()(img, use_det=False, use_cls=False, use_rec=True)
        texts, scores = getattr(result, "txts", None), getattr(result, "scores", None)
        text = unicodedata.normalize("NFKC", str(texts[0])).strip().upper().replace(" ", "") if texts else ""
        text = text.rstrip(",;:")
        if (LETTER.fullmatch(text) or NUMBER.fullmatch(text) or text == "|") and scores[0] >= 0.5:
            b.label, b.confidence = text, float(scores[0])
        log.debug("bubble (%.1f, %.1f): %r", b.x, b.y, text)


def _groups(bubbles: list[Bubble], axis: str, tol: float = 3.0):
    groups: list[list[Bubble]] = []
    for b in sorted(bubbles, key=lambda b: getattr(b, axis)):
        value = getattr(b, axis)
        if groups and abs(value - np.mean([getattr(a, axis) for a in groups[-1]])) <= tol:
            groups[-1].append(b)
        else:
            groups.append([b])
    return [g for g in groups if len({b.label for b in g}) >= 3]


def build_views(bubbles: list[Bubble]) -> list[View]:
    """Opposite numeric strips define a view; letter bubbles identify its rows.

    CLP letters are rows. Repeated labels in inset details remain independent.
    Conflicting opposite labels are reported and excluded, never guessed from neighbours.
    """
    # OCR cannot distinguish I/1 or O/0 reliably. The axis STRIP identifies the
    # vocabulary: only repair those shapes when at least three letter labels agree.
    for group in _groups([b for b in bubbles if b.label], "x"):
        if sum(bool(LETTER.fullmatch(b.label)) for b in group) >= 3:
            for b in group:
                if b.label in ("0", "1", "|"):
                    b.label = {"0": "O", "1": "I", "|": "I"}[b.label]
    number_groups = _groups([b for b in bubbles if b.label and NUMBER.fullmatch(b.label)], "y")
    letters = [b for b in bubbles if b.label and LETTER.fullmatch(b.label)]
    views, used = [], set()
    for i, top in enumerate(number_groups):
        if i in used:
            continue
        top_axis = {b.label: b.x for b in top}
        pairs = []
        for j in range(i + 1, len(number_groups)):
            if j in used:
                continue
            bottom = number_groups[j]
            if bottom[0].y - top[0].y < 80:
                continue
            bottom_axis = {b.label: b.x for b in bottom}
            common = [k for k in top_axis if k in bottom_axis and abs(top_axis[k] - bottom_axis[k]) < 8]
            if len(common) >= 3:
                pairs.append((len(common), j, bottom))
        if not pairs:
            continue
        _, j, bottom = max(pairs, key=lambda p: (p[0], -p[1]))
        bottom_axis = {b.label: b.x for b in bottom}
        numbers, warnings = {}, []
        for key in sorted(top_axis.keys() | bottom_axis.keys()):
            vals = [a[key] for a in (top_axis, bottom_axis) if key in a]
            if max(vals) - min(vals) > 8:
                warnings.append(f"conflicting column {key}: {vals}")
            else:
                numbers[key] = sum(vals) / len(vals)
        if len(numbers) < 3:
            continue
        nx0, nx1 = min(numbers.values()), max(numbers.values())
        y0, y1 = float(np.mean([b.y for b in top])), float(np.mean([b.y for b in bottom]))
        # Bubble strips can step sideways midway down a page (CLP RDC). Pair by
        # position and label inside this viewport rather than requiring one fixed x.
        left = [b for b in letters if nx0 - 260 < b.x < nx0 - 10 and y0 < b.y < y1]
        right = [b for b in letters if nx1 + 10 < b.x < nx1 + 260 and y0 < b.y < y1]
        right_groups = _groups(right, "x", tol=5)
        if not right_groups:
            continue
        right = max(right_groups, key=lambda g: sum(any(abs(b.y - a.y) < 4 for a in left) for b in g))
        # A nearby inset's letter strip is not part of the main view. Left strips may
        # legitimately step sideways; retain those whose rows align with this right.
        left_groups = _groups(left, "x", tol=5)
        left = [b for g in left_groups
                if sum(any(abs(b.y - a.y) < 4 for a in right) for b in g) >= 3 for b in g]
        rows = {}
        # Cluster individual row positions without imposing that axis-strip minimum.
        groups = []
        for bubble in sorted(left + right, key=lambda b: b.y):
            if groups and abs(bubble.y - groups[-1][0].y) < 4:
                groups[-1].append(bubble)
            else:
                groups.append([bubble])
        for index, group in enumerate(groups):
            best = max(group, key=lambda b: b.confidence)
            labels = {b.label for b in group}
            if len(labels) > 1:
                # Choose only among the observed readings. Adjacent unambiguous
                # row letters can distinguish G/Q without inventing a new label.
                if 0 < index < len(groups) - 1:
                    before = {b.label for b in groups[index - 1]}
                    after = {b.label for b in groups[index + 1]}
                    if len(before) == len(after) == 1:
                        a, b = next(iter(before)), next(iter(after))
                        if len(a) == len(b) == 1 and abs(ord(a) - ord(b)) == 2:
                            expected = chr((ord(a) + ord(b)) // 2)
                            if expected in labels:
                                best = max((b for b in group if b.label == expected), key=lambda b: b.confidence)
                warnings.append(f"row label disagreement at y={best.y:.1f}: {sorted(labels)}; kept {best.label}")
            if best.label in rows and abs(rows[best.label] - best.y) > 4:
                warnings.append(f"repeated row {best.label} inside view; excluded")
                rows.pop(best.label)
                continue
            rows[best.label] = float(np.mean([b.y for b in group]))
        if len(rows) >= 3 and left and right:
            used.update((i, j))
            views.append(View(f"view-{len(views) + 1}",
                              (min(b.x for b in left), y0, max(b.x for b in right), y1),
                              rows, numbers, warnings))
    return views


def recognise_line(src: PageImage, line: TextLine) -> TextLine:
    thickness = line.x1 - line.x0 if line.vertical else line.y1 - line.y0
    zoom = min(24.0, max(4.0, 64 / max(thickness, 1)))
    pad = thickness * 0.25
    crop = (line.rect + (-pad, -pad, pad, pad)) & pymupdf.Rect(0, 0, src.width, src.height)
    raw = src.render(crop, zoom)
    clean = remove_rules(raw, thickness * zoom / 1.6)
    images = [raw, clean] if clean is not raw else [raw]
    if line.vertical:
        images = [np.ascontiguousarray(np.rot90(img, k)) for img in images for k in (-1, 1)]
    reads = []
    for img in images:
        r = ocr._ocr()(img, use_det=False, use_cls=False, use_rec=True)
        texts, scores = getattr(r, "txts", None), getattr(r, "scores", None)
        if texts:
            reads.append((str(texts[0]).strip(), float(scores[0])))
    if reads:
        # Prefer an intact bar grammar over a high-confidence truncated reading.
        # This uses the DA's vocabulary only, never a plan's expected values.
        def rank(read):
            text, confidence = read
            b = parse_bar_line(text, "imperial")
            complete = bool(b and (b.armature.longueur_mm is not None or
                                   (b.armature.repere and MARK.fullmatch(b.armature.repere))))
            return complete, confidence
        line.text, line.confidence = max(reads, key=rank)
    return line


def read_region(src: PageImage, rect: pymupdf.Rect, zoom: float = 4.0) -> list[TextLine]:
    r = ocr._ocr()(src.render(rect, zoom), use_det=True, use_cls=False, use_rec=False)
    boxes = getattr(r, "boxes", None)
    if boxes is None:
        return []
    found = []
    for poly in boxes:
        xs, ys = [rect.x0 + p[0] / zoom for p in poly], [rect.y0 + p[1] / zoom for p in poly]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        b = TextLine(float(x0), float(y0), float(x1), float(y1), "", 0.0,
                     bool((y1 - y0) > 1.5 * (x1 - x0)))
        read = recognise_line(src, b)
        if read.text and read.confidence >= 0.5:
            found.append(read)
    # A detector can split a label and its values; combine fragments on the same line.
    groups: list[list[TextLine]] = []
    for l in sorted(found, key=lambda l: (l.vertical, l.cy if not l.vertical else l.cx)):
        for group in groups:
            a = group[0]
            same_band = abs(l.cx - a.cx) < 0.4 * min(l.x1 - l.x0, a.x1 - a.x0) if l.vertical else \
                abs(l.cy - a.cy) < 0.4 * min(l.y1 - l.y0, a.y1 - a.y0)
            gap = max(l.y0 - max(b.y1 for b in group), min(b.y0 for b in group) - l.y1) if l.vertical else \
                max(l.x0 - max(b.x1 for b in group), min(b.x0 for b in group) - l.x1)
            if a.vertical == l.vertical and same_band and gap < 8:
                group.append(l)
                break
        else:
            groups.append([l])
    out = []
    for g in groups:
        g.sort(key=lambda l: -l.y1 if l.vertical else l.x0)
        out.append(TextLine(min(l.x0 for l in g), min(l.y0 for l in g), max(l.x1 for l in g),
                            max(l.y1 for l in g), " ".join(l.text for l in g),
                            min(l.confidence for l in g), g[0].vertical))
    return sorted(out, key=lambda l: (l.y0, l.x0))


def metadata(src: PageImage, filename: str) -> tuple[str | None, str | None]:
    # Drawing title gives layer and level; filename supplies a level fallback only.
    box = pymupdf.Rect(src.width * 0.85, src.height * 0.87, src.width, src.height * 0.99)
    text = fold(" ".join(l.text for l in read_region(src, box)))
    layer = "intégrité" if "INTEGRITE" in text else "haut" if "HAUT" in text else "bas" if "BAS" in text else None
    m = re.search(r"NIVEAU\s*(\d+)", text)
    if m:
        level = f"NIVEAU {m.group(1)}"
    # A TRÉFOND sheet also prints "ARMATURE DU REZ-DE CHAUSSÉE" in its title block, so
    # the explicit TRÉFOND wording is checked first.
    elif "TREFOND" in text:
        level = "TRÉFOND"
    elif "REZ" in text or "RDC" in text:
        level = "RDC"
    else:
        name = fold(filename)
        m = re.search(r"NIV\s*(\d+)", name)
        level = f"NIVEAU {m.group(1)}" if m else "RDC" if "RDC" in name else "TRÉFOND" if "TREFOND" in name else None
    return level, layer


def crop_for(rect: pymupdf.Rect, view: View,
             anchor: tuple[float, float] | None = None) -> pymupdf.Rect:
    x, y = anchor or center(rect)
    # Tall wall symbols can span two rows. Crop from the actual intersection,
    # allowing raised callouts across close row boundaries, not the wall centre.
    return pymupdf.Rect(x - 100, y - 100, x + 130, y + 40) & pymupdf.Rect(view.bbox)


def associate(lines: list[TextLine], target: Support, neighbours: list[Support]) -> list[TextLine]:
    """Keep callouts whose centres are nearest this support within the same view.

    A local crop may include a neighbour's block. Never let the crop alone assign it.
    Equal-distance cases are omitted and reported via the support's unread status.
    """
    candidates = [s for s in neighbours if s.view == target.view]
    out = []
    for l in lines:
        distances = sorted((math.hypot(l.cx - (s.anchor or center(pymupdf.Rect(s.symbol_bbox)))[0],
                                      l.cy - ((s.anchor or center(pymupdf.Rect(s.symbol_bbox)))[1]
                                              - (30 if s.anchor else 0)))
                            + (40 if s.detection == "grid_intersection" else 0), i)
                           for i, s in enumerate(candidates))
        if not distances or candidates[distances[0][1]] is not target:
            continue
        if len(distances) > 1 and distances[1][0] - distances[0][0] < 2:
            continue
        out.append(l)
    return out


def parse_lines(lines: list[TextLine], system: str = "imperial") -> list[dict]:
    bars = []
    for l in lines:
        text = re.sub(r"^[-_|]+\s*(?=(?:NUM|ALP)\b)", "", l.text, flags=re.I)
        text = re.sub(r"\b(NUM|ALP)[.:]\s*(?=\d)", r"\1: ", text, flags=re.I)
        text = re.sub(r"(?<!\S)(\d+)\.\s+(?=(?:10|15|20|25|30|35|45|55)M\b)", r"\1 ", text)
        b = parse_bar_line(text, system)
        if b and b.armature.quantite is not None and b.armature.diametre is not None:
            issue = None
            mark = b.armature.repere
            if re.search(r"\b\d+-\d{3,}\b", text):
                b.armature.longueur_mm = None
                b.armature.repere = None
                issue = "truncated or noisy OCR length"
            length = re.search(r"(?:^|\s)(\d+)-(\d{2})(?=\s|$)", l.text)
            if b.armature.longueur_mm is not None and length and int(length.group(2)) > 11:
                b.armature.longueur_mm = None
                issue = "invalid inches in OCR length: " + length.group(0).strip()
            if mark and not MARK.fullmatch(mark):
                # A truncated length such as 11- or 11-038 must never become a mark.
                b.armature.repere = None
                issue = f"unread length or fabrication mark: {mark}"
            elif mark is None and b.armature.longueur_mm is None and issue is None:
                issue = "length or fabrication mark unread"
            bars.append({"label": b.label, **b.armature.model_dump(), "raw": l.text,
                         "formatted": f"{b.armature.quantite}-{b.armature.diametre}",
                         "bbox": tuple(l.rect), "confidence": l.confidence,
                         "size_from_mark": b.size_from_mark, "vertical": l.vertical,
                         "issue": issue})
    for l in lines:
        if not SPACING.fullmatch(l.text.strip()):
            continue
        eligible = [b for b in bars if b["espacement_mm"] is None and b["vertical"] == l.vertical]
        if eligible:
            b = min(eligible, key=lambda b: math.hypot(center(pymupdf.Rect(b["bbox"]))[0] - l.cx,
                                                     center(pymupdf.Rect(b["bbox"]))[1] - l.cy))
            bx, by = center(pymupdf.Rect(b["bbox"]))
            if math.hypot(bx - l.cx, by - l.cy) < 25:
                b["espacement_mm"] = parse_spacing(l.text, system)
                b["confidence"] = min(b["confidence"], l.confidence)
                b["raw"] += " " + l.text
    return bars


def scan_grid(src: PageImage, view: View) -> list[TextLine]:
    """Detect once in overlapping tiles; read intact callout lines for every axis.

    Grid intersections without a detected grey fill are included. Joining detector
    boxes before recognition prevents crop edges from becoming truncated numbers.
    """
    viewport = pymupdf.Rect(view.bbox)
    boxes = []
    for y in np.arange(viewport.y0, viewport.y1, 200):
        for x in np.arange(viewport.x0, viewport.x1, 200):
            crop = pymupdf.Rect(x, y, min(x + 250, viewport.x1), min(y + 250, viewport.y1))
            img = src.render(crop, 4)
            if img.min() > 200:
                continue
            detected = ocr._ocr()(img, use_det=True, use_cls=False, use_rec=False)
            detected_boxes = getattr(detected, "boxes", None)
            for poly in detected_boxes if detected_boxes is not None else []:
                xs = [crop.x0 + p[0] / 4 for p in poly]
                ys = [crop.y0 + p[1] / 4 for p in poly]
                boxes.append(TextLine(float(min(xs)), float(min(ys)), float(max(xs)), float(max(ys)),
                                      "", 0, bool(max(ys) - min(ys) > 1.5 * (max(xs) - min(xs)))))
    lines = []
    for box in _join(boxes):
        length = box.y1 - box.y0 if box.vertical else box.x1 - box.x0
        thickness = box.x1 - box.x0 if box.vertical else box.y1 - box.y0
        if length < 25 or not 2 < thickness < 18:
            continue
        line = recognise_line(src, box)
        if line.confidence >= 0.5 and line.text:
            lines.append(line)
    log.info("%s: scanned %d callout-sized text lines across every grid intersection", view.id, len(lines))
    return lines


def _oracle(page: pymupdf.Page, rect: pymupdf.Rect) -> list[TextLine]:
    """Validation ONLY. Called after the image result is final, never used for parsing."""
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            box = pymupdf.Rect(line["bbox"])
            if rect.contains(pymupdf.Point(*center(box))):
                text = " ".join(s["text"] for s in line["spans"]).strip()
                out.append(TextLine(*box, text, 1.0, abs(line["dir"][1]) > 0.5))
    return out


def bar_key(b: dict):
    return (b["label"], b["quantite"], b["diametre"], fold(b["repere"] or ""),
            b["espacement_mm"], b["longueur_mm"])


def parse_page(page: pymupdf.Page, filename: str = "", coordinates: list[str] | None = None,
               max_supports: int | None = None, check: bool = False) -> PageResult:
    started = time.time()
    if page.rotation:
        page.remove_rotation()
    src = PageImage(page)
    img = src.render(pymupdf.Rect(0, 0, src.width, src.height), ZOOM)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    bubbles = circle_candidates(gray)
    log.info("page %d: %d bubble candidates", page.number + 1, len(bubbles))
    read_bubbles(src, bubbles)
    views = build_views(bubbles)
    warnings = [w for v in views for w in v.warnings]
    if not views:
        warnings.append("grid unread: no paired letter strips with numeric axes")
    for v in views:
        log.info("%s: %d rows, %d columns", v.id, len(v.letters), len(v.numbers))
    rects = grey_rectangles(gray)
    outlines = outlined_supports(gray, views, rects)
    log.info("%d filled rectangles; %d outlined supports in grey backgrounds", len(rects), len(outlines))
    supports = []
    occupied = set()
    for rect in rects + outlines:
        # A filled wall can touch more than one grid intersection. Each needs its
        # own callout read; choosing only the rectangle's nearest row drops data.
        matches = [(v, f"{letter}-{number}", (x, y))
                   for v in views for letter, y in v.letters.items()
                   for number, x in v.numbers.items()
                   if pymupdf.Rect(v.bbox).contains(pymupdf.Point(x, y))
                   and (rect + (-8, -8, 8, 8)).contains(pymupdf.Point(x, y))]
        if matches:
            for v, coordinate, anchor in matches:
                if (v.id, coordinate) in occupied:
                    continue
                occupied.add((v.id, coordinate))
                s = Support(page.number + 1, v.id, coordinate, tuple(rect), anchor=anchor)
                s.confidence = 1.0
                if rect in outlines:
                    s.detection = "outlined_rectangle"
                    s.confidence *= 0.85
                supports.append(s)
        else:
            s = Support(page.number + 1, None, None, tuple(rect), reason="grey rectangle not located on a readable grid")
            supports.append(s)
    # Check every labelled intersection, including symbols missed by the grey-fill
    # detector. Keep these distinguishable from pixel-confirmed supports.
    for v in views:
        for letter, y in v.letters.items():
            for number, x in v.numbers.items():
                coordinate = f"{letter}-{number}"
                if (v.id, coordinate) not in occupied:
                    supports.append(Support(page.number + 1, v.id, coordinate,
                                            (x - 2, y - 2, x + 2, y + 2),
                                            detection="grid_intersection", anchor=(x, y),
                                            confidence=0.8))
    supports.sort(key=lambda s: (s.view or "~", (s.anchor or center(pymupdf.Rect(s.symbol_bbox)))[1],
                                (s.anchor or center(pymupdf.Rect(s.symbol_bbox)))[0]))
    # Keep ALL neighbours for association, including those outside the requested subset.
    selected = [s for s in supports if not coordinates or s.coordinate in coordinates]
    if max_supports:
        selected = selected[:max_supports]
        warnings.append(f"limited run: {len(selected)} of {len(supports)} detected supports read")
    if coordinates:
        missing = set(coordinates) - {s.coordinate for s in selected}
        warnings.extend(f"requested support not detected: {key}" for key in sorted(missing))
    level, layer = metadata(src, filename)
    if layer is None:
        warnings.append("reinforcement layer unread in drawing title")
    scanned = {v.id: scan_grid(src, v) for v in views} if not coordinates and not max_supports else None
    for i, s in enumerate(selected, 1):
        s.level, s.layer = level, layer
        if not s.view:
            continue
        v = next(v for v in views if v.id == s.view)
        crop = crop_for(pymupdf.Rect(s.symbol_bbox), v, s.anchor)
        s.crop_bbox = tuple(crop)
        available = [l for l in scanned[v.id] if crop.contains(pymupdf.Point(l.cx, l.cy))] \
            if scanned is not None else read_region(src, crop)
        lines = associate(available, s, supports)
        bars = parse_lines(lines)
        # Whole-grid scans already read intact joined boxes. Retry actual callouts
        # and pixel-confirmed supports; blank/unmarked intersections need no OCR.
        retry_needed = not bars or any(b["issue"] for b in bars)
        if scanned is not None and not bars and s.detection == "grid_intersection" \
                and not any("INTEGR" in fold(l.text) or re.search(r"\b(?:NUM|ALP)\b", l.text, re.I) for l in lines):
            retry_needed = False
        if retry_needed:
            retry = associate(read_region(src, crop, zoom=8.0), s, supports)
            retry_bars = parse_lines(retry)
            quality = lambda bs: (sum(not b["issue"] for b in bs), len(bs))
            if quality(retry_bars) > quality(bars):
                lines, bars = retry, retry_bars
        s.lines = [l.text for l in lines]
        s.text_boxes = [{"text": l.text, "bbox": tuple(l.rect), "confidence": l.confidence,
                         "vertical": l.vertical} for l in lines]
        s.bars = bars
        s.summary = " · ".join((f"{b['label']}: " if b["label"] else "") + b["formatted"]
                               for b in bars) or None
        issues = [b["issue"] for b in bars if b["issue"]]
        s.status = "partial" if issues else "read" if bars else "unread"
        s.reason = "; ".join(issues) if issues else None if bars else "no complete reinforcement line read near this support"
        s.confidence = round(s.confidence * min((b["confidence"] for b in bars), default=0.0), 3)
        if issues:
            s.confidence = round(s.confidence * 0.5, 3)
        if check:
            truth = associate(_oracle(page, crop), s, supports)
            s.oracle = [l.text for l in truth]
            truth_bars = parse_lines(truth)
            s.check_equal = sorted(map(bar_key, bars), key=str) == sorted(map(bar_key, truth_bars), key=str)
            count_sizes = lambda bs: sorted((b["quantite"], b["diametre"]) for b in bs)
            s.check_count_size_equal = count_sizes(bars) == count_sizes(truth_bars)
        log.info("support %d/%d %s %s: %s%s", i, len(selected), s.view, s.coordinate,
                 s.summary or s.reason,
                 f" [check={s.check_equal}]" if check else "")
    return PageResult(filename, page.number + 1, views, selected, warnings, round(time.time() - started, 2))


def unique_supports(result: PageResult) -> list[Support]:
    """All final exports share the same repeated-detail filtering."""
    mappings = view_correspondences([asdict(v) for v in result.views])
    candidates = []
    for index, s in enumerate(result.supports):
        if not s.bars or not s.coordinate or not s.view:
            continue
        candidates.append(dict(index=index, fichier=result.fichier, page=s.page,
            view=s.view, coordinate=s.coordinate, niveau=s.level, layer=s.layer,
            summary=s.summary, status=s.status, confidence=s.confidence,
            reinforcement=[dict(b, role=b.get("label")) for b in s.bars]))
    selected, report = deduplicate(candidates,
        identity=lambda row:(row["fichier"],row.get("niveau") or f"page-{row['page']}",
                             row.get("layer"),*grid_identity(row,mappings)),
        signature=lambda row:reinforcement_key(row["reinforcement"]),
        quality=lambda row:grid_quality(row,mappings))
    report["view_correspondences"] = mappings
    result.deduplication = report
    return [replace(result.supports[row["index"]], status=row["status"],
                    reason=row.get("reason", result.supports[row["index"]].reason),
                    duplicate_conflict=row.get("duplicate_conflict",False)) for row in selected]


def records(result: PageResult, filename: str) -> list[ElementRecord]:
    out = []
    for i, s in enumerate(unique_supports(result), 1):
        if not s.bars:
            continue
        boxes = [pymupdf.Rect(b["bbox"]) for b in s.bars]
        annotation = pymupdf.Rect(min(b.x0 for b in boxes), min(b.y0 for b in boxes),
                                 max(b.x1 for b in boxes), max(b.y1 for b in boxes))
        feuillet = f"{Path(filename).stem} p{result.page}"
        out.append(ElementRecord(
            id=f"{feuillet}_{s.view}_{s.coordinate}_{i}_atelier", source="atelier",
            fichier=filename, feuillet=feuillet, page=result.page, x=center(annotation)[0],
            y=center(annotation)[1], type_element="dalle", element=s.coordinate or "UNKNOWN",
            armature=[{k: b[k] for k in ("repere", "diametre", "quantite", "espacement_mm", "longueur_mm")} for b in s.bars],
            debug=Debug(raw=s.lines, confidence=s.confidence, decode_path="ocr", tier=1,
                        locator_kind="grid", niveau=s.level, symbol_bbox=s.symbol_bbox,
                        layer=s.layer, roles=[b["label"] for b in s.bars], view=s.view,
                        crop_bbox=s.crop_bbox, text_boxes=s.text_boxes,
                        duplicate_conflict=s.duplicate_conflict)))
    return out


def write_csv(path: str, results: list[PageResult]) -> None:
    """Review output: one row per directional bar entry; keep unread supports."""
    fields = ("fichier", "page", "niveau", "layer", "view", "coordinate", "role",
              "reinforcement", "quantite", "diametre", "longueur_mm", "espacement_mm",
              "repere", "status", "confidence", "detection", "count_size_check",
              "full_check", "raw", "reason")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for result in results:
            for s in result.supports:
                for b in s.bars or [{}]:
                    writer.writerow({
                        "fichier": result.fichier, "page": s.page, "niveau": s.level,
                        "layer": s.layer, "view": s.view, "coordinate": s.coordinate,
                        "role": b.get("label"), "reinforcement": b.get("formatted"),
                        **{key: b.get(key) for key in ("quantite", "diametre", "longueur_mm",
                                                     "espacement_mm", "repere")},
                        "status": s.status, "confidence": s.confidence,
                        "detection": s.detection,
                        "count_size_check": "" if s.check_count_size_equal is None else
                            "EMPTY" if not s.bars and s.check_count_size_equal else
                            "MATCH" if s.check_count_size_equal else "DIFFERENT",
                        "full_check": "" if s.check_equal is None else
                            "EMPTY" if not s.bars and s.check_equal else
                            "MATCH" if s.check_equal else "DIFFERENT",
                        "raw": b.get("raw", " | ".join(s.lines)), "reason": s.reason,
                    })


def clean_output(results: list[PageResult]) -> list[dict]:
    """Final review JSON: located reinforcement only, with optional nulls omitted."""
    output = []
    for result in results:
        for support in unique_supports(result):
            if not support.coordinate or not support.view:
                continue
            bars = []
            for bar in support.bars:
                quantity, diameter = bar.get("quantite"), bar.get("diametre")
                if not isinstance(quantity, int) or quantity < 1 or diameter not in BAR_DESIGNATORS:
                    continue
                entry = {key: bar.get(key) for key in ("quantite", "diametre", "longueur_mm",
                         "espacement_mm", "repere", "raw", "issue")}
                entry.update(role=bar.get("label"), formatted=f"{quantity}-{diameter}")
                bars.append({key: value for key, value in entry.items() if value is not None})
            if not bars:
                continue
            row = dict(fichier=result.fichier, page=support.page, niveau=support.level,
                       layer=support.layer, view=support.view, coordinate=support.coordinate,
                       summary=" · ".join((f"{b['role']}: " if b.get("role") else "") + b["formatted"] for b in bars),
                       reinforcement=bars, status=support.status, confidence=support.confidence,
                       detection=support.detection, count_size_check=support.check_count_size_equal,
                       full_check=support.check_equal, reason=support.reason)
            if support.duplicate_conflict:
                row["duplicate_conflict"] = True
            output.append({key: value for key, value in row.items() if value is not None})
    return output


def annotate(page: pymupdf.Page, result: PageResult) -> None:
    shape = page.new_shape()
    shape.insert_text((15, 18), f"OCR review - source page {result.page}", fontsize=8)
    for x, label, color in ((190, "Read", (0, 0.65, 0)), (235, "Partial", (1, 0.5, 0)),
                            (290, "Unread", (1, 0, 0)), (350, "Grid", (0, 0.6, 1))):
        shape.insert_text((x, 18), label, fontsize=8, color=color)
    for v in result.views:
        for label, y in v.letters.items():
            shape.draw_line((v.bbox[0], y), (v.bbox[2], y))
            shape.finish(color=(0, 0.6, 1), width=0.4)
        for label, x in v.numbers.items():
            shape.draw_line((x, v.bbox[1]), (x, v.bbox[3]))
            shape.finish(color=(0, 0.6, 1), width=0.4)
    for s in result.supports:
        if s.detection == "grid_intersection" and not s.bars:
            continue  # Blank checked intersections stay in diagnostics, not over the drawing.
        color = (0, 0.65, 0) if s.status == "read" else (1, 0.5, 0) if s.status == "partial" else (1, 0, 0)
        r = pymupdf.Rect(s.symbol_bbox)
        shape.draw_rect(r + (-2, -2, 2, 2))
        shape.finish(color=color, width=1)
        label_position = (s.anchor[0] + 3, s.anchor[1] + 8) if s.anchor else (r.x0, r.y1 + 7)
        shape.insert_text(label_position, s.coordinate or "UNKNOWN", fontsize=6, color=color)
        for b in s.bars:
            t = pymupdf.Rect(b["bbox"])
            shape.draw_rect(t)
            shape.finish(color=color, width=0.6)
            shape.draw_line(pymupdf.Point(*(s.anchor or center(r))), pymupdf.Point(*center(t)))
            shape.finish(color=color, width=0.4)
    shape.commit()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", default=DEFAULT_FILE)
    ap.add_argument("--coordinate", action="append", help="read only this grid coordinate, repeatable")
    ap.add_argument("--max-supports", type=int, help="limit for quick experiments")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--check", action="store_true", help="compare image read with hidden PDF text, validation only")
    ap.add_argument("--json", default="out/dalle_clp.json", help="Appendix-A records")
    ap.add_argument("--diagnostics", default="out/dalle_clp_diagnostics.json", help="all supports, raw reads and unread reasons")
    ap.add_argument("--summary-json", default="out/dalle_clp_summaries.json", help="count-size summaries, e.g. NUM: 22-15M")
    ap.add_argument("--output-json", default="out/dalle_clp_output.json", help="final clean review JSON; excludes empty rows")
    ap.add_argument("--csv", help="optional CSV diagnostics export, including unread intersections")
    ap.add_argument("--annotated", help="review PDF path, or output directory for a folder run")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("RapidOCR").setLevel(logging.WARNING)
    ocr.OCR_THREADS = max(1, args.threads)
    ocr._ocr.cache_clear()
    cv2.setNumThreads(1)
    try:
        os.nice(10)
    except OSError:
        pass
    if args.max_supports is not None and args.max_supports < 1:
        ap.error("--max-supports must be positive")
    source = Path(args.file).expanduser()
    files = sorted(source.glob("*.pdf")) if source.is_dir() else [source]
    if not files:
        ap.error("no PDF files found")
    output_paths = [p for p in (args.json, args.diagnostics, args.summary_json, args.output_json, args.csv) if p]
    for output in output_paths:
        if any(Path(output).resolve() == file.resolve() for file in files):
            ap.error("JSON outputs must differ from input PDFs")
    if len({Path(p).resolve() for p in output_paths}) < len(output_paths):
        ap.error("records, diagnostics, summaries and CSV must have different output paths")
    results, all_records = [], []
    for file in files:
        with pymupdf.open(file) as doc:
            page = doc[-1]
            log.info("%s: reading last page ONLY (%d/%d)", file.name, len(doc), len(doc))
            result = parse_page(page, file.name, args.coordinate, args.max_supports, args.check)
            results.append(result)
            all_records.extend(records(result, file.name))
    summaries = [{"fichier": r.fichier, "page": s.page, "coordinate": s.coordinate,
                  "view": s.view, "niveau": s.level, "layer": s.layer,
                  "summary": s.summary, "reinforcement": [b["formatted"] for b in s.bars],
                  "roles": [b["label"] for b in s.bars], "status": s.status,
                  "confidence": s.confidence, "reason": s.reason}
                 for r in results for s in unique_supports(r)]
    for path, data in ((args.json, [r.to_schema() for r in all_records]),
                       (args.diagnostics, [asdict(r) for r in results]),
                       (args.summary_json, summaries),
                       (args.output_json, clean_output(results))):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    supports = [s for r in results for s in r.supports]
    if args.csv:
        write_csv(args.csv, results)
    log.info("Final clean JSON output saved: %s", args.output_json)
    # Save data before drawing the optional overlays; copy ONLY each last page.
    if args.annotated:
        for file, result in zip(files, results):
            output = Path(args.annotated)
            if source.is_dir():
                output = output / f"{file.stem}_review.pdf"
            if output.resolve() == file.resolve():
                ap.error("annotated output must differ from input")
            output.parent.mkdir(parents=True, exist_ok=True)
            log.info("Writing annotated last-page review: %s", output)
            with pymupdf.open(file) as doc:
                page = doc[-1]
                if page.rotation:
                    page.remove_rotation()
                annotate(page, result)
                with pymupdf.open() as review:
                    review.insert_pdf(doc, from_page=page.number, to_page=page.number)
                    review.save(output)
    print(f"{len(all_records)} records; {sum(s.status == 'partial' for s in supports)} partial, "
          f"{sum(s.status == 'unread' for s in supports)} unread supports; "
          f"{sum(r.seconds for r in results):.1f}s")
    print(f"records: {args.json}\ndiagnostics: {args.diagnostics}")
    print(f"count-size summaries: {args.summary_json}")
    print(f"FINAL OUTPUT: {args.output_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
