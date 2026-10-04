"""Beams (S-300 elevations) - plan side.

Measured on all four projects: rows of beam elevations, each titled UNDER it by its mark
and section (``P108 -  16" x 40 3/8"``, ``P-100: 600x1130``), with bar callouts above:
    2-20M  /  3 - 35M  /  LIT 1: 5-35M         longitudinal bars
    10M @ 18'' c/c  /  15M@375 c/c             stirrups by zone (``ℓ/3`` markers)
    ARM. DE PEAU 15M@8" CH. FACE               skin bars
The element is the beam mark; one record contains all its reinforcement callouts.
The contiguous-group assignment used for wall views would be fragile here (beams differ in length), so a callout goes to
the beam whose DRAWN span contains it, with that assignment as the fallback.
"""

from __future__ import annotations

import re

from ..model import Armature, Debug, ElementRecord
from ..beam_records import align_beam_records
from ..page import PreparedPage
from ..units import BAR_DESIGNATORS, UnitSystem, parse_spacing
from .walls import assign_views

_TITLE = re.compile(r"^(P-?\d{2,4}[A-Z]?)\s*[-:]\s*(.+)$")
_COUNT = re.compile(r"^(?:LIT\s*(?P<lit>\d)\s*:\s*)?(?P<q>\d+)\s*-\s*(?P<size>\d{2})\s*M\b\s*(?P<rest>.*)$")
_SPACED = re.compile(r"^(?P<skin>ARM\.?\s*DE\s*PEAU\s*)?(?P<size>\d{2})\s*M\s*@\s*(?P<sp>.+)$")
_STIRRUP_N = re.compile(r"^(?P<q>\d+)\s*[ÉE]TRIERS?\s*(?P<size>\d{2})\s*M\b(?P<rest>.*)$")
_AXIS = re.compile(r"^(?:[A-Z]|\d{1,2}(?:\.\d{1,2})?)$")


def beam_positions(page: PreparedPage, titles, spans):
    """Keep elevation axes local to each view; these are not a floor-plan grid.

    Titles and circle labels use the same large lettering on CLP. A repeated
    number in a different elevation must never overwrite this view's position.
    The drawn span determines which axes the beam crosses, including interior
    axes (P100 crosses 17, 16 and 15).
    """
    if not titles:
        return {}
    title_size = max(l.size for l in page.lines if _TITLE.match(l.text.strip()))
    axes = [l for l in page.lines if not l.vertical and _AXIS.fullmatch(l.text.strip())
            and l.size >= title_size * .85]
    result = {}
    for name, cx, cy, _ in titles:
        if name not in spans:
            continue
        x0, x1 = spans[name]
        candidates = [l for l in axes if cy-500 < l.cy < cy-45
                      and x0-100 <= l.cx <= x1+100]
        if not candidates:
            continue
        # The closest header is the elevation's own strip, not an earlier view.
        header_y = max(l.cy for l in candidates)
        strip = sorted((l for l in candidates if abs(l.cy-header_y) < 65), key=lambda l:l.cx)
        positions = [dict(label=l.text.strip(), x=l.cx, y=l.cy) for l in strip
                     if x0-50 <= l.cx <= x1+50]
        if positions:
            result[name] = positions
    return result


def _titles(page: PreparedPage):
    """Beam titles: the LARGEST ``P### - section`` lines on the sheet (within 90 %).
    Relative, not absolute: the plan's titles are 18 pt, CLP's fabricator's 9 pt, and
    smaller matches are cross-references."""
    hits = [(l, m) for l in page.lines
            if not l.vertical and (m := _TITLE.match(l.text.strip()))]
    if not hits:
        return []
    top = max(l.size for l, _ in hits)
    return [(m.group(1).replace("P-", "P"), l.cx, l.cy, m.group(2).strip())
            for l, m in hits if l.size >= 0.9 * top]


def _spans(page: PreparedPage, titles):
    """Each beam's drawn x-extent: the longest horizontal stroke above its title that spans
    the title's centre and no neighbouring title's centre (the slab line spans several
    beams and is excluded). CLP S-300: P103 = 1453-1789, P104 = 1813-2175."""
    segs = []
    for d in page.drawings:
        for it in d["items"]:
            if it[0] == "l" and abs(it[1].y - it[2].y) < 0.5 and abs(it[1].x - it[2].x) >= 60:
                segs.append((it[1].y, min(it[1].x, it[2].x), max(it[1].x, it[2].x)))
    out = {}
    for name, cx, cy in titles:
        rivals = [t[1] for t in titles if t[0] != name and abs(t[2] - cy) < 60]
        best = None
        for y, x0, x1 in segs:
            if not (cy - 400 <= y <= cy - 10 and x0 <= cx <= x1):
                continue
            if any(x0 < r < x1 for r in rivals):
                continue
            if best is None or x1 - x0 > best[1] - best[0]:
                best = (x0, x1)
        if best:
            out[name] = best
    return out


