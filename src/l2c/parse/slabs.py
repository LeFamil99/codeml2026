"""Slab reinforcement (S-600 series) - plan side.

Same grammar in all four projects (measured):
    16(8)            count(parenthesized value); preserve the second value in debug
    12(9)-20M        same, size stated (also drawn ``12(5-20M)``, ``18(9)20-M``)
    4-20M            plain count-size;  ``3-20M ADD. HT.`` = additional top bars
and the sheet note ``BARRE D'ARMATURE TYPIQUE (S.I.C.) : 15M`` gives the size when a
callout omits it. Callouts sit at the column they reinforce (CLP S-603: ``16(8)`` is
25 pt from the J-15 intersection - answer-key row 6), so each is located on the grid.
Circled integrity labels are resolved separately through the plan's own detail
table by ``slab_integrity``; they are not ordinary numeric slab callouts.
"""

from __future__ import annotations

import re

from ..geometry.grid import locator
from ..model import Armature, Debug, ElementRecord
from ..page import PreparedPage
from ..units import BAR_DESIGNATORS, UnitSystem
from . import slab_integrity

_NM = re.compile(r"^(\d+)\s*\((\d+)\)\s*(?:-\s*(\d{2})\s*M|(\d{2})\s*-?\s*M)?\s*$")
_NM_IN = re.compile(r"^(\d+)\s*\((\d+)\s*-\s*(\d{2})\s*M\s*\)?\s*$")
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

    callouts: list[ElementRecord] = []
    seen: dict[str, int] = {}
    # the notes column / title block is skipped by position, but the plan itself can
    # run into it (LIGREP grid line 29 sits at 0.85-0.94 of the width, CLP's inset views
    # at the bottom right): a point is kept there only INSIDE a view of the drawn grid
    # (15 pt of slack, not the 120 pt used to name a callout beside its column)
    drawn = grid.evidence.get("source") == "grid_lines"
    for l in page.lines:
        if l.x0 > 0.82 * page.width or (l.x0 > 0.60 * page.width and l.y0 > 0.78 * page.height):
            if not (drawn and grid.locate(l.cx, l.cy, margin=15.0)[0]):
                continue
        text = l.text.strip()
        parenthesized_count = None
        if m := (_NM.match(text) or _NM_IN.match(text)):
            stated_size = m.group(3) or (m.group(4) if m.re is _NM else None)
            size = f"{stated_size}M" if stated_size else typ
            bars = [Armature(quantite=int(m.group(1)),
                             diametre=size if size in BAR_DESIGNATORS else None)]
            stated = bool(stated_size)
            parenthesized_count = int(m.group(2))
        elif (m := _NS.match(text)) and f"{m.group(2)}M" in BAR_DESIGNATORS:
            bars = [Armature(quantite=int(m.group(1)), diametre=f"{m.group(2)}M")]
            stated = True
        else:
            continue
        element, cost = grid.locate(l.cx, l.cy)
        key = element or "UNKNOWN"
        seen[key] = seen.get(key, 0) + 1
        conf = max(0.0, 1.0 - 0.6 * cost - (0.0 if stated or typ else 0.2))
        callouts.append(ElementRecord(
            id=f"{page.sheet_id}_{key}_{seen[key]}_plan", source="plan",
            fichier=page.fichier, feuillet=page.sheet_id, page=page.index + 1,
            x=l.cx, y=l.cy, type_element="dalle", element=key, armature=bars,
            debug=Debug(raw=[text] + ([] if stated else [f"(taille typique {typ})"]),
                        confidence=round(conf, 3),
                        locator_kind="grid" if element else "unknown", niveau=page.niveau,
                        direction="vertical" if l.vertical else "horizontal",
                        reinforcement_kind="slab",
                        parenthesized_count=parenthesized_count),
        ))
    sizeless = sum(1 for r in callouts if r.armature[0].diametre is None)
    if sizeless:      # a sheet whose callouts all state their size needs no note (S-600C)
        diag["warnings"].append(f"no typical-bar note; {sizeless} size-less callouts carry no diameter")
    integrity, integrity_diag = slab_integrity.extract(page, grid)
    # Only the circled integrity steel is extracted: the DA reads only the integrity sheet.
    # General slab callouts stay out of the plan output until they can be compared.
    records = list(integrity)
    diag["integrity"] = integrity_diag
    diag["warnings"].extend(integrity_diag["warnings"])
    diag["records"] = len(records)
    diag["located"] = sum(r.element != "UNKNOWN" for r in records)
    return records, diag
