"""Shear walls (S-400 elevations) - plan side.

Measured on all four projects:
- each view is titled by a large letter + ``ÉLÉVATION`` under it (``A``, ``B``, ``A.1``);
- storey labels (``NIVEAU 2``, ``REZ-DE-CHAUSSÉE``, ``SOUS-SOL S1``) run down the left of
  each view group - per group, since EspCa3B's groups show different storeys;
- reinforcement is the column grammar: ``ARM.: 8-30M`` over ``LIG.: 10M@7" c/c``
  (LIGREP writes ``ARM:``), plus ``ARM. & LIG.:`` for distributed web bars;
- the web of each storey panel carries its distributed steel as a pair of lines written
  mid-wall, ``H.:15M@9"`` over ``V.:10M@8"`` (``+GOUJ`` on the lowest panel).

The answer key locates a wall finding as ``élévation B - RDC @ 2``: view, then the storey
span bottom @ top. That string is the element; the nearest vertical grid line (which
end of the wall) goes in the id so the two boundary elements of one span stay distinct.
"""

from __future__ import annotations

import re

from ..geometry.gridlines import extract_lines
from ..model import Armature, Debug, ElementRecord
from ..page import PreparedPage
from ..units import UnitSystem, parse_spacing

_LEVEL = re.compile(
    r"^(NIVEAU\s+\d+|REZ-DE-CHAUSS[ÉE]E|RDC|SOUS-SOL(\s+S\d)?|TOIT(\s+APPENTIS)?(\s+-\s+PT\.\s*HT\.)?"
    r"|APPENTIS|MEZZANINE|P\d|S\d)$")
_ARM = re.compile(r"^ARM\.?\s*:\s*(\d+)-(\d{2}M)(.*)$")
_LIG = re.compile(r"^LIG\.?\s*:\s*(?:(?P<q>\d+)\s*-\s*)?(?P<size>\d{2}M)\s*@\s*(.+)$")
_WEB = re.compile(r"^(?P<dir>[HV])\s*\.?\s*:\s*(?P<size>\d{2}M)\s*@\s*(.+)$")
_BOTH = re.compile(r"^ARM\.?\s*&\s*LIG\.?\s*:?\s*(.*)$")
_SPACED = re.compile(r"(\d{2}M)\s*@\s*\S+")


def short_level(name: str) -> str:
    """The answer key's spelling: ``REZ-DE-CHAUSSÉE`` -> ``RDC``, ``NIVEAU 2`` -> ``2``."""
    n = name.upper()
    if n.startswith("NIVEAU"):
        return n.split()[-1]
    if n.startswith("REZ") or n == "RDC":
        return "RDC"
    if n.startswith("SOUS-SOL"):
        parts = n.split()
        return parts[1] if len(parts) > 1 else "SS"
    if "APPENTIS" in n:
        return "APPENTIS"
    if n.startswith("TOIT"):
        return "TOIT"
    return n


def _titles(page: PreparedPage) -> list[tuple[str, float, float]]:
    """(view name, centre x, y) for every ``<big letter> ÉLÉVATION`` title."""
    out = []
    words = page.words
    for w in words:
        if w.text not in ("ÉLÉVATION", "ELEVATION") or (w.y1 - w.y0) < 12:
            continue
        prev = [o for o in words if abs(o.cy - w.cy) < 8 and 0 < w.x0 - o.x1 < 30
                and (o.y1 - o.y0) > 1.4 * (w.y1 - w.y0)]
        if prev:
            name = max(prev, key=lambda o: o.x1).text
            out.append((name, w.cx, w.cy))
    return out


def _levels(page: PreparedPage) -> list[tuple[str, float, float]]:
    return [(l.text, l.x0, l.cy) for l in page.lines if not l.vertical and _LEVEL.match(l.text)]


