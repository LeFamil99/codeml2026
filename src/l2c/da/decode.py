"""Outlined glyphs -> text lines: the tier-2 reader (PLAN SS5.2-5.8, SS5.14).

Order matters: GEOMETRY first, identity second.
1. Lines: link each glyph to its right neighbour of similar height and overlapping
   vertical band; small marks (``. , - ' "``) join the line whose band holds them.
2. Merge: in real text two characters never overlap horizontally, so parts that do are
   one character (``:`` ``;`` ``É`` ``i`` ``=`` a split ``R``); two adjacent top ticks
   are one ``"``.
3. Score every glyph against every template: shape (``glyphs.scores``) minus how far
   its height and top, relative to the line's cap height, are from the template's.
   That is what separates ``o/O``, ``c/C``, ``x/X``, ``./'/-`` (PLAN SS5.3).
4. Tokens, then two narrow context rules - nothing open-ended:
   - inside a number, a digit look-alike letter (O I l S B ...) becomes the digit when
     the digit is a close candidate;
   - the closed bar vocabulary: after 10|15|20|25|30|35|45|55 a designator slot holds
     ``M``. ``25H`` cannot be emitted when ``M`` is among the glyph's candidates
     (PLAN SS5.14: 83% raw M->H on EspCa3B).
Blind to the plan.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .glyphs import Glyph, bank, merge, page_glyphs, scores

K = 6
MIN_LINE_CONF = 0.55          # mean best score; measured junk lines sit far below real text
_LOOKALIKE_DIGIT = {"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1", "i": "1",
                    "|": "1", "j": "1", "J": "1", "S": "5", "s": "5", "B": "8", "g": "9", "q": "9"}
_DESIG_SLOT = re.compile(r"(?<!\d)(10|15|20|25|30|35|45|55)(?=[A-Za-z])")


@dataclass
class DecodedLine:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float                 # cap height
    confidence: float
    glyphs: list = field(default_factory=list, repr=False)
    words: list = field(default_factory=list, repr=False)   # (x0, y0, x1, y1, text)

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


# ----------------------------------------------------------------- 1. lines
def _vov(a: Glyph, b: Glyph) -> float:
    ov = min(a.y1, b.y1) - max(a.y0, b.y0)
    return ov / max(min(a.h, b.h), 1e-6)


def _lines(gl: list[Glyph]) -> list[list[int]]:
    cell = 30.0
    grid: dict[tuple, list[int]] = defaultdict(list)
    for i, g in enumerate(gl):
        grid[(int(g.x0 // cell), int(g.cy // cell))].append(i)
    parent = list(range(len(gl)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(gl):
        if getattr(a, "solo", False):
            continue                                   # a lone straight stroke never seeds
        best = None
        # labels are often followed by a wide gap ("GOUJ:      25L16X94"): reach far, but
        # beyond 1.3 h only for a glyph of the same height on the same baseline
        reach = 3.0 * a.h
        for cx in range(int((a.x1 - 2) // cell), int((a.x1 + reach) // cell) + 1):
            for cy in range(int((a.cy - a.h) // cell), int((a.cy + a.h) // cell) + 1):
                for j in grid.get((cx, cy), ()):
                    if j == i:
                        continue
                    b = gl[j]
                    if getattr(b, "solo", False):
                        continue
                    gap = b.x0 - a.x1
                    if not (-0.35 * min(a.w, b.w) <= gap <= reach):
                        continue
                    if not (0.55 <= b.h / max(a.h, 1e-6) <= 1.8) or _vov(a, b) < 0.6:
                        continue
                    if gap > 1.3 * a.h and (abs(a.y1 - b.y1) > 0.15 * max(a.h, b.h)
                                            or not 0.8 <= b.h / max(a.h, 1e-6) <= 1.25):
                        continue
                    if best is None or gap < best[0]:
                        best = (gap, j)
        if best:
            parent[find(i)] = find(best[1])
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(gl)):
        groups[find(i)].append(i)
    lines = [g for g in groups.values() if len(g) > 1]
    singles = [g[0] for g in groups.values() if len(g) == 1]

    # attach small marks (and lone characters) to the line whose band holds them
    _orig = list(lines)
    bands = []
    for li, idx in enumerate(lines):
        H, base = _cap(gl, idx)
        bands.append((li, min(gl[i].x0 for i in idx), max(gl[i].x1 for i in idx), base - H, base, H))
    # tiny groups of small marks (the two ticks of a ", the dots of a :) inside a bigger
    # line's band belong to that line - they link to each other, not to it
    big = sorted(range(len(lines)), key=lambda k: -len(lines[k]))
    absorbed = set()
    for k in range(len(lines)):
        g = lines[k]
        if len(g) > 3 or k in absorbed:
            continue
        gh = max(gl[i].h for i in g)
        gx0, gx1 = min(gl[i].x0 for i in g), max(gl[i].x1 for i in g)
        gy0, gy1 = min(gl[i].y0 for i in g), max(gl[i].y1 for i in g)
        for li, x0, x1, top, base, H in bands:
            if li == k or li in absorbed or len(lines[li]) <= len(g) or gh > 0.6 * H:
                continue
            if top - 0.35 * H <= gy0 and gy1 <= base + 0.4 * H and x0 - 1.2 * H <= gx0 and gx1 <= x1 + 1.2 * H:
                lines[li].extend(g)
                absorbed.add(k)
                break
    lines = [g for k, g in enumerate(lines) if k not in absorbed]
    loose = []
    for i in singles:
        g = gl[i]
        solo = getattr(g, "solo", False)
        home = [(abs(g.cx - (x0 + x1) / 2), li) for li, x0, x1, top, base, H in bands
                if top - 0.35 * H <= g.y0 and g.y1 <= base + 0.4 * H
                and x0 - 1.2 * H <= g.cx <= x1 + 1.2 * H and g.h <= 1.3 * H]
        if home:
            li = min(home)[1]
            # bands index the ORIGINAL line list; absorbed lines were removed
            target = next((g for g in lines if g is _orig[li]), None)
            if target is not None:
                target.append(i)
            elif not solo:
                loose.append([i])
        elif not solo:
            loose.append([i])
    return lines + loose


def _cap(gl, idx) -> tuple[float, float]:
    """(cap height, baseline) of a line from its tallest glyphs."""
    hs = np.array([gl[i].h for i in idx])
    core = [i for i in idx if gl[i].h >= 0.6 * hs.max()]
    H = float(np.median([gl[i].h for i in core]))
    base = float(np.median([gl[i].y1 for i in core]))
    return H, base


# ----------------------------------------------------------------- 2. merge parts
def _merge_parts(gl: list[Glyph], idx: list[int]) -> list[Glyph]:
    seq = sorted((gl[i] for i in idx), key=lambda g: g.x0)
    out: list[list[Glyph]] = []
    for g in seq:
        if out:
            prev = out[-1]
            px0, px1 = min(p.x0 for p in prev), max(p.x1 for p in prev)
            py0, py1 = min(p.y0 for p in prev), max(p.y1 for p in prev)
            ov = min(px1, g.x1) - max(px0, g.x0)
            vov = (min(py1, g.y1) - max(py0, g.y0)) / max(min(py1 - py0, g.h), 1e-6)
            inside = px0 - 0.2 <= g.x0 and g.x1 <= px1 + 0.2 and py0 - 0.2 <= g.y0 and g.y1 <= py1 + 0.2
            # stacked parts (":" "É" "i" "=") or a piece inside the previous outline
            # (split "R"); side-by-side touching characters (". :" in a tight font) are not
            if ov >= 0.5 * min(px1 - px0, g.w) and (vov <= 0.3 or inside):
                prev.append(g)
                continue
        out.append([g])
    merged = [p[0] if len(p) == 1 else merge(p) for p in out]
    # two adjacent top ticks are one double quote
    H, base = _cap(merged, list(range(len(merged))))
    res: list[Glyph] = []
    for g in merged:
        if res:
            a = res[-1]
            tick = lambda q: q.h < 0.5 * H and q.y1 < base - 0.45 * H
            if tick(a) and tick(g) and g.x0 - a.x1 < 0.3 * H:
                res[-1] = merge([a, g])
                continue
        res.append(g)
    return res


# ----------------------------------------------------------------- 3. score
def _candidates(line: list[Glyph]) -> tuple[list[list[tuple[str, float]]], float, float]:
    b = bank()
    H, base = _cap(line, list(range(len(line))))
    S = scores(line)
    rh = np.array([g.h / H for g in line])[:, None]
    rt = np.array([(g.y0 - (base - H)) / H for g in line])[:, None]
    S = S - 0.5 * np.abs(rh - b.height[None, :]) - 0.35 * np.abs(rt - b.top[None, :])
    out = []
    for row in S:
        best: dict[str, float] = {}
        for j in np.argsort(-row)[:200]:
            c = b.chars[j]
            if c not in best:
                best[c] = float(row[j])
                if len(best) == K:
                    break
        out.append(sorted(best.items(), key=lambda t: -t[1]))
    return out, H, base


# ----------------------------------------------------------------- 4. tokens + context
def _words(line: list[Glyph], H: float) -> list[list[int]]:
    """Split where a gap stands out from THIS line's letter spacing. Measured against
    the truth: letter gaps are 0.10 H in normal fonts but 0.29 H in CLP's condensed
    Times, while word gaps start at 0.33 H there - no global threshold separates them,
    a per-line one does (most gaps in a line are letter gaps)."""
    if not line:
        return []
    gaps = [line[i + 1].x0 - line[i].x1 for i in range(len(line) - 1)]
    if len(gaps) >= 3:
        base = float(np.median(gaps))
        thr = max(base + 0.15 * H, 1.7 * base, 0.18 * H)
    else:
        thr = 0.3 * H
    words, cur = [], [0]
    for i, gap in enumerate(gaps):
        if gap > thr:
            words.append(cur)
            cur = []
        cur.append(i + 1)
    words.append(cur)
    return words


def _digit_alt(c, slack):
    best_ch, best_s = c[0]
    if best_ch not in _LOOKALIKE_DIGIT:
        return None
    d = next(((ch, s) for ch, s in c if ch.isdigit()), None)
    return d[0] if d and d[1] >= best_s - slack else None


def _decode_token(cands: list[list[tuple[str, float]]]) -> str:
    chosen = [c[0][0] for c in cands]
    digitish = sum(any(ch.isdigit() and s >= c[0][1] - 0.06 for ch, s in c) for c in cands)
    numeric = digitish >= max(1, 0.6 * len(cands))
    for i, c in enumerate(cands):
        # a look-alike becomes a digit inside a number, or right after a digit ("4O" in
        # "40MPa"); a mark like 25Z13-05 keeps its letters because Z is no look-alike
        after_digit = i > 0 and chosen[i - 1].isdigit()
        if numeric or after_digit:
            d = _digit_alt(c, 0.08 if numeric else 0.05)
            if d:
                chosen[i] = d
    text = "".join(chosen)
    # closed bar vocabulary: a designator slot is M whenever M is a candidate
    for m in _DESIG_SLOT.finditer(text):
        k = m.end()
        if text[k] != "M" and any(ch == "M" for ch, _ in cands[k]):
            chosen[k] = "M"
            text = "".join(chosen)
    return text


def decode_page(page) -> list[DecodedLine]:
    """All horizontal text lines of an outlined page, left-to-right, top-to-bottom."""
    gl = page_glyphs(page)
    out: list[DecodedLine] = []
    for idx in _lines(gl):
        line = _merge_parts(gl, idx)
        if not line:
            continue
        cands, H, base = _candidates(line)
        conf = float(np.mean([c[0][1] for c in cands]))
        if conf < MIN_LINE_CONF:
            continue                 # arrowheads, hatch ticks, symbols chained into a "line"
        words = _words(line, H)
        toks = []
        for w in words:
            t = _decode_token([cands[i] for i in w])
            gs = [line[i] for i in w]
            toks.append((min(g.x0 for g in gs), min(g.y0 for g in gs),
                         max(g.x1 for g in gs), max(g.y1 for g in gs), t))
        out.append(DecodedLine(min(g.x0 for g in line), min(g.y0 for g in line),
                               max(g.x1 for g in line), max(g.y1 for g in line),
                               " ".join(t[4] for t in toks), H, round(conf, 3), line, toks))
    out.sort(key=lambda l: (round(l.y0 / 4), l.x0))
    return out
