"""Isolated footings (S-100) - plan side.

Same template in all four projects (measured): a schedule table
    NOMENCLATURE DES SEMELLES ISOLÉES
    TYPE | LONGUEUR | LARGEUR | ÉPAISSEUR | ARM. LONG. | ARM. TRANS.
    TYPE C | 11'-1" | 11'-1" | 2'-0" | 9-25M | 9-25M
(a cell can stack two lines: WP2 TYPE D is ``10-20M`` over ``+ ÉP. 15M@300``)
and, on the plan, an isolated type letter beside each footing (CLP L-13 -> ``C``).
A footing record = that mark's grid intersection + its schedule row's bars.
"""

from __future__ import annotations

import re

from ..geometry.grid import locator
from ..geometry.gridlines import is_bubble
from ..model import Armature, Debug, ElementRecord
from ..page import PreparedPage
from ..units import UnitSystem, parse_spacing

_QTY = re.compile(r"^(\d+)-(\d{2}M)$")
_SPC = re.compile(r"^(\d{2}M)@")


def _line(words, w, tol=3.0):
    return sorted((o for o in words if abs(o.cy - w.cy) < tol), key=lambda o: o.x0)


def parse_schedule(page: PreparedPage) -> tuple[dict[str, dict], tuple | None]:
    """TYPE -> {"long": token, "trans": token, "raw": [...]}; plus the table's bbox."""
    words = page.words
    title = next((w for w in words if w.text == "NOMENCLATURE"
                  and any(o.text.startswith("SEMELLE") for o in _line(words, w))), None)
    if title is None:
        return {}, None
    below = [w for w in words if 0 < w.cy - title.cy < 200 and abs(w.x0 - title.x0) < 40]
    header = next((w for w in sorted(below, key=lambda w: w.cy) if w.text == "TYPE"), None)
    if header is None:
        return {}, None
    hline = _line(words, header)
    # an ARM. column is centred under "ARM. LONG." / "ARM. TRANS."; values are centred too
    arm_cols = []
    for i, w in enumerate(hline):
        if w.text == "ARM.":
            nxt = hline[i + 1] if i + 1 < len(hline) else w
            arm_cols.append((w.x0 + nxt.x1) / 2)
    x_hi = max(w.x1 for w in hline) + 40

    rows: dict[str, dict] = {}
    y_last = header.cy
    heads = []
    for w in sorted(words, key=lambda w: w.cy):
        if w.text != "TYPE" or abs(w.x0 - header.x0) > 8 or w.cy <= header.cy + 2:
            continue
        if w.cy - y_last > 30:            # the table ended
            break
        heads.append(w)
        y_last = w.cy
    y_last = header.cy
    for n, w in enumerate(heads):
        line = [o for o in _line(words, w) if header.x0 - 5 <= o.x0 <= x_hi]
        if len(line) < 2:
            continue
        name = line[1].text
        # the row's band reaches halfway to its neighbours, so a two-line cell is whole
        up = (w.cy - (heads[n - 1].cy if n else header.cy)) / 2
        down = (heads[n + 1].cy - w.cy) / 2 if n + 1 < len(heads) else up
        band = sorted((o for o in words if -up < o.cy - w.cy < down
                       and line[1].x1 < o.x0 <= x_hi), key=lambda o: (round(o.cy), o.x0))
        cells: dict[int, list[str]] = {}
        for o in band:
            if not arm_cols or not (_QTY.match(o.text) or _SPC.match(o.text)):
                continue
            k = min(range(len(arm_cols)), key=lambda i: abs(arm_cols[i] - o.cx))
            if abs(arm_cols[k] - o.cx) < 40:
                cells.setdefault(k, []).append(o.text)
        rows[name] = {
            "long": " ".join(cells.get(0, [])) or None,
            "trans": " ".join(cells.get(1, [])) or None,
            "raw": [o.text for o in line],
        }
        y_last = w.cy
    bbox = (title.x0 - 20, title.y0 - 10, x_hi, y_last + 15)
    return rows, bbox


def _outline(page: PreparedPage, w, lo: float = 30.0, hi: float = 400.0):
    """Smallest drawn outline containing the mark: the footing itself. Its centre, not
    the mark (placed in a corner, ~70 pt off on CLP L-13), is what sits on the grid."""
    best = None
    for d in page.drawings:
        r = d["rect"]
        if len(d["items"]) < 4 or not (r.x0 <= w.cx <= r.x1 and r.y0 <= w.cy <= r.y1):
            continue
        if lo <= min(r.width, r.height) and max(r.width, r.height) <= hi:
            if best is None or r.width * r.height < best.width * best.height:
                best = r
    return best


