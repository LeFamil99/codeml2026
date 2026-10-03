"""DA beam elevations (``Poutres`` folders) - shop-drawing side.

CLP's fabricator titles each elevation exactly like the plan (``P108 -  16" x 40 3/8"``,
measured 11 such titles + ``POUTRE P103``), with bar lines above it:
    2 25M 19-03  /  15 10M 10TT16X35 @18"  /  3 15M 21-09 @8"C.F.
So the plan's beam geometry is reused as-is - the drawn span of each beam above its
title (``l2c.parse.beams``) - with the DA bar grammar. Geometry code only: nothing from
the plan's records is read.
"""

from __future__ import annotations

from collections import Counter

from ..model import Debug, ElementRecord
from ..page import PreparedPage
from ..parse.beams import _owners, _spans, _titles
from ..units import UnitSystem
from .common import parse_bar_line, sheet_identity


def extract(page: PreparedPage, system: UnitSystem, dafile) -> tuple[list[ElementRecord], dict]:
    feuillet, title = sheet_identity(page, dafile.name)
    titles = _titles(page)
    tri = [(t[0], t[1], t[2]) for t in titles]
    labels: Counter = Counter()
    callouts = []
    for l in page.lines:
        if l.x0 > 0.85 * page.width and l.y0 > 0.75 * page.height:
            continue
        b = parse_bar_line(l.text, system)
        if b is not None:
            callouts.append((l, b))
            labels[b.label or "(sans étiquette)"] += 1
    spans = _spans(page, tri)
    owners = _owners(tri, spans, [(l.cx, l.cy) for l, _ in callouts]) if tri else [None] * len(callouts)
    sections = {t[0]: t[3] for t in titles}

    records: list[ElementRecord] = []
    seen: Counter = Counter()
    for (l, b), mark in zip(callouts, owners):
        key = mark or "UNKNOWN"
        seen[key] += 1
        records.append(ElementRecord(
            id=f"{feuillet}_{key}_{seen[key]}_atelier", source="atelier", fichier=dafile.name,
            feuillet=feuillet, page=page.index + 1, x=l.cx, y=l.cy, type_element="poutre",
            element=key, armature=[b.armature],
            debug=Debug(raw=[b.text], confidence=(0.8 if b.size_from_mark else 0.9) if mark else 0.3,
                        locator_kind="attribute" if mark else "unknown",
                        roles=[b.label], section=sections.get(mark), title=title,
                        folder=dafile.folder),
        ))
    diag = {"feuillet": feuillet, "title": title, "beams": len(titles), "spans_found": len(spans),
            "labels": dict(labels), "records": len(records),
            "located": sum(r.element != "UNKNOWN" for r in records),
            "warnings": [] if titles else ["aucun titre de poutre (P### - section) trouvé"]}
    return records, diag
