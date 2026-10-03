"""Mat foundation / radier (S-050, S-060) - plan side.

Grammar measured per project (the one type that varies):
    CLP      RANG 2:30M@11" c/c          layer on the same line
    WP2      35M @250 c/c  + a per-view direction legend;  2 RANGS DE 35M @175 c/c
    EspCa3B  22-35M;  2 LITS DE 35M @125c/c;  + the same legend
Several enlarged views repeat the grid on one sheet, which the line grid handles
(answer-key row 1: CLP S-050 J-10.8 ``RANG 2: 25M@11"``).
The layer is not a bar mark, so it stays out of ``repere`` and lives in debug.
"""

from __future__ import annotations

import re

from ..geometry.grid import locator
from ..model import Armature, Debug, ElementRecord
from ..page import PreparedPage
from ..units import BAR_DESIGNATORS, UnitSystem, parse_spacing

_SPACED = re.compile(
    r"^(?:RANG\s*(?P<rang>\d(?:\s*&\s*\d)?)\s*:\s*)?"
    r"(?:(?P<n>\d+)\s+(?:RANGS|LITS)\s+(?:DE\s+)?)?"
    r"(?P<size>\d{2})\s*M\s*@\s*(?P<sp>.+)$")
_COUNT = re.compile(r"^(?P<q>\d+)\s*-\s*(?P<size>\d{2})\s*M\b\s*(?P<rest>.*)$")
_LAYER = re.compile(r"^RANGS?\s*(\d(?:\s*&\s*\d)*)$")


def extract(page: PreparedPage, system: UnitSystem) -> tuple[list[ElementRecord], dict]:
    grid = locator(page)
    diag = {"source": grid.evidence.get("source"), "letter_role": grid.letter_role,
            "warnings": []}
    body = [l for l in page.lines
            if l.x0 < 0.82 * page.width and not (l.x0 > 0.60 * page.width and l.y0 > 0.78 * page.height)
            and not l.text.lstrip().startswith("-")]
    layers = [(l, _LAYER.match(l.text.strip()).group(1)) for l in body if _LAYER.match(l.text.strip())]

    records: list[ElementRecord] = []
    seen: dict[str, int] = {}
    for l in body:
        text = re.sub(r"\s+", " ", l.text.strip())
        rang = None
        if (m := _SPACED.match(text)) and f"{m.group('size')}M" in BAR_DESIGNATORS:
            bars = [Armature(diametre=f"{m.group('size')}M",
                             espacement_mm=parse_spacing(text, system))]
            rang = m.group("rang")
            if m.group("n"):
                rang = rang or f"{m.group('n')} lits"
        elif (m := _COUNT.match(text)) and f"{m.group('size')}M" in BAR_DESIGNATORS:
            bars = [Armature(quantite=int(m.group("q")), diametre=f"{m.group('size')}M")]
        else:
            continue
        inferred = False
        if rang is None:
            # WP2 / EspCa3B: each view carries a legend - "RANG 1 & 4" written in one
            # direction, "RANG 2 & 3" in the other (the pairing flips between views).
            # A callout's layer is the nearest legend entry written in ITS direction.
            same = [t for t in layers if t[0].vertical == l.vertical]
            if same:
                rang = min(same, key=lambda t: (t[0].cx - l.cx) ** 2 + (t[0].cy - l.cy) ** 2)[1]
                inferred = True
        element, cost = grid.locate(l.cx, l.cy)
        key = element or "UNKNOWN"
        seen[key] = seen.get(key, 0) + 1
        records.append(ElementRecord(
            id=f"{page.sheet_id}_{key}_{seen[key]}_plan", source="plan",
            fichier=page.fichier, feuillet=page.sheet_id, page=page.index + 1,
            x=l.cx, y=l.cy, type_element="radier", element=key, armature=bars,
            debug=Debug(raw=[text] + ([f"RANG {rang}"] if rang and "RANG" not in text else []),
                        confidence=round(max(0.0, 1.0 - 0.6 * cost - (0.1 if inferred else 0.0)), 3),
                        locator_kind="grid" if element else "unknown", niveau=page.niveau,
                        layer=rang, layer_from_legend=inferred, direction="vertical" if l.vertical else "horizontal"),
        ))
    diag["records"] = len(records)
    diag["located"] = sum(r.element != "UNKNOWN" for r in records)
    diag["with_layer"] = sum(r.debug.layer is not None for r in records)
    return records, diag