def _pedestal(page: PreparedPage, w, reach: float = 80.0):
    """Fallback when the footing outline is not one closed path (WP2): the filled
    column/pedestal square the mark labels - footings are centred on their column."""
    best = None
    for d in page.drawings:
        r = d["rect"]
        if d.get("fill") is None or not (8 <= min(r.width, r.height) and max(r.width, r.height) <= 120):
            continue
        dx = max(r.x0 - w.cx, 0, w.cx - r.x1)
        dy = max(r.y0 - w.cy, 0, w.cy - r.y1)
        dist = (dx * dx + dy * dy) ** 0.5
        if dist <= reach and (best is None or dist < best[0]):
            best = (dist, r)
    return best[1] if best else None


def _bars(token: str | None, system: UnitSystem) -> list[Armature]:
    """Every entry of a schedule cell: the bars, then any ties stacked under them."""
    out = []
    for t in (token or "").split():
        if m := _QTY.match(t):
            out.append(Armature(quantite=int(m.group(1)), diametre=m.group(2)))
        elif m := _SPC.match(t):
            out.append(Armature(diametre=m.group(1), espacement_mm=parse_spacing(t, system)))
    return out


def extract(page: PreparedPage, system: UnitSystem) -> tuple[list[ElementRecord], dict]:
    grid = locator(page)
    schedule, tbox = parse_schedule(page)
    diag = {"source": grid.evidence.get("source"), "letter_role": grid.letter_role,
            "schedule_types": sorted(schedule), "warnings": []}
    if not schedule:
        diag["warnings"].append("no footing schedule found on this sheet")
        return [], diag

    def inside(w, box):
        return box and box[0] <= w.cx <= box[2] and box[1] <= w.cy <= box[3]

    words = page.words
    marks = []
    for w in words:
        if w.text not in schedule or inside(w, tbox):
            continue
        if any(o is not w and abs(o.cy - w.cy) < 6 and o.x0 - 10 < w.x1 and w.x0 < o.x1 + 10
               for o in words):
            continue      # part of a phrase ("TYPE A") or a glyph pile (WP2's centreline: C over L)
        if is_bubble(grid, w):
            continue
        marks.append(w)

    # footing marks share one text height (16.4 pt on CLP and WP2); stray letters do not
    if marks:
        hs = sorted(round(m.y1 - m.y0) for m in marks)
        mode = max(set(hs), key=lambda h: (hs.count(h), h))
        marks = [m for m in marks if abs((m.y1 - m.y0) - mode) <= 0.25 * mode]
        diag["mark_height"] = mode

    records: list[ElementRecord] = []
    for k, w in enumerate(marks):
        box = _outline(page, w) or _pedestal(page, w)
        fx, fy = ((box.x0 + box.x1) / 2, (box.y0 + box.y1) / 2) if box else (w.cx, w.cy)
        element, cost = grid.locate(fx, fy)
        if box is None:
            cost = min(1.0, cost + 0.3)
        row = schedule[w.text]
        bars = _bars(row["long"], system) + _bars(row["trans"], system)
        records.append(ElementRecord(
            id=f"{page.sheet_id}_{element or 'p%d#%d' % (page.index + 1, k)}_plan",
            source="plan", fichier=page.fichier, feuillet=page.sheet_id,
            page=page.index + 1, x=w.cx, y=w.cy, type_element="semelle",
            element=element or "UNKNOWN", armature=bars,
            debug=Debug(raw=[f"TYPE {w.text}", f"ARM. LONG.: {row['long']}",
                             f"ARM. TRANS.: {row['trans']}"],
                        confidence=round(max(0.0, 1.0 - 0.6 * cost), 3),
                        locator_kind="grid" if element else "unknown", niveau=page.niveau,
                        symbol_bbox=(box.x0, box.y0, box.x1, box.y1) if box else None),
        ))
    diag["records"] = len(records)
    diag["located"] = sum(r.element != "UNKNOWN" for r in records)
    diag["with_outline"] = sum(r.debug.symbol_bbox is not None for r in records)
    located = [r.element for r in records if r.element != "UNKNOWN"]
    dup = sorted({e for e in located if located.count(e) > 1})
    if dup:
        diag["warnings"].append(f"{len(dup)} grid cells carry more than one footing mark")
    return records, diag
