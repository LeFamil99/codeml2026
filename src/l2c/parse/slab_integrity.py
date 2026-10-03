"""Resolve circled slab integrity labels against the document's own detail table."""

from __future__ import annotations

import math
import re
import unicodedata

import pymupdf

from ..model import Armature, Debug, ElementRecord
from ..page import PreparedPage, prepare
from ..units import BAR_DESIGNATORS


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.upper())
                   if not unicodedata.combining(c))


def read_catalog(pages):
    """Find identification / reinforcement tables beside an integrity detail title.

    Values belong to this document, never a hardcoded A/B dictionary. Conflicting
    definitions are withheld rather than silently selected.
    """
    catalog, conflicts = {}, set()
    for page in pages:
        headings = [l for l in page.lines if re.search(r"ARMATURE D.?INTEGRITE", fold(l.text))
                    and "DALLE STRUCTURALE" in fold(l.text)]
        for title in headings:
            headers = [l for l in page.lines if fold(l.text) == "IDENTIFICATION"
                       and title.x0 - 100 < l.cx < title.x1 + 100
                       and 0 < title.y0 - l.y0 < 650]
            for header in headers:
                bar_header = next((l for l in page.lines if fold(l.text) == "ARMATURE"
                                   and abs(l.cy - header.cy) < 5
                                   and 0 < l.cx - header.cx < 150), None)
                if bar_header is None:
                    continue
                detail = next((l.text for l in page.lines if re.fullmatch(r"\d{2,3}", l.text)
                               and abs(l.cy - title.cy) < 20
                               and 0 < title.x0 - l.x1 < 80), None)
                for label in page.lines:
                    if not re.fullmatch(r"[A-Z]", label.text) or abs(label.cx - header.cx) > 25 \
                            or not header.y1 < label.cy < min(header.y1 + 250, title.y0):
                        continue
                    row = next((l for l in page.lines if abs(l.cy - label.cy) < 4
                                and bar_header.x0 - 20 < l.x0 < bar_header.x1 + 30
                                and re.fullmatch(r"\d+\s*-\s*\d{2}M\s+CH\.?\s*DIR\.?", fold(l.text))), None)
                    if row is None:
                        continue
                    match = re.match(r"(\d+)\s*-\s*(\d{2}M)", row.text)
                    quantity, diameter = int(match[1]), match[2]
                    if diameter not in BAR_DESIGNATORS:
                        continue
                    entry = dict(quantite=quantity, diametre=diameter, raw=row.text,
                                 fichier=page.fichier, feuillet=page.sheet_id,
                                 page=page.index + 1, detail=detail, bbox=(row.x0, row.y0, row.x1, row.y1))
                    previous = catalog.get(label.text)
                    if previous and (previous["quantite"], previous["diametre"]) != (quantity, diameter):
                        conflicts.add(label.text)
                    else:
                        catalog[label.text] = entry
    for label in conflicts:
        catalog.pop(label, None)
    return catalog, sorted(conflicts)


def catalog_for(page: PreparedPage):
    # Document-local primitives only; no global cache that can leak another
    # project's definitions or survive the dashboard's cache regeneration.
    doc = page._page.parent
    if not hasattr(doc, "_l2c_slab_integrity_catalog"):
        pages = [prepare(doc, i, page.fichier) for i in range(len(doc))]
        doc._l2c_slab_integrity_catalog = read_catalog(pages)
    return doc._l2c_slab_integrity_catalog


def is_circle(path):
    rect = path["rect"]
    if path.get("fill") is not None or not 8 < rect.width < 32 \
            or not 0.85 < rect.width / max(rect.height, 0.01) < 1.18:
        return False
    points = []
    curves = 0
    for item in path["items"]:
        if item[0] == "l":
            points.extend(item[1:3])
        elif item[0] == "c":
            curves += 1
            points.extend((item[1], item[-1]))
    if len(points) < 24 and curves < 3:
        return False
    radius = (rect.width + rect.height) / 4
    return all(0.8 * radius < math.hypot(p.x - (rect.x0 + rect.x1) / 2,
                                       p.y - (rect.y0 + rect.y1) / 2) < 1.2 * radius for p in points)