def _owners(titles, spans, centres):
    """Beam span containing the callout (row = nearest title row below it); the contiguous
    -group assignment is the fallback for beams whose outline was not found."""
    fallback = assign_views(titles, centres, merge=0.0)
    out = []
    for (x, y), fb in zip(centres, fallback):
        below = [t for t in titles if t[2] > y - 5]
        if not below:
            out.append(fb)
            continue
        row_y = min(t[2] for t in below)
        row = [t for t in below if abs(t[2] - row_y) < 60 and t[0] in spans]
        inside = [t for t in row if spans[t[0]][0] - 25 <= x <= spans[t[0]][1] + 25]
        if inside:
            out.append(min(inside, key=lambda t: abs((spans[t[0]][0] + spans[t[0]][1]) / 2 - x))[0])
        elif row:
            # Skin labels and cantilever callouts can sit outside the outline.
            # Keep them in this elevation row instead of the page-wide fallback.
            out.append(min(row, key=lambda t: max(spans[t[0]][0]-x, x-spans[t[0]][1], 0)
                           + .01*abs(t[1]-x))[0])
        else:
            out.append(fb)
    return out


def _parse(text: str, system: UnitSystem):
    if (m := _COUNT.match(text)) and f"{m.group('size')}M" in BAR_DESIGNATORS:
        role = f"lit {m.group('lit')}" if m.group("lit") else "longitudinale"
        return Armature(quantite=int(m.group("q")), diametre=f"{m.group('size')}M"), role
    if (m := _STIRRUP_N.match(text)) and f"{m.group('size')}M" in BAR_DESIGNATORS:
        return Armature(quantite=int(m.group("q")), diametre=f"{m.group('size')}M"), "étriers"
    if (m := _SPACED.match(text)) and f"{m.group('size')}M" in BAR_DESIGNATORS:
        role = "peau" if m.group("skin") else "étriers"
        return Armature(diametre=f"{m.group('size')}M", espacement_mm=parse_spacing(text, system)), role
    return None, None


def extract(page: PreparedPage, system: UnitSystem) -> tuple[list[ElementRecord], dict]:
    titles = _titles(page)
    diag = {"beams": len(titles), "warnings": []}
    if not titles:
        diag["warnings"].append("no beam titles (P### - section) found")
    sections = {t[0]: t[3] for t in titles}
    anchors = {t[0]: (t[1], t[2]) for t in titles}

    callouts = []
    for l in page.lines:
        if l.x0 > 0.60 * page.width and l.y0 > 0.78 * page.height:
            continue
        text = re.sub(r"\s+", " ", l.text.strip())
        bar, role = _parse(text, system)
        if bar:
            callouts.append((l, text, bar, role))
    tri = [(t[0], t[1], t[2]) for t in titles]
    spans = _spans(page, tri)
    positions = beam_positions(page, titles, spans)
    diag["spans_found"] = len(spans)
    owners = _owners(tri, spans, [(l.cx, l.cy) for l, *_ in callouts])

    records: list[ElementRecord] = []
    seen: dict[str, int] = {}
    for (l, text, bar, role), mark in zip(callouts, owners):
        key = mark or "UNKNOWN"
        seen[key] = seen.get(key, 0) + 1
        records.append(ElementRecord(
            id=f"{page.sheet_id}_{key}_{seen[key]}_plan", source="plan",
            fichier=page.fichier, feuillet=page.sheet_id, page=page.index + 1,
            x=l.cx, y=l.cy, type_element="poutre", element=key, armature=[bar],
            debug=Debug(raw=[text], confidence=0.85 if mark else 0.3,
                        locator_kind="attribute" if mark else "unknown", niveau=page.niveau,
                        role=role, section=sections.get(mark),
                        beam_anchor=anchors.get(mark), symbol_bbox=(l.x0, l.y0, l.x1, l.y1),
                        axes=positions.get(mark, []),
                        position=" → ".join(a["label"] for a in positions.get(mark, []))),
        ))
    diag["reinforcement_entries"] = len(records)
    records = align_beam_records(records)
    diag["records"] = len(records)
    diag["beam_positions"] = positions
    diag["located"] = sum(r.element != "UNKNOWN" for r in records)
    bare = sorted(set(sections) - {r.element for r in records})
    if bare:
        diag["warnings"].append(f"{len(bare)} beams with no bar callout found: {', '.join(bare[:8])}")
    return records, diag