def assign_views(titles, centres, merge: float = 60.0):
    """Callout -> view name, for every callout centre (x, y).

    Callouts stack in vertical columns, two per view (one at each wall end) on CLP, and a
    title is centred under its view. Nearest-title fails at the seams (CLP S-400: view A's
    right end is 410 pt from title A and 415 pt from title B), so instead the sorted
    callout columns of each row of views are split into CONTIGUOUS groups, one per title,
    minimising |group centre - title x| (exact DP; an empty group costs half a view).
    """
    if not titles:
        return [None] * len(centres)
    # rows of views: titles within 60 pt in y; a callout belongs to the row titled below it
    rows: list[list] = []
    for t in sorted(titles, key=lambda t: t[2]):
        if rows and abs(t[2] - rows[-1][0][2]) < 60:
            rows[-1].append(t)
        else:
            rows.append([t])
    row_of = []
    for x, y in centres:
        below = [r for r in rows if r[0][2] > y - 5]
        row_of.append(min(range(len(rows)), key=lambda k: rows[k][0][2]) if not below
                      else rows.index(min(below, key=lambda r: r[0][2])))

    out: list[str | None] = [None] * len(centres)
    for k, row in enumerate(rows):
        row = sorted(row, key=lambda t: t[1])
        idx = [i for i, r in enumerate(row_of) if r == k]
        if not idx:
            continue
        # callout columns: x-centres within `merge` pt (0 for beams, whose neighbours'
        # callouts can sit 25 pt apart)
        cols: list[list[int]] = []
        for i in sorted(idx, key=lambda i: centres[i][0]):
            if cols and centres[i][0] - centres[cols[-1][-1]][0] < max(merge, 0.5):
                cols[-1].append(i)
            else:
                cols.append([i])
        xs = [sum(centres[i][0] for i in c) / len(c) for c in cols]
        n, m = len(cols), len(row)
        # an empty view must cost something, or one wide group centred on a middle title
        # swallows the row; half the title spacing (most views do carry callouts)
        gaps = [b[1] - a[1] for a, b in zip(row, row[1:])]
        empty = 0.5 * (sorted(gaps)[len(gaps) // 2] if gaps else 400.0)
        INF = float("inf")
        f = [[INF] * (m + 1) for _ in range(n + 1)]
        back = [[0] * (m + 1) for _ in range(n + 1)]
        f[0][0] = 0.0
        for j in range(1, m + 1):
            for i in range(n + 1):
                for s in range(i + 1):             # columns s..i-1 go to title j-1
                    if f[s][j - 1] == INF:
                        continue
                    cost = empty if s == i else abs((xs[s] + xs[i - 1]) / 2 - row[j - 1][1])
                    if f[s][j - 1] + cost < f[i][j]:
                        f[i][j], back[i][j] = f[s][j - 1] + cost, s
        i = n
        for j in range(m, 0, -1):
            s = back[i][j]
            for c in cols[s:i]:
                for ci in c:
                    out[ci] = row[j - 1][0]
            i = s
    return out


def _spans(page: PreparedPage, titles) -> dict[str, tuple[float, float]]:
    """Each view's drawn x-extent: the longest horizontal stroke above its title that
    spans the title's centre and no neighbouring title's centre (storey lines run across
    several views and are excluded). WP2 S-400: A = 378-528, B = 682-1270, C = 1446-1596."""
    segs = []
    for d in page.drawings:
        for it in d["items"]:
            if it[0] == "l" and abs(it[1].y - it[2].y) < 0.5 and abs(it[1].x - it[2].x) >= 40:
                segs.append((it[1].y, min(it[1].x, it[2].x), max(it[1].x, it[2].x)))
            elif it[0] == "re" and it[1].width >= 40:
                segs += [(it[1].y0, it[1].x0, it[1].x1), (it[1].y1, it[1].x0, it[1].x1)]
    out = {}
    for name, cx, cy in titles:
        rivals = [t[1] for t in titles if t[0] != name and abs(t[2] - cy) < 60]
        top = max((t[2] for t in titles if t[2] < cy - 60), default=0.0)
        best = None
        for y, x0, x1 in segs:
            if not (top < y < cy - 10 and x0 <= cx <= x1) or any(x0 < r < x1 for r in rivals):
                continue
            if best is None or x1 - x0 > best[1] - best[0]:
                best = (x0, x1)
        if best:
            out[name] = best
    return out


def _owners(titles, spans, centres):
    """Callout -> view: the view whose drawn wall it sits in or beside (callouts are
    written just outside each wall end, web steel just left of the wall). The contiguous
    -group assignment is the fallback for a row where a wall outline was not found: it
    reads LIGREP S-400 right only by a 1 pt margin once web callouts join in."""
    fallback = assign_views(titles, centres)
    out = []
    for (x, y), fb in zip(centres, fallback):
        below = [t for t in titles if t[2] > y - 5] or titles
        row_y = min((t[2] for t in below), default=None)
        row = [t for t in below if abs(t[2] - row_y) < 60]
        found = sorted(spans[t[0]] for t in row if t[0] in spans)
        if (not row or len(found) < len(row)
                or any(b[0] < a[1] for a, b in zip(found, found[1:]))):
            out.append(fb)               # an outline missing, or two walls overlapping:
            continue                     # the strokes found are not the walls
        out.append(min(row, key=lambda t: max(spans[t[0]][0] - x, 0.0, x - spans[t[0]][1]))[0])
    return out


def _band_for(levels, x, y):
    """(bottom, top) storey labels around y, from the label column serving this x."""
    if not levels:
        return None, None
    # one label column = labels whose left edges are within 80 pt (CLP's TOIT sits 23 pt
    # right of its NIVEAU labels); columns are separated by whole views
    groups: list[list] = []
    for l in sorted(levels, key=lambda l: l[1]):
        if groups and l[1] - groups[-1][-1][1] < 80:
            groups[-1].append(l)
        else:
            groups.append([l])
    # a storey column has several labels; a lone "TOIT APPENTIS" over one view is not one
    real = [g for g in groups if len(g) >= 3] or groups
    left = [g for g in real if g[0][1] <= x + 10]
    labs = left[-1] if left else real[0]
    above = [l for l in labs if l[2] <= y]
    below = [l for l in labs if l[2] > y]
    top = max(above, key=lambda l: l[2])[0] if above else None
    bottom = min(below, key=lambda l: l[2])[0] if below else None
    return bottom, top


def _tie(m: re.Match, text: str, system: UnitSystem) -> Armature:
    """``LIG.: 10M@7" c/c``; EspCa3B also counts the legs, ``LIG.: 3-10M@200 c/c``."""
    return Armature(quantite=int(m.group("q")) if m.group("q") else None,
                    diametre=m.group("size"), espacement_mm=parse_spacing(text, system))


def _web(page: PreparedPage, system: UnitSystem):
    """Distributed web steel: an ``H.:`` line and the ``V.:`` line stacked with it are one
    callout, horizontal bars first whichever is written on top (CLP's view C writes V
    over H); a line left alone stands on its own."""
    lines = [l for l in page.lines if not l.vertical and l.x0 < 0.82 * page.width]
    hits = [(l, m) for l in lines if (m := _WEB.match(l.text.strip()))]
    used: set[int] = set()
    out = []
    for i, (l, m) in enumerate(hits):
        if i in used:
            continue
        used.add(i)
        group = [(l, m)]
        for j, (o, om) in enumerate(hits):
            if (j not in used and om.group("dir") != m.group("dir")
                    and 0 < abs(o.cy - l.cy) < 16 and abs(o.x1 - l.x1) < 40):
                group.append((o, om))
                used.add(j)
                break
        group.sort(key=lambda g: g[1].group("dir"))            # H before V
        out.append(([min(g.x0 for g, _ in group), min(g.y0 for g, _ in group),
                     max(g.x1 for g, _ in group), max(g.y1 for g, _ in group)],
                    [Armature(diametre=gm.group("size"),
                              espacement_mm=parse_spacing(g.text, system)) for g, gm in group],
                    [g.text for g, _ in group],
                    ["horizontale" if gm.group("dir") == "H" else "verticale" for _, gm in group]))
    return out


def _callouts(page: PreparedPage, system: UnitSystem):
    """Pair each ARM line with the LIG line directly under it (the column grammar)."""
    lines = [l for l in page.lines if not l.vertical and l.x0 < 0.82 * page.width]
    used: set[int] = set()
    out = []
    for i, l in enumerate(lines):
        m = _ARM.match(l.text)
        if not m:
            continue
        bars = [Armature(quantite=int(m.group(1)), diametre=m.group(2))]
        raw = [l.text]
        box = [l.x0, l.y0, l.x1, l.y1]
        for j, o in enumerate(lines):
            if j in used or j == i:
                continue
            if 0 < o.cy - l.cy < 16 and abs(o.x1 - l.x1) < 40 and (lm := _LIG.match(o.text)):
                bars.append(_tie(lm, o.text, system))
                raw.append(o.text)
                box = [min(box[0], o.x0), box[1], max(box[2], o.x1), o.y1]
                used.add(j)
                break
        used.add(i)
        out.append((box, bars, raw))
    for i, l in enumerate(lines):
        if i in used:
            continue
        if lm := _LIG.match(l.text):
            out.append(([l.x0, l.y0, l.x1, l.y1],
                        [_tie(lm, l.text, system)], [l.text]))
        elif bm := _BOTH.match(l.text):
            vals = bm.group(1).strip()
            nxt = [o for o in lines if 0 < o.cy - l.cy < 30 and abs(o.x0 - l.x0) < 60
                   and _SPACED.search(o.text)]
            text = vals if _SPACED.search(vals) else (nxt[0].text if nxt else "")
            sm = _SPACED.search(text)
            if sm:
                out.append(([l.x0, l.y0, l.x1, l.y1],
                            [Armature(diametre=sm.group(1), espacement_mm=parse_spacing(text, system))],
                            [l.text, text]))
    return out


def extract(page: PreparedPage, system: UnitSystem) -> tuple[list[ElementRecord], dict]:
    titles, levels = _titles(page), _levels(page)
    grid = extract_lines(page)          # elevations rarely have 2 letter + 2 number lines
    verticals = [g for g in grid.lines if g.orient == "v"]
    diag = {"views": sorted({t[0] for t in titles}), "level_labels": len(levels),
            "source": grid.evidence.get("source"), "warnings": []}
    if not titles:
        diag["warnings"].append("no elevation titles found")
    records: list[ElementRecord] = []
    seen: dict[str, int] = {}
    callouts = _callouts(page, system)
    centres = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b, _, _ in callouts]
    web = _web(page, system)
    web_centres = [((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) for b, *_ in web]
    owners = _owners(titles, _spans(page, titles), centres + web_centres)
    views, web_views = owners[:len(centres)], owners[len(centres):]
    for (box, bars, raw), (cx, cy), view in zip(callouts, centres, views):
        bottom, top = _band_for(levels, box[0], cy)
        if view and (bottom or top):
            span = f"{short_level(bottom) if bottom else 'FONDATION'} @ {short_level(top) if top else 'TOIT'}"
            element = f"élévation {view} - {span}"
            conf = 0.9 if (bottom and top) else 0.7
        else:
            element, conf = "UNKNOWN", 0.3
        near = [g for g in verticals if g.covers(cy, 60)]
        end = min(near, key=lambda g: abs(g.c - cx)).label if near else None
        key = f"{element}@{end}"
        seen[key] = seen.get(key, 0) + 1
        suffix = f"_{end}" if end else ""
        suffix += f"#{seen[key]}" if seen[key] > 1 else ""
        records.append(ElementRecord(
            id=f"{page.sheet_id}_{element}{suffix}_plan", source="plan",
            fichier=page.fichier, feuillet=page.sheet_id, page=page.index + 1,
            x=cx, y=cy, type_element="mur_refend", element=element, armature=bars,
            debug=Debug(raw=raw, confidence=conf, locator_kind="elevation" if view else "unknown",
                        niveau=short_level(bottom) if bottom else None, grid_line=end),
        ))
    for (box, bars, raw, roles), (cx, cy), view in zip(web, web_centres, web_views):
        bottom, top = _band_for(levels, box[0], cy)
        if view and (bottom or top):
            span = f"{short_level(bottom) if bottom else 'FONDATION'} @ {short_level(top) if top else 'TOIT'}"
            element = f"élévation {view} - {span}"
            conf = 0.85 if (bottom and top) else 0.65
        else:
            element, conf = "UNKNOWN", 0.3
        key = f"{element}@âme" + (roles[0] if len(roles) == 1 else "")
        seen[key] = seen.get(key, 0) + 1
        # the id is the only place the output JSON tells web steel from a boundary zone,
        # and a lone line's direction from the other
        suffix = "_âme" + (f"-{roles[0][0].upper()}" if len(roles) == 1 else "")
        suffix += f"#{seen[key]}" if seen[key] > 1 else ""
        records.append(ElementRecord(
            id=f"{page.sheet_id}_{element}{suffix}_plan", source="plan",
            fichier=page.fichier, feuillet=page.sheet_id, page=page.index + 1,
            x=cx, y=cy, type_element="mur_refend", element=element, armature=bars,
            debug=Debug(raw=raw, confidence=conf, locator_kind="elevation" if view else "unknown",
                        niveau=short_level(bottom) if bottom else None, role="âme",
                        directions=roles),
        ))
    diag["web"] = len(web)
    diag["records"] = len(records)
    diag["located"] = sum(r.element != "UNKNOWN" for r in records)
    return records, diag
