"""Column (S-500 series) extraction - plan side.

Callout shape, identical in all four projects (PLAN SS5.6):
    COL. 16"x24"        <- stated section size
    ARM.: 4-25M         <- vertical bars: quantity-designator
    LIG.: 10M@6" c/c    <- ties: designator@spacing
    BETON: 25MPa / N
Variants met on the corpus: a round section (``COL. 500mmØ``), a size split by its
fraction (``16`` + ``1/4"x21"``), and labels / values split into several tokens on some
WP2 sheets (``ARM.`` ``:`` ``4-20M``, ``LIG.`` ``:`` ``10M`` ``@100``).
"""

from __future__ import annotations

import re

import numpy as np

from ..geometry.grid import locator
from ..geometry.symbols import (calibrate_scale, candidate_symbols, matches_dimensions,
                                parse_dimensions)
from ..model import Armature, Debug, ElementRecord
from ..column_records import align_column_records
from ..page import PreparedPage
from ..units import UnitSystem, parse_spacing
from ._assoc import UNREACHABLE, assign, runner_up_ratio

_ARM = re.compile(r"^(\d+)-(\d{2}M)$")
_LIG = re.compile(r"^(\d{2}M)@")
#: a callout sits beside its column: measured edge distance is <= 88 pt on every true
#: pair of the four projects. Without a limit, one callout whose symbol is missing takes
#: its neighbour's and shifts the whole bay (WP2 S-501: 19 pairs 113-425 pt apart).
_REACH = 100.0
_ARM_IN = re.compile(r"\b\d+-\d{2}M\b")
_LIG_IN = re.compile(r"\b\d{2}M@\S+")


def _dim_text(vals: list[str]) -> str | None:
    """The stated size among the tokens after ``COL.``; a mixed fraction splits it in
    two (``16`` ``1/4"x21"``), so runs of 2-3 tokens are tried too."""
    for n in (1, 2, 3):
        for i in range(len(vals) - n + 1):
            text = " ".join(vals[i:i + n])
            if parse_dimensions(text):
                return text
    return None


def _typical_offset(offsets: list[tuple[float, float]], tol: float = 8.0,
                    share: float = 0.7) -> tuple[float, float] | None:
    """Median callout-centre -> symbol-centre offset, only when the sheet agrees on it:
    at least `share` of the pairs within `tol` pt of the median (else None)."""
    if len(offsets) < 10:
        return None
    mx = float(np.median([o[0] for o in offsets]))
    my = float(np.median([o[1] for o in offsets]))
    close = sum(abs(o[0] - mx) <= tol and abs(o[1] - my) <= tol for o in offsets)
    return (round(mx, 1), round(my, 1)) if close >= share * len(offsets) else None


def _split_value(vals: list, pattern: re.Pattern) -> str | None:
    """A value written as several tokens (``:`` ``10M`` ``@100``), glued back."""
    text = re.sub(r"\s*@\s*", "@", " ".join(w.text for w in vals))
    m = pattern.search(text)
    return m.group(0) if m else None


def _same_column_block(page: PreparedPage, anchor, span: float = 32.0):
    """Tokens belonging to one callout: same left edge, within `span` below the COL. line."""
    return [
        w for w in page.words
        if abs(w.x0 - anchor.x0) < 2.5 and 0 <= w.y0 - anchor.y0 <= span
    ]


def _tokens_right_of(page: PreparedPage, label, max_dx: float = 60.0) -> list:
    """Value tokens on the same line as a label. Returned as Words because they
    define the right edge of the callout block, which the association depends on."""
    return sorted(
        (w for w in page.words
         if abs(w.cy - label.cy) < 3 and w.x0 >= label.x1 - 1 and w.x0 - label.x1 < max_dx),
        key=lambda w: w.x0,
    )


def _value_right_of(page: PreparedPage, label, max_dx: float = 60.0) -> list[str]:
    return [w.text for w in _tokens_right_of(page, label, max_dx)]


