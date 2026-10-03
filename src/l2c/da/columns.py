"""DA column schedules (``Colonnes`` folders) - shop-drawing side.

CLP layout, measured: one vertical strip per column, its grid label (``K-6``) printed
at the bottom AND the top of the strip; storey segments stacked upward, each with
    VERT:    4 25M 25Z13-05
    ÉTRI:   28 10M 10ET13X21 @6"
just above a storey line; storey labels (``NIVEAU 2``, ``REZ-DE-CHAUSSÉE``) down the left
edge. Strips repeat every ~112 pt; a bar line sits 25-30 pt right of the bottom label and
~5 pt from the top one, so each line goes to the strip whose label column is nearest.

One record per (column, storey segment): element = grid label, niveau = ``RDC @ 2``.

LIGREP's fabricator uses PANELS instead (measured on GP2_COLONNE-NIV-2@3): one file per
storey span (the name says it: ``NIV-2@3``), one panel per column titled
``COL : DD - 12``, labels and values on the same row as separate lines
(``ET:`` ... ``26 10M 10T52X32``, ``VERT:`` ... ``4 20M 3400``), tie spacing as
``25 @ 100``. A panel spans ~120 pt left to ~230 pt right of its title. The layout is
chosen per page by what it shows: strips of ``K-6`` labels, or ``COL :`` panels.
"""

from __future__ import annotations

import os
import re
import statistics
from collections import Counter, defaultdict

from ..model import Debug, ElementRecord
from ..page import PreparedPage
from ..parse.walls import _band_for, _levels, short_level
from ..units import UnitSystem, parse_spacing
from .common import LOCATOR, parse_bar_line, section, sheet_identity

_PANEL = re.compile(r"^COL\s*:?\s*([A-Z]{1,2}(?:\.\d{1,2})?'?)\s*-\s*(\d{1,2}(?:\.\d{1,2})?)$")
_ROLE = re.compile(r"^([A-ZÉÈ][A-ZÉÈ.\-]*)\s*:?$")
_COUNT_AT = re.compile(r"^(\d+)\s*@\s*(\d+(?:\s*mm)?)$")
_FILE_SPAN = re.compile(r"NIV[-_ ]?([A-Z0-9]+)\s*@\s*([A-Z0-9]+)", re.I)


def _span_from_name(name: str) -> str | None:
    """``GP2_COLONNE-NIV-2@3.pdf`` -> ``2 @ 3``; ``FDN@RDC`` -> ``FONDATION @ RDC``."""
    m = _FILE_SPAN.search(os.path.splitext(name)[0])
    if not m:
        return None
    f = lambda t: {"FDN": "FONDATION", "SS": "SS"}.get(t.upper(), t.upper())
    return f"{f(m.group(1))} @ {f(m.group(2))}"


def _panels(page: PreparedPage, system: UnitSystem, dafile, feuillet, title):
    titles = [(l, f"{m.group(1)}-{m.group(2)}") for l in page.lines
              if (m := _PANEL.match(re.sub(r"\s+", " ", l.text.strip())))]
    span = _span_from_name(dafile.name)
    labels: Counter = Counter()
    lines = [l for l in page.lines if not l.vertical]

    def owner(l):
        cands = [(abs(l.cy - t.cy), t, lab) for t, lab in titles
                 if t.cx - 120 <= l.cx < t.cx + 230 and t.cy - 40 <= l.cy < t.cy + 640]
        return min(cands, key=lambda c: c[0])[1:] if cands else (None, None)

    groups: dict[str, list] = defaultdict(list)
    spacings: dict[str, list] = defaultdict(list)
    sections: dict[str, str] = {}
    for l in lines:
        t = re.sub(r"\s+", " ", l.text.strip())
        tl, lab = owner(l)
        if lab is None:
            continue
        if (m := _COUNT_AT.match(t)):
            spacings[lab].append((l, m))
            continue
        if sec := section(t.replace(" X ", "X")):
            sections.setdefault(lab, sec)
            continue
        bar = parse_bar_line(t, system)
        if bar is None:
            continue
        # the role label sits on the same row, to the left, as its own line
        role = bar.label
        if role is None:
            left = [o for o in lines if abs(o.cy - l.cy) < 6 and o.cx < l.cx
                    and _ROLE.match(o.text.strip()) and owner(o)[1] == lab]
            if left:
                role = max(left, key=lambda o: o.cx).text.strip().rstrip(":").rstrip(".").upper()
        labels[role or "(sans étiquette)"] += 1
        groups[lab].append((l, bar, role))

    records = []
    for lab, items in sorted(groups.items()):
        for l, bar, role in items:          # tie spacing: the "25 @ 100" in this panel
            if role in ("ET", "ÉTRI", "ETRI", "LIG") and bar.armature.espacement_mm is None:
                sp = spacings.get(lab)
                if sp:
                    near = min(sp, key=lambda t: abs(t[0].cy - l.cy))
                    bar.armature.espacement_mm = parse_spacing("@" + near[1].group(2), system)
        ls = [l for l, _, _ in items]
        x0, y0 = min(l.x0 for l in ls), min(l.y0 for l in ls)
        x1, y1 = max(l.x1 for l in ls), max(l.y1 for l in ls)
        inferred = any(b.size_from_mark for _, b, _ in items)
        records.append(ElementRecord(
            id=f"{feuillet}_{lab}_{span or 'p'}_atelier", source="atelier", fichier=dafile.name,
            feuillet=feuillet, page=page.index + 1, x=(x0 + x1) / 2, y=(y0 + y1) / 2,
            type_element="colonne", element=lab, armature=[b.armature for _, b, _ in items],
            debug=Debug(raw=[b.text for _, b, _ in items], confidence=0.85 if inferred else 0.95,
                        locator_kind="grid", niveau=span, roles=[r for _, _, r in items],
                        section=sections.get(lab), title=title, folder=dafile.folder,
                        layout="panels"),
        ))
    return records, labels, len(titles)


