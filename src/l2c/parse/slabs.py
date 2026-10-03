"""Slab reinforcement (S-600 series) - plan side.

Same grammar in all four projects (measured):
    16(8)            count(count in the column strip); bar size from the sheet note
    12(9)-20M        same, size stated
    4-20M            plain count-size;  ``3-20M ADD. HT.`` = additional top bars
and the sheet note ``BARRE D'ARMATURE TYPIQUE (S.I.C.) : 15M`` gives the size when a
callout omits it. Callouts sit at the column they reinforce (CLP S-603: ``16(8)`` is
25 pt from the J-15 intersection - answer-key row 6), so each is located on the grid.
"""

from __future__ import annotations

import re

from ..geometry.grid import locator
from ..model import Armature, Debug, ElementRecord
from ..page import PreparedPage
from ..units import BAR_DESIGNATORS, UnitSystem

_NM = re.compile(r"^(\d+)\s*\((\d+)\)\s*(?:-\s*(\d{2})\s*M)?\s*$")
_NS = re.compile(r"^(\d+)\s*-\s*(\d{2})\s*M\b\s*(.*)$")
_TYPICAL = re.compile(r"ARMATURE TYPIQUE.*?:\s*(\d{2}M)")


def typical_bar(page: PreparedPage) -> str | None:
    for l in page.lines:
        if m := _TYPICAL.search(l.text):
            return m.group(1)
    return None


def extract(page: PreparedPage, system: UnitSystem) -> tuple[list[ElementRecord], dict]:
    grid = locator(page)
    typ = typical_bar(page)
    diag = {"source": grid.evidence.get("source"), "letter_role": grid.letter_role,
            "typical_bar": typ, "warnings": []}
    if typ is None:
        diag["warnings"].append("no typical-bar note; size-less callouts carry no diameter")

    records: list[ElementRecord] = []
    seen: dict[str, int] = {}
    for l in page.lines:
        if l.x0 > 0.82 * page.width or (l.x0 > 0.60 * page.width and l.y0 > 0.78 * page.height):
            continue                                   # notes column / title block
        text = l.text.strip()
        if m := _NM.match(text):
            size = f"{m.group(3)}M" if m.group(3) else typ
            bars = [Armature(quantite=int(m.group(1)),
                             diametre=size if size in BAR_DESIGNATORS else None)]
            stated = bool(m.group(3))
        elif (m := _NS.match(text)) and f"{m.group(2)}M" in BAR_DESIGNATORS:
            bars = [Armature(quantite=int(m.group(1)), diametre=f"{m.group(2)}M")]
            stated = True
        else:
            continue
        element, cost = grid.locate(l.cx, l.cy)
        key = element or "UNKNOWN"
        seen[key] = seen.get(key, 0) + 1
        conf = max(0.0, 1.0 - 0.6 * cost - (0.0 if stated or typ else 0.2))
        records.append(ElementRecord(
            id=f"{page.sheet_id}_{key}_{seen[key]}_plan", source="plan",
            fichier=page.fichier, feuillet=page.sheet_id, page=page.index + 1,
            x=l.cx, y=l.cy, type_element="dalle", element=key, armature=bars,
            debug=Debug(raw=[text] + ([] if stated else [f"(taille typique {typ})"]),
                        confidence=round(conf, 3),
                        locator_kind="grid" if element else "unknown", niveau=page.niveau,
                        direction="vertical" if l.vertical else "horizontal"),
        ))
    diag["records"] = len(records)
    diag["located"] = sum(r.element != "UNKNOWN" for r in records)
    return records, diag