def extract(page: PreparedPage, grid):
    if not grid.ok:
        return [], {"records": 0, "unresolved": [], "warnings": ["integrity grid unread"]}
    catalog, conflicts = catalog_for(page)
    letters, numbers = list(grid.letters.values()), list(grid.numbers.values())
    x_values, y_values = (letters, numbers) if grid.letter_role == "columns" else (numbers, letters)
    bounds = pymupdf.Rect(min(x_values) - 15, min(y_values) - 15,
                         max(x_values) + 15, max(y_values) + 15)
    symbols = [d["rect"] for d in page.drawings if d.get("fill") is not None
               and max(d["fill"]) < 0.9 and 4 < min(d["rect"].width, d["rect"].height)
               and max(d["rect"].width, d["rect"].height) < 160]
    records, unresolved, seen = [], [], set()
    for path in page.drawings:
        if not is_circle(path):
            continue
        circle = path["rect"]
        cx, cy = (circle.x0 + circle.x1) / 2, (circle.y0 + circle.y1) / 2
        if not bounds.contains(pymupdf.Point(cx, cy)):
            continue
        labels = [w for w in page.words if re.fullmatch(r"[A-Z]", w.text)
                  and circle.contains(pymupdf.Point(w.cx, w.cy))]
        if len(labels) != 1:
            continue
        label = labels[0]
        identity = (label.text, round(label.cx, 1), round(label.cy, 1))
        if identity in seen:
            continue
        seen.add(identity)
        nearby = sorted((math.hypot(max(r.x0 - cx, 0, cx - r.x1),
                                    max(r.y0 - cy, 0, cy - r.y1)), i) for i, r in enumerate(symbols))
        symbol = symbols[nearby[0][1]] if nearby and nearby[0][0] < 45 else None
        # Use the point on the support nearest the label. A large wall's centre
        # can lie between rows, while the actual label marks one end.
        x = min(max(cx, symbol.x0), symbol.x1) if symbol else cx
        y = min(max(cy, symbol.y0), symbol.y1) if symbol else cy
        element, cost = grid.locate(x, y)
        definition = catalog.get(label.text)
        if definition is None:
            unresolved.append(dict(label=label.text, coordinate=element, bbox=tuple(circle),
                                   reason="conflicting integrity type" if label.text in conflicts else "integrity type absent from detail table"))
            continue
        q, size = definition["quantite"], definition["diametre"]
        records.append(ElementRecord(
            id=f"{page.sheet_id}_{element or 'UNKNOWN'}_integrity_{label.text}_{len(records)+1}_plan",
            source="plan", fichier=page.fichier, feuillet=page.sheet_id, page=page.index+1,
            x=label.cx, y=label.cy, type_element="dalle", element=element or "UNKNOWN",
            armature=[Armature(quantite=q, diametre=size), Armature(quantite=q, diametre=size)],
            debug=Debug(raw=[label.text, definition["raw"]], niveau=page.niveau,
                        confidence=round(max(0, 1 - 0.6*cost - (0 if symbol else 0.15)), 3),
                        locator_kind="grid" if element else "unknown", reinforcement_kind="integrity",
                        layer="intégrité", integrity_type=label.text, roles=["NUM", "ALP"],
                        detail_reference=definition, circle_bbox=tuple(circle),
                        symbol_bbox=tuple(symbol) if symbol else None,
                        support_association="filled_symbol" if symbol else "nearest_grid")))
    return records, {"records": len(records), "types": catalog, "unresolved": unresolved,
                     "warnings": [f"conflicting integrity definitions: {conflicts}"] if conflicts else []}
