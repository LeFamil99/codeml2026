"""Project-level orchestration - the whole plan set, plan side.

Every sheet is classified from its title (page.py) and sent to its type's parser:
    S-050/060 radier   S-100 semelle   S-300 poutre   S-400 mur_refend
    S-500 colonne      S-600 dalle
Sheets that carry no element reinforcement (typical details, general-arrangement
plans) are reported as `skipped` WITH the reason, never silently dropped.
A project without a radier sheet (LIGREP) draws its mats on the foundations plan; that
sheet is then read twice, and reported twice: once as semelle, once as radier.
Shop-drawing reading, matching and comparison are later stages.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from .model import ElementRecord, sort_key
from .page import UNKNOWN_SHEET, open_document, prepare
from .parse import beams, columns, footings, radier, slabs, walls
from .units import UnitSystem, detect_unit_system

PARSERS = {
    "colonne": columns.extract,
    "semelle": footings.extract,
    "mur_refend": walls.extract,
    "dalle": slabs.extract,
    "radier": radier.extract,
    "poutre": beams.extract,
}
SUPPORTED_TYPES = set(PARSERS)


def skip_reason(page) -> str:
    t = page.title.upper()
    if "TYPIQUE" in t:
        return "détails typiques : aucun élément localisable sur la grille"
    if "PLAN DU" in t or "PLAN DE LA" in t or "PLAN DES" in t:
        return ("plan général d'aménagement : l'armature des dalles est sur la série S-600, "
                "celle des colonnes sur la série S-500")
    return "aucun type d'élément reconnu dans le titre du feuillet"


@dataclass
class SheetReport:
    feuillet: str
    page: int
    type_element: str | None
    niveau: str | None
    records: int
    located: int
    status: str                      # "extracted" | "skipped" | "no_callouts" | "unread"
    reason: str | None = None
    diagnostics: dict = field(default_factory=dict)
    fichier: str | None = None       # DA side: the file this page belongs to
    tier: int | None = None          # DA side: reading tier (1 text, 2 glyphs, 3 raster, 4 none)


@dataclass
class ProjectResult:
    project: str
    plan_file: str
    unit_system: UnitSystem
    unit_evidence: dict
    records: list[ElementRecord]
    sheets: list[SheetReport]
    elapsed_s: float
    meta: dict = field(default_factory=dict)

    @property
    def totals(self) -> dict:
        return {
            # a foundations sheet read as semelle AND radier is still one sheet
            "sheets": len({(s.fichier, s.page) for s in self.sheets}),
            "sheets_extracted": len({(s.fichier, s.page) for s in self.sheets
                                     if s.status == "extracted"}),
            "sheets_skipped": sum(1 for s in self.sheets if s.status == "skipped"),
            "elements": len(self.records),
            "located": sum(1 for r in self.records if r.element != "UNKNOWN"),
            "mean_confidence": round(
                sum(r.debug.confidence for r in self.records) / len(self.records), 3
            ) if self.records else 0.0,
        }


def find_plan(project_dir: str) -> str | None:
    for name in sorted(os.listdir(project_dir)):
        if name.startswith("L2C_PLAN_STR") and name.lower().endswith(".pdf"):
            return os.path.join(project_dir, name)
    return None


def run_plan(plan_path: str, progress=None) -> ProjectResult:
    t0 = time.time()
    fichier = os.path.basename(plan_path)
    project = fichier.replace("L2C_PLAN_STR_", "").replace(".pdf", "")
    doc = open_document(plan_path)
    n = len(doc)

    # unit system is a PROJECT-level property; detect once from the whole set
    # (PLAN SS5.6: CLP imperial, WP2/LIGREP/EspCa3B metric)
    sample: list[str] = []
    pages = []
    for i in range(n):
        p = prepare(doc, i, fichier)
        pages.append(p)
        sample.extend(w.text for w in p.words)
    system, evidence = detect_unit_system(sample)

    records: list[ElementRecord] = []
    sheets: list[SheetReport] = []
    for i, p in enumerate(pages):
        if progress:
            progress(i + 1, n, p.sheet_id)
        kind = p.type_element
        if kind not in PARSERS:
            sheets.append(SheetReport(p.sheet_id, i + 1, kind, p.niveau, 0, 0,
                                      "skipped", skip_reason(p)))
            continue
        recs, diag = PARSERS[kind](p, system)
        records.extend(recs)
        sheets.append(SheetReport(
            p.sheet_id, i + 1, kind, p.niveau, len(recs), diag.get("located", 0),
            "extracted" if recs else "no_callouts",
            None if recs else f"aucune annotation d'armature reconnue ({kind})", diag,
        ))
        if kind == "semelle" and radier.marks(p):
            recs, diag = radier.extract(p, system, on_foundations=True)
            records.extend(recs)
            sheets.append(SheetReport(
                p.sheet_id, i + 1, "radier", p.niveau, len(recs), diag.get("located", 0),
                "extracted" if recs else "no_callouts",
                None if recs else "aucune annotation d'armature reconnue (radier)", diag,
            ))

    records.sort(key=sort_key)
    # ids must be unique project-wide; two callouts on one grid cell (a column's two
    # slab callouts, a re-used footing mark) share a natural id, so suffix the repeats
    seen: dict[str, int] = {}
    for r in records:
        k = seen[r.id] = seen.get(r.id, 0) + 1
        if k > 1:
            r.id = f"{r.id.removesuffix('_plan')}#{k}_plan"
    return ProjectResult(project, fichier, system, evidence, records, sheets,
                         round(time.time() - t0, 2))
