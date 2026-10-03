"""Grid located from the drawn grid LINES, not just the bubble labels.

Why: a sheet often carries several views (CLP S-050 has five enlarged radier views),
each repeating the grid bubbles at a different place. A page-wide "label -> one
coordinate" model cannot represent that. Every grid line here keeps its own extent,
so a point is located against the lines of the view it actually sits in.

Signature, measured on all four projects: a grid line is a chain of >= 6 short,
collinear, same-style segments (pre-dashed centre line) >= 150 pt long, with its
bubble label just past one end.
"""

from __future__ import annotations

import collections
import re
import statistics
from dataclasses import dataclass, field

from ..page import PreparedPage

_LETTER = re.compile(r"^[A-Z]{1,2}(\.\d{1,2})?'?$")
_NUMBER = re.compile(r"^\d{1,2}(\.\d{1,2})?$")      # "10'" is a dimension fragment


@dataclass(frozen=True)
class GridLine:
    label: str
    kind: str          # "letter" | "number"
    orient: str        # "h" (constant y) | "v" (constant x)
    c: float           # the constant coordinate
    lo: float          # extent along the line
    hi: float

    def covers(self, along: float, margin: float) -> bool:
        return self.lo - margin <= along <= self.hi + margin


@dataclass
class LineGrid:
    lines: list[GridLine] = field(default_factory=list)
    evidence: dict = field(default_factory=dict)

    @property
    def letters(self) -> dict[str, float]:
        return {l.label: l.c for l in self.lines if l.kind == "letter"}

    @property
    def numbers(self) -> dict[str, float]:
        return {l.label: l.c for l in self.lines if l.kind == "number"}

    @property
    def letter_role(self) -> str:
        o = self.evidence.get("letter_orient")
        return "rows" if o == "h" else "columns" if o == "v" else "unknown"

    @property
    def ok(self) -> bool:
        return (sum(l.kind == "letter" for l in self.lines) >= 2
                and sum(l.kind == "number" for l in self.lines) >= 2)

    def locate(self, x: float, y: float, margin: float = 120.0) -> tuple[str | None, float]:
        """Nearest pair of INTERSECTING letter/number lines whose extents cover the point."""
        if not self.ok:
            return None, 1.0

        def perp(l: GridLine) -> float:
            return abs((y if l.orient == "h" else x) - l.c)

        def along(l: GridLine) -> float:
            return x if l.orient == "h" else y

        near = [l for l in self.lines if l.covers(along(l), margin)]
        L = sorted((l for l in near if l.kind == "letter"), key=perp)[:6]
        N = sorted((l for l in near if l.kind == "number"), key=perp)[:6]
        best = None
        for a in L:
            for b in N:
                if a.orient == b.orient:
                    continue
                if not (a.covers(b.c, 40) and b.covers(a.c, 40)):   # same view
                    continue
                cost = perp(a) / self._spacing(a) + perp(b) / self._spacing(b)
                if best is None or cost < best[0]:
                    best = (cost, f"{a.label}-{b.label}")
        if best is None:
            return None, 1.0
        return best[1], min(1.0, best[0] / 2)

    def _spacing(self, l: GridLine) -> float:
        """Distance to the nearest parallel line of the same kind in the same view."""
        ds = [abs(o.c - l.c) for o in self.lines
              if o is not l and o.kind == l.kind and o.orient == l.orient
              and abs(o.c - l.c) > 1 and min(o.hi, l.hi) - max(o.lo, l.lo) > 0]
        return min(ds) if ds else 300.0


def _dash_runs(page: PreparedPage, min_len=150.0, min_pieces=6, gap=15.0):
    groups: dict[tuple, list[tuple[float, float]]] = collections.defaultdict(list)
    for d in page.drawings:
        style = (d.get("color"), round(d.get("width") or 0, 2))
        for it in d["items"]:
            if it[0] != "l":
                continue
            a, b = it[1], it[2]
            if abs(a.y - b.y) < 0.5 and abs(a.x - b.x) > 0.3:
                groups[("h", round(a.y * 2) / 2, style)].append((min(a.x, b.x), max(a.x, b.x)))
            elif abs(a.x - b.x) < 0.5 and abs(a.y - b.y) > 0.3:
                groups[("v", round(a.x * 2) / 2, style)].append((min(a.y, b.y), max(a.y, b.y)))
    out = []
    for (o, c, _), iv in groups.items():
        iv.sort()
        lo, hi, n = iv[0][0], iv[0][1], 1
        for s, e in iv[1:]:
            if s - hi <= gap:
                hi, n = max(hi, e), n + 1
                continue
            if hi - lo >= min_len and n >= min_pieces:
                out.append((o, c, lo, hi))
            lo, hi, n = s, e, 1
        if hi - lo >= min_len and n >= min_pieces:
            out.append((o, c, lo, hi))
    return out


def extract_lines(page: PreparedPage, reach: float = 60.0, perp: float = 8.0) -> LineGrid:
    cands = [w for w in page.words if _LETTER.match(w.text) or _NUMBER.match(w.text)]
    raw: list[tuple[GridLine, float]] = []
    for o, c, lo, hi in _dash_runs(page):
        best = None
        for w in cands:
            a, x = (w.cx, w.cy) if o == "h" else (w.cy, w.cx)
            if abs(x - c) > perp:
                continue
            d = lo - a if a < lo else a - hi if a > hi else None
            if d is None or d > reach:
                continue
            if best is None or d < best[0]:
                best = (d, w)
        if best:
            w = best[1]
            kind = "letter" if _LETTER.match(w.text) else "number"
            raw.append((GridLine(w.text, kind, o, c, lo, hi), w.y1 - w.y0))

    ev: dict = {"dash_runs": len(raw)}
    if not raw:
        return LineGrid([], ev)

    # 1. bubble text shares one glyph height; dimension fragments do not
    sizes = [s for _, s in raw]
    med = statistics.median(sizes)
    kept = [l for l, s in raw if 0.6 * med <= s <= 1.6 * med]

    # 2. on one sheet all letter lines share one orientation, numbers the other
    lo_ = collections.Counter(l.orient for l in kept if l.kind == "letter")
    if lo_:
        letter_orient = lo_.most_common(1)[0][0]
        kept = [l for l in kept
                if (l.kind == "letter") == (l.orient == letter_orient)]
        ev["letter_orient"] = letter_orient
    ev["kept"] = len(kept)
    ev["dropped"] = len(raw) - len(kept)
    return LineGrid(kept, ev)


def is_bubble(grid: LineGrid, w, reach: float = 60.0, perp: float = 8.0) -> bool:
    """True when token `w` is a grid bubble label (just past the end of a grid line)."""
    for l in grid.lines:
        a, x = (w.cx, w.cy) if l.orient == "h" else (w.cy, w.cx)
        if abs(x - l.c) <= perp and (l.lo - reach <= a < l.lo or l.hi < a <= l.hi + reach):
            return True
    return False
