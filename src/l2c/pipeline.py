"""Project-level orchestration.

SCOPE OF THIS SLICE: plan-side column (S-500) extraction only. Shop-drawing reading,
matching and comparison are deliberately absent (PLAN SS12 Phase 1 thin slice), so that
the JSON contract and the UI can be validated before the harder stages land.
Sheets we cannot yet handle are reported explicitly as `skipped`, never silently.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

from .model import ElementRecord, sort_key
from .page import UNKNOWN_SHEET, open_document, prepare
from .parse import columns
from .units import UnitSystem, detect_unit_system

SUPPORTED_TYPES = {"colonne"}


@dataclass
class SheetReport:
    feuillet: str
    page: int
    type_element: str | None
    niveau: str | None
    records: int
    located: int
    status: str                      # "extracted" | "skipped" | "no_callouts"
    reason: str | None = None
    diagnostics: dict = field(default_factory=dict)


@dataclass
class ProjectResult:
    project: str
    plan_file: str
    unit_system: UnitSystem
    unit_evidence: dict
    records: list[ElementRecord]
    sheets: list[SheetReport]
    elapsed_s: float

    @property
    def totals(self) -> dict:
        return {
            "sheets": len(self.sheets),
            "sheets_extracted": sum(1 for s in self.sheets if s.status == "extracted"),
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
        if kind not in SUPPORTED_TYPES:
            sheets.append(SheetReport(p.sheet_id, i + 1, kind, p.niveau, 0, 0,
                                      "skipped",
                                      f"element type {kind!r} not implemented in this slice"))
            continue
        recs, diag = columns.extract(p, system)
        records.extend(recs)
        sheets.append(SheetReport(
            p.sheet_id, i + 1, kind, p.niveau, len(recs), diag.get("located", 0),
            "extracted" if recs else "no_callouts",
            None if recs else "no COL. callouts on this sheet", diag,
        ))

    records.sort(key=sort_key)
    return ProjectResult(project, fichier, system, evidence, records, sheets,
                         round(time.time() - t0, 2))
