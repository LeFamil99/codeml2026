"""Grid axes and locator resolution.

The axis convention is PER PROJECT (PLAN SS5.15b): CLP letters=rows,
WP2/EspCa3B letters=columns. We detect it rather than assume it, but always EMIT
``<letter>-<number>`` so the output stays diffable against the answer key, whose
locators are always letter-first (``K-6``, ``J-10.8``, ``L-13``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..page import PreparedPage

LETTER = re.compile(r"^[A-Z]$")
NUMBER = re.compile(r"^\d{1,2}(\.\d)?$")


@dataclass
class GridSystem:
    letters: dict[str, float]          # label -> position along its axis
    numbers: dict[str, float]
    letter_role: str                   # "rows" | "columns"
    evidence: dict

    @property
    def ok(self) -> bool:
        return len(self.letters) >= 2 and len(self.numbers) >= 2

    def locate(self, x: float, y: float) -> tuple[str | None, float]:
        """Nearest grid intersection -> ``K-6``, plus a normalised distance cost."""
        if not self.ok:
            return None, 1.0
        lx = x if self.letter_role == "columns" else y
        nx = y if self.letter_role == "columns" else x
        letter, ld = min(((k, abs(v - lx)) for k, v in self.letters.items()), key=lambda i: i[1])
        number, nd = min(((k, abs(v - nx)) for k, v in self.numbers.items()), key=lambda i: i[1])
        spacing = _median_spacing(self.letters) or 1.0
        spacing_n = _median_spacing(self.numbers) or 1.0
        cost = min(1.0, (ld / spacing + nd / spacing_n) / 2)
        return f"{letter}-{number}", cost


def _median_spacing(axis: dict[str, float]) -> float | None:
    vals = sorted(axis.values())
    if len(vals) < 2:
        return None
    gaps = sorted(b - a for a, b in zip(vals, vals[1:]) if b - a > 1)
    return gaps[len(gaps) // 2] if gaps else None


def _bubble_labels(page: PreparedPage, min_diameter: float = 30.0) -> list[tuple[str, float, float]]:
    """Grid labels read out of their bubble circles.

    The paired-margin heuristic below finds nothing on LIGREP and EspCa3B plan sheets
    (measured: 0 letter axes, 0/677 and 0/644 records located). Bubbles there are not
    duplicated at both margins, so we detect the CIRCLE and read the token inside it.

    Thresholds come from measurement (PLAN SS5.15): real bubbles are ~49 pt across while
    stray round glyphs (the O/D/R/E/A/U in "ORDRE DE POSE D'ARMATURE") are 13-20 pt, so a
    30 pt floor separates them. Circles must be stroked, not filled.
    """
    circles = []
    for d in page.drawings:
        if d.get("fill") is not None:
            continue                                   # a filled disc is not a bubble
        r = d["rect"]
        if r.width <= 0 or r.height <= 0:
            continue
        curves = sum(1 for it in d["items"] if it[0] == "c")
        if curves < 3:
            continue
        if not (0.80 < r.width / r.height < 1.25):
            continue
        if r.width < min_diameter:
            continue
        circles.append(r)

    # dedupe concentric duplicates (every bubble is drawn twice, at two diameters)
    kept = []
    for r in sorted(circles, key=lambda r: -r.width):
        cx, cy = (r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2
        if any(abs(cx - k[0]) < 6 and abs(cy - k[1]) < 6 for k in kept):
            continue
        kept.append((cx, cy, r))

    out = []
    for cx, cy, r in kept:
        inside = [
            w for w in page.words
            if r.x0 - 1 <= w.cx <= r.x1 + 1 and r.y0 - 1 <= w.cy <= r.y1 + 1
            and (LETTER.match(w.text) or NUMBER.match(w.text))
        ]
        if len(inside) == 1:
            out.append((inside[0].text, cx, cy))
    return out


def _axes_from_bubbles(labels: list[tuple[str, float, float]]) -> tuple[dict, dict, str]:
    """Bubbles sharing a y lie on one header strip -> they label the x axis."""
    letters = [(t, x, y) for t, x, y in labels if LETTER.match(t)]
    numbers = [(t, x, y) for t, x, y in labels if NUMBER.match(t)]

    def axis(group):
        if len(group) < 2:
            return {}, None
        xs = [g[1] for g in group]
        ys = [g[2] for g in group]
        if max(xs) - min(xs) > max(ys) - min(ys):
            return {g[0]: g[1] for g in group}, "columns"   # spread in x
        return {g[0]: g[2] for g in group}, "rows"          # spread in y

    lmap, lrole = axis(letters)
    nmap, _ = axis(numbers)
    return lmap, nmap, lrole or "rows"


def _collinear_axis(occurrences: dict[str, list[tuple[float, float]]], tol: float = 14.0,
                    min_labels: int = 3, min_spread: float = 150.0):
    """Find a set of DISTINCT labels lying on one straight line.

    This subsumes the paired-margin case and also handles LIGREP, whose plan sheets
    label each row only once (A B C D all at x=3405) and draw no bubble circles at all
    - measured 0/677 records located before this change.

    Returns (mapping label->position, role) or ({}, None).
    """
    best: tuple[int, dict, str] = (0, {}, "")

    for axis in ("x", "y"):
        buckets: dict[int, dict[str, float]] = {}
        for label, pts in occurrences.items():
            for cx, cy in pts:
                key = int(round((cx if axis == "x" else cy) / tol))
                # a label collinear in x varies in y, so store the OTHER coordinate
                buckets.setdefault(key, {})[label] = cy if axis == "x" else cx
        for key, group in buckets.items():
            if len(group) < min_labels:
                continue
            positions = list(group.values())
            if max(positions) - min(positions) < min_spread:
                continue
            # collinear in x  -> the labels march down the side -> they mark ROWS
            role = "rows" if axis == "x" else "columns"
            if len(group) > best[0]:
                best = (len(group), dict(group), role)

    return (best[1], best[2]) if best[0] else ({}, None)


def extract_grid(page: PreparedPage) -> GridSystem:
    """Grid axes, by three strategies in order of cost.

    1. collinear label sets (handles all four dev projects)
    2. bubble circles with the label read from inside (EspCa3B-style)
    3. whatever partial axis either found - reported, never silently assumed
    """
    _, _, evidence = page.axis_convention()

    letter_pts: dict[str, list[tuple[float, float]]] = {}
    number_pts: dict[str, list[tuple[float, float]]] = {}
    counts: dict[str, int] = {}
    for w in page.words:
        if LETTER.match(w.text) or NUMBER.match(w.text):
            counts[w.text] = counts.get(w.text, 0) + 1
    for w in page.words:
        # a grid label appears once or twice (one per margin); 'N' from "BETON: 25MPa / N"
        # occurs 66 times on a LIGREP sheet and must not be mistaken for an axis
        if counts.get(w.text, 0) > 6:
            continue
        if LETTER.match(w.text):
            letter_pts.setdefault(w.text, []).append((w.cx, w.cy))
        elif NUMBER.match(w.text):
            number_pts.setdefault(w.text, []).append((w.cx, w.cy))

    letters, lrole = _collinear_axis(letter_pts)
    numbers, nrole = _collinear_axis(number_pts)

    # the two axes must be complementary; trust the one with more labels
    if lrole and nrole and lrole == nrole:
        if len(letters) >= len(numbers):
            nrole = "columns" if lrole == "rows" else "rows"
            numbers, _ = _collinear_axis(number_pts, min_labels=2)
        else:
            lrole = "columns" if nrole == "rows" else "rows"
            letters, _ = _collinear_axis(letter_pts, min_labels=2)

    if len(letters) >= 2 and len(numbers) >= 2:
        evidence["source"] = "collinear_labels"
        evidence["letters"] = len(letters)
        evidence["numbers"] = len(numbers)
        return GridSystem(letters, numbers, lrole or "rows", evidence)

    labels = _bubble_labels(page)
    b_letters, b_numbers, b_role = _axes_from_bubbles(labels)
    evidence["bubbles_found"] = len(labels)
    if len(b_letters) >= 2 and len(b_numbers) >= 2:
        evidence["source"] = "bubble_circles"
        return GridSystem(b_letters, b_numbers, b_role, evidence)

    evidence["source"] = "partial"
    return GridSystem(letters or b_letters, numbers or b_numbers, lrole or "rows", evidence)