def _strips(page: PreparedPage):
    """label -> (x, y_min, y_max) from every instance of the label on the page."""
    inst: dict[str, list] = defaultdict(list)
    for l in page.lines:
        if not l.vertical and LOCATOR.match(l.text.strip()):
            inst[l.text.strip()].append(l)
    out = {}
    for label, ls in inst.items():
        out[label] = (statistics.fmean(l.cx for l in ls), min(l.cy for l in ls),
                      max(l.cy for l in ls))
    return out


def extract(page: PreparedPage, system: UnitSystem, dafile) -> tuple[list[ElementRecord], dict]:
    feuillet, title = sheet_identity(page, dafile.name)
    # panels first: LIGREP's panel ids ("C-01") look like grid labels, COL : titles do not lie
    if any(_PANEL.match(re.sub(r"\s+", " ", l.text.strip())) for l in page.lines):
        recs, labs, n = _panels(page, system, dafile, feuillet, title)
        return recs, {"feuillet": feuillet, "title": title, "layout": "panels", "panels": n,
                      "records": len(recs), "located": len(recs), "labels": dict(labs),
                      "niveau": _span_from_name(dafile.name), "warnings": []}
    strips = _strips(page)
    levels = _levels(page)
    labels: Counter = Counter()
    diag = {"feuillet": feuillet, "title": title, "strips": len(strips),
            "level_labels": len(levels), "warnings": []}
    if not strips:
        recs, labs, n = _panels(page, system, dafile, feuillet, title)
        if n:
            diag.update(layout="panels", panels=n, records=len(recs), located=len(recs),
                        labels=dict(labs), niveau=_span_from_name(dafile.name))
            return recs, diag
        diag["warnings"].append("aucune étiquette de grille (K-6) ni panneau COL : — "
                                "tableau sans repère de grille")
        return [], diag
    xs = sorted(s[0] for s in strips.values())
    pitch = statistics.median([b - a for a, b in zip(xs, xs[1:])]) if len(xs) > 1 else 120.0

    groups: dict[tuple, list] = defaultdict(list)
    sections: dict[str, str] = {}
    for l in page.lines:
        if l.vertical:
            continue
        sec = section(l.text)
        bar = None if sec else parse_bar_line(l.text, system)
        if not (sec or bar):
            continue
        near = [(abs(l.cx - x), lab) for lab, (x, y0, y1) in strips.items()
                if y0 - 60 <= l.cy <= y1 + 60]
        if not near:
            continue
        d, lab = min(near)
        if d > 0.6 * pitch:
            continue
        if sec:
            sections.setdefault(lab, sec)
            continue
        labels[bar.label or "(sans étiquette)"] += 1
        bottom, top = _band_for(levels, l.x0, l.cy)
        band = (f"{short_level(bottom) if bottom else 'FONDATION'} @ "
                f"{short_level(top) if top else 'TOIT'}")
        groups[(lab, band)].append((l, bar))

    records: list[ElementRecord] = []
    for (lab, band), items in sorted(groups.items(), key=lambda kv: (kv[0][0], -kv[1][0][0].cy)):
        ls = [l for l, _ in items]
        x0, y0 = min(l.x0 for l in ls), min(l.y0 for l in ls)
        x1, y1 = max(l.x1 for l in ls), max(l.y1 for l in ls)
        inferred = any(b.size_from_mark for _, b in items)
        records.append(ElementRecord(
            id=f"{feuillet}_{lab}_{band}_atelier", source="atelier", fichier=dafile.name,
            feuillet=feuillet, page=page.index + 1, x=(x0 + x1) / 2, y=(y0 + y1) / 2,
            type_element="colonne", element=lab, armature=[b.armature for _, b in items],
            debug=Debug(raw=[b.text for _, b in items], confidence=0.85 if inferred else 0.95,
                        locator_kind="grid", niveau=band,
                        roles=[b.label for _, b in items], section=sections.get(lab),
                        title=title, folder=dafile.folder),
        ))
    diag["records"] = len(records)
    diag["located"] = len(records)
    diag["labels"] = dict(labels)
    diag["niveau"] = None
    return records, diag
