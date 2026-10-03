"""Shop-drawing orchestration: the DA counterpart of ``l2c.pipeline.run_plan``.

Returns the SAME ``ProjectResult`` shape (one ``SheetReport`` per DA page, carrying its
file and reading tier), so the dashboard renders both sides with the same components.
Reads blind: the unit system comes from the DA's own text, never from the plan.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from typing import Callable

from ..model import ElementRecord, sort_key
from ..page import open_document, prepare
from ..pipeline import ProjectResult, SheetReport
from ..units import detect_unit_system
from .inventory import TIER_LABELS, DAFile, inventory

from . import beams, columns, planview

#: (type_element) -> parser(page, system, dafile) -> (records, diagnostics)
PARSERS: dict[str, Callable] = {
    "colonne": columns.extract,
    "dalle": planview.slabs,
    "semelle": planview.footings,
    "radier": planview.radier,
    "poutre": beams.extract,
}

#: reading tiers with a reader implemented
READABLE_TIERS: set[int] = {1}


def _reason(tier: int, kind: str | None) -> str:
    if tier not in READABLE_TIERS:
        return {2: "glyphes vectoriels : décodeur à venir (DA_PLAN phase 2)",
                3: "page image : OCR local à venir",
                4: "aucun contenu lisible"}.get(tier, TIER_LABELS.get(tier, "?"))
    if kind is None:
        return "type d'élément non reconnu (nom du dossier)"
    return f"lecteur « {kind} » à venir (DA_PLAN phase 1)"


def run_da(project_dir: str, progress=None, files: list[DAFile] | None = None) -> ProjectResult:
    t0 = time.time()
    planview._FILE_GRID.clear()
    project = os.path.basename(os.path.normpath(project_dir))
    files = inventory(project_dir) if files is None else files

    # units: from the DA's own text layer (blind to the plan); unknown without text
    sample: list[str] = []
    for f in files:
        if 1 in f.tiers:
            doc = open_document(f.path)
            for i, t in enumerate(f.tiers):
                if t == 1:
                    sample.extend(w[4] for w in doc[i].get_text("words"))
    system, evidence = detect_unit_system(sample) if sample else ("inconnu", {"tokens": 0})

    records: list[ElementRecord] = []
    sheets: list[SheetReport] = []
    labels: Counter = Counter()
    n = sum(f.pages for f in files)
    k = 0
    for f in files:
        doc = open_document(f.path) if any(t in READABLE_TIERS for t in f.tiers) else None
        for i, tier in enumerate(f.tiers):
            k += 1
            if progress:
                progress(k, n, f.name)
            parser = PARSERS.get(f.type_element) if tier in READABLE_TIERS else None
            if parser is None:
                sheets.append(SheetReport(f"{f.name} p{i + 1}", i + 1, f.type_element, None,
                                          0, 0, "unread", _reason(tier, f.type_element),
                                          {"folder": f.folder}, fichier=f.name, tier=tier))
                continue
            page = prepare(doc, i, f.name)
            recs, diag = parser(page, system, f)
            labels.update(diag.get("labels", {}))
            records.extend(recs)
            sheets.append(SheetReport(
                diag.get("feuillet", page.sheet_id), i + 1, f.type_element, diag.get("niveau"),
                len(recs), diag.get("located", 0),
                "extracted" if recs else "no_callouts",
                None if recs else "aucune annotation d'armature reconnue",
                {"folder": f.folder, **diag}, fichier=f.name, tier=tier))

    records.sort(key=lambda r: (r.fichier, *sort_key(r)))
    seen: dict[str, int] = {}
    for r in records:
        c = seen[r.id] = seen.get(r.id, 0) + 1
        if c > 1:
            r.id = f"{r.id.removesuffix('_atelier')}#{c}_atelier"
    tiers = Counter(s.tier for s in sheets)
    return ProjectResult(project, f"DA ({len(files)} fichiers)", system, evidence, records,
                         sheets, round(time.time() - t0, 2),
                         meta={"files": len(files), "pages": n,
                               "tiers": dict(sorted(tiers.items())),
                               "labels": dict(labels.most_common())})
