"""Element symbol detection and scale self-calibration.

Validated on CLP S-502 (PLAN SS6.2): 67 filled rects of 12x18 pt matched 67 callouts
stating 16"x24", and 3 rects of 7x22 pt matched 3 callouts stating 10"x30". The drawing
scale derived to exactly 0.75 pt/inch = 1/8" = 1'-0".

The callout text is NOT at the element's position; it is placed adjacent. Association
must therefore go through the symbol, by EDGE distance (centre distance picks the wrong
neighbour in dense bays).
"""

from __future__ import annotations

import re
import statistics as st
from dataclasses import dataclass

from ..page import PreparedPage
from ..units import MM_PER_INCH

#: ``16"x24"`` / ``16 1/4"X21"`` (imperial) or ``300x300`` / ``400x750`` (metric mm)
_DIM_IMPERIAL = re.compile(r'^(\d+(?:\s+\d+/\d+)?)"\s*[xX]\s*(\d+(?:\s+\d+/\d+)?)"$')
_DIM_METRIC = re.compile(r"^(\d{3,4})\s*[xX]\s*(\d{3,4})$")


def parse_dimensions(text: str) -> tuple[float, float] | None:
    """Stated section size -> (width_mm, height_mm)."""
    m = _DIM_IMPERIAL.match(text)
    if m:
        def to_mm(s: str) -> float:
            parts = s.split()
            total = float(parts[0])
            if len(parts) > 1:
                num, den = parts[1].split("/")
                total += int(num) / int(den)
            return total * MM_PER_INCH
        return to_mm(m.group(1)), to_mm(m.group(2))
    m = _DIM_METRIC.match(text)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None


@dataclass
class Symbol:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def edge_distance(self, x: float, y: float) -> float:
        """Point-to-rectangle distance."""
        dx = max(self.x0 - x, 0, x - self.x1)
        dy = max(self.y0 - y, 0, y - self.y1)
        return (dx * dx + dy * dy) ** 0.5

    def rect_distance(self, bx0: float, by0: float, bx1: float, by1: float) -> float:
        """Rectangle-to-rectangle EDGE distance - the correct metric for associating a
        callout block with its element symbol.

        Centre-to-centre (or centre-to-edge) picks the wrong neighbour in dense bays:
        on CLP S-502 the 4-35M callout's centre is nearer the K-7 symbol (79 pt) than
        the K-6 one (89 pt), but its block EDGE is 27 pt from K-6 and 63 pt from K-7.
        K-6 is the ground truth.
        """
        dx = max(self.x0 - bx1, 0, bx0 - self.x1)
        dy = max(self.y0 - by1, 0, by0 - self.y1)
        return (dx * dx + dy * dy) ** 0.5


def candidate_symbols(page: PreparedPage, lo: float = 4.0, hi: float = 80.0) -> list[Symbol]:
    out = []
    for d in page.drawings:
        if d.get("fill") is None:
            continue
        r = d["rect"]
        if lo <= min(r.width, r.height) and max(r.width, r.height) <= hi:
            out.append(Symbol(r.x0, r.y0, r.x1, r.y1))
    return out


def calibrate_scale(symbols: list[Symbol], stated: list[tuple[float, float]]) -> float | None:
    """pt per mm, fitted by matching symbol aspect ratios to the stated dimensions.

    Self-validating: a poor fit means the sheet's geometry and text disagree, which
    invalidates every locator on that sheet, so the caller must warn.
    """
    if not symbols or not stated:
        return None
    ratios = []
    for w_mm, h_mm in stated:
        want = max(w_mm, h_mm) / min(w_mm, h_mm)
        for s in symbols:
            if s.w <= 0 or s.h <= 0:
                continue
            got = max(s.w, s.h) / min(s.w, s.h)
            if abs(got - want) < 0.06 * want:
                ratios.append(max(s.w, s.h) / max(w_mm, h_mm))
    return round(st.median(ratios), 6) if len(ratios) >= 3 else None


def matches_dimensions(s: Symbol, dims: tuple[float, float], scale: float, tol: float = 0.18) -> bool:
    want = sorted((dims[0] * scale, dims[1] * scale))
    got = sorted((s.w, s.h))
    return all(abs(g - w) <= tol * max(w, 1.0) for g, w in zip(got, want))