def extract(page: PreparedPage, system: UnitSystem) -> tuple[list[ElementRecord], dict]:
    grid = locator(page)
    anchors = [w for w in page.words if w.text == "COL."]
    diag = {
        "anchors": len(anchors),
        "grid_ok": grid.ok,
        "grid_letters": len(grid.letters),
        "grid_numbers": len(grid.numbers),
        "letter_role": grid.letter_role,
        "source": grid.evidence.get("source"),
        "warnings": [],
    }
    if not anchors:
        return [], diag

    # ---- 1. assemble callout blocks (labels AND their values: the values define the
    #         block's right edge, which the association depends on)
    blocks = []
    for a in anchors:
        labels = _same_column_block(page, a)
        members = list(labels) + _tokens_right_of(page, a)
        dim_txt = _dim_text(_value_right_of(page, a))
        arm = lig = None
        for t in labels:
            if t.text in ("ARM.:", "ARM."):
                vals = _tokens_right_of(page, t)
                members += vals
                arm = (next((w.text for w in vals if _ARM.match(w.text)), None)
                       or _split_value(vals, _ARM_IN))
            elif t.text in ("LIG.:", "LIG."):
                vals = _tokens_right_of(page, t)
                members += vals
                lig = (next((w.text for w in vals if _LIG.match(w.text)), None)
                       or _split_value(vals, _LIG_IN))
        if not (dim_txt or arm or lig):
            continue      # "℄ COL." on a dimension string (WP2 floor plans): no reinforcement
        xs = [w.x0 for w in members] + [w.x1 for w in members]
        ys = [w.y0 for w in members] + [w.y1 for w in members]
        blocks.append({
            "dim_txt": dim_txt, "arm": arm, "lig": lig,
            "bbox": (min(xs), min(ys), max(xs), max(ys)),
            "dims": parse_dimensions(dim_txt) if dim_txt else None,
        })

    diag["callouts"] = len(blocks)
    if not blocks:
        return [], diag

    # ---- 2. detect symbols and self-calibrate the drawing scale against stated sizes
    symbols = candidate_symbols(page)
    # a disc's bounding box is square: it would calibrate against every square symbol
    scale = calibrate_scale(symbols, [b["dims"] for b in blocks
                                      if b["dims"] and "Ø" not in b["dim_txt"]])
    diag["scale_pt_per_mm"] = scale
    diag["scale_pt_per_inch"] = round(scale * 25.4, 4) if scale else None
    diag["symbols"] = len(symbols)
    if scale is None:
        diag["warnings"].append("scale calibration failed; locators fall back to callout position")

    # ---- 3. global min-cost assignment of callouts to dimension-compatible symbols
    costs = np.full((len(blocks), len(symbols)), UNREACHABLE)
    if scale:
        for i, b in enumerate(blocks):
            if not b["dims"]:
                continue
            for j, sym in enumerate(symbols):
                if matches_dimensions(sym, b["dims"], scale):
                    d = sym.rect_distance(*b["bbox"])
                    if d <= _REACH:
                        costs[i, j] = d
    pairing = assign(costs)
    diag["assigned"] = len(pairing)
    if len(pairing) < len(blocks):
        diag["warnings"].append(
            f"{len(blocks) - len(pairing)} callouts had no dimension-matching symbol in reach"
        )

    # a callout left without a symbol (its column is drawn inside a wall, WP2 S-501 line
    # 24.1) still hangs off its column the way the sheet's other callouts do: when that
    # offset is the same across the sheet, it places the column better than the text does
    offset = _typical_offset([(symbols[j].cx - (blocks[i]["bbox"][0] + blocks[i]["bbox"][2]) / 2,
                               symbols[j].cy - (blocks[i]["bbox"][1] + blocks[i]["bbox"][3]) / 2)
                              for i, j in pairing.items()])
    diag["typical_offset"] = offset

    # ---- 4. build records
    records: list[ElementRecord] = []
    for i, b in enumerate(blocks):
        bx0, by0, bx1, by1 = b["bbox"]
        cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2     # Appendix A: centre of the annotation
        if i in pairing:
            sym = symbols[pairing[i]]
            sx, sy = sym.cx, sym.cy
            assoc_cost = runner_up_ratio(costs, i, pairing[i])
            locator_kind = "grid"
            reach = round(float(costs[i, pairing[i]]), 1)
        else:
            sx, sy = (cx + offset[0], cy + offset[1]) if offset else (cx, cy)
            assoc_cost, locator_kind, reach = 1.0, "unknown", None

        element, grid_cost = grid.locate(sx, sy)
        bars: list[Armature] = []
        if b["arm"]:
            m = _ARM.match(b["arm"])
            bars.append(Armature(quantite=int(m.group(1)), diametre=m.group(2)))
        if b["lig"]:
            m = _LIG.match(b["lig"])
            bars.append(Armature(diametre=m.group(1),
                                 espacement_mm=parse_spacing(b["lig"], system)))

        confidence = round(max(0.0, 1.0 - 0.5 * grid_cost - 0.2 * assoc_cost), 3)
        records.append(ElementRecord(
            id=f"{page.sheet_id}_{element or 'p%d#%d' % (page.index + 1, i)}_plan",
            source="plan",
            fichier=page.fichier,
            feuillet=page.sheet_id,
            page=page.index + 1,
            x=cx, y=cy,
            type_element="colonne",
            element=element or "UNKNOWN",
            armature=bars,
            debug=Debug(
                raw=[t for t in (b["dim_txt"], b["arm"], b["lig"]) if t],
                confidence=confidence,
                decode_path="text_layer",
                locator_kind=locator_kind if element else "unknown",
                niveau=page.niveau,
                symbol_bbox=(sx, sy, sx, sy),
                assoc_distance=reach,
            ),
        ))

    located = [r.element for r in records if r.element != "UNKNOWN"]
    diag["records"] = len(records)
    diag["located"] = len(located)
    diag["duplicate_locators"] = sorted({e for e in located if located.count(e) > 1})
    if diag["duplicate_locators"]:
        diag["warnings"].append(f"duplicate locators: {diag['duplicate_locators']}")
    return align_column_records(records), diag
