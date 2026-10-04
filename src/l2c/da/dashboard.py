"""Dashboard DA runner: full column/radier files; last page for other DA types.

The older generic readers in pipeline.py remain a historical CLI baseline. This
runner never inventories or reads their files, PDF text layers, or original plans.
The explicit inputs mapping is also the integration point for future UI uploads.
"""

from __future__ import annotations

import time
import hashlib
import json
import pickle
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Mapping, Sequence

import pymupdf

from ..model import ElementRecord, sort_key
from ..beam_records import align_beam_records, BEAM_FORMAT
from ..column_records import align_column_records, COLUMN_FORMAT
from ..record_formats import align_records, FORMAT_VERSION
from ..pipeline import ProjectResult, SheetReport
from .parsers import colonne_clp, dalle_clp, poutre_clp, radier_clp, semelle_clp
from .jobs import write_pickle

PARSER_VERSION = "clp-image-parsers-v2-all-slabs"
COLUMN_SCOPE = "all-pages-v2-floor-boundaries"
# Partie 1's first four schedules are superseded by Partie 3, but its fifth
# sheet contains 23 additional basement columns absent from the newer PDF.
COLUMN_SUPPLEMENT = "CLP_COLONNES Partie 1.pdf"
CLP_FILES = {
    "colonne": "DA/Colonnes/CLP_COLONNES Partie 3.pdf",
    "dalle": "DA/Dalles/CLP_DALLE NIV 3.pdf",
    "semelle": "DA/Fondations/CLP_SEMELLES FND.pdf",
    "poutre": "DA/Poutres/CLP_POUTRES.pdf",
    "radier": "DA/Fondations/CLP_RADIERS.pdf",
}


InputSource = str | Path | Sequence[str | Path]


def configured_inputs(project_dir: str) -> dict[str, tuple[Path, ...]]:
    root = Path(project_dir).expanduser()
    if root.name.upper() != "CLP":
        raise ValueError("Les cinq lecteurs DA actuels sont disponibles pour CLP uniquement.")
    inputs = {kind: (root / relative,) for kind, relative in CLP_FILES.items()}
    supplement = inputs["colonne"][0].with_name(COLUMN_SUPPLEMENT)
    if supplement.is_file():
        inputs["colonne"] += (supplement,)
    slab = root / CLP_FILES["dalle"]
    # Re-evaluate folder membership on every rerun so new/removed PDFs change
    # the cache key. Keep an unread expected input if no slab PDFs are available.
    slabs = tuple(sorted((p for p in slab.parent.iterdir()
                          if p.is_file() and p.suffix.lower() == ".pdf"),
                         key=lambda p: p.name.casefold())) if slab.parent.is_dir() else ()
    inputs["dalle"] = slabs or (slab,)
    return inputs


def input_files(inputs: Mapping[str, InputSource]) -> list[tuple[str, Path]]:
    """One or several PDFs per type; also accepts the earlier single-path API."""
    files = []
    for kind, sources in inputs.items():
        paths = (sources,) if isinstance(sources, (str, Path)) else sources
        for path in dict.fromkeys(Path(p).expanduser() for p in paths):
            files.append((kind, path))
    return files


def input_stamp(project_dir: str) -> tuple:
    """Only selected source PDFs invalidate the DA cache, including deletions."""
    stamp = []
    for kind, path in input_files(configured_inputs(project_dir)):
        stat = path.stat() if path.is_file() else None
        stamp.append((kind, str(path), stat.st_mtime_ns if stat else None,
                      stat.st_size if stat else None, COLUMN_SCOPE) if kind == "colonne" else
                     (kind, str(path), stat.st_mtime_ns if stat else None,
                      stat.st_size if stat else None))
    return tuple(stamp)


def _columns(page, filename):
    # Disable the standalone CLI's experimental two-strip limit.
    cells = colonne_clp.parse_page(page, max_strips=0, check=False)
    _, deduplication = colonne_clp.clean_output(cells, filename)
    return colonne_clp.records(cells, filename), {
        "cells": [asdict(c) for c in cells], "deduplication": deduplication,
        "warnings": [c.reason for c in cells if c.reason],
    }


def _slabs(page, filename):
    result = dalle_clp.parse_page(page, filename, check=False)
    records = dalle_clp.records(result, filename)
    return records, asdict(result)


def _footings(page, filename):
    result = semelle_clp.parse_page(page, filename, check=False)
    return semelle_clp.records(result), result


def _beams(page, filename):
    rows, diagnostics = poutre_clp.parse_page(page, filename)
    return poutre_clp.records(rows), diagnostics


def _radiers(page, filename):
    result = radier_clp.parse_page(page, filename, check=False)
    return radier_clp.records(result, filename), asdict(result)


PARSERS: dict[str, Callable] = {
    "colonne": _columns, "dalle": _slabs, "semelle": _footings, "poutre": _beams,
    "radier": _radiers,
}


def file_checkpoint(kind, path, directory):
    """A checkpoint belongs to one parser revision and one unchanged PDF."""
    path = Path(path)
    stat = path.stat()
    fingerprint = [PARSER_VERSION, kind, str(path.resolve()), stat.st_mtime_ns, stat.st_size]
    if kind == "colonne":
        fingerprint.append(COLUMN_SCOPE)
    key = hashlib.sha256(json.dumps(fingerprint).encode()).hexdigest()
    return fingerprint, Path(directory) / f"{key}.pkl"


def last_page_only(kind, path):
    return kind not in {"colonne", "radier"} or (kind == "colonne" and Path(path).name == COLUMN_SUPPLEMENT)


def load_checkpoint(path, fingerprint):
    try:
        with path.open("rb") as source:
            saved = pickle.load(source)
        reports = saved.get("sheets", [saved.get("sheet")])
        if (saved["fingerprint"] == fingerprint and isinstance(reports, list) and reports and
                all(isinstance(report, SheetReport) for report in reports) and
                isinstance(saved["records"], list) and
                all(isinstance(r, ElementRecord) for r in saved["records"])):
            if any(r.type_element == "poutre" and
                   getattr(r.debug, "record_format", None) != BEAM_FORMAT for r in saved["records"]):
                saved["records"] = align_beam_records(saved["records"])
                sheet = saved["sheet"]
                sheet.records = len(saved["records"])
                sheet.located = sum(r.element != "UNKNOWN" for r in saved["records"])
                sheet.diagnostics.update(records=sheet.records, located=sheet.located,
                    reinforcement_entries=sum(len(r.armature) for r in saved["records"]))
                write_pickle(path, saved)
            if any(r.type_element == "colonne" and
                   getattr(r.debug,"record_format",None) != COLUMN_FORMAT for r in saved["records"]):
                saved["records"] = align_column_records(saved["records"])
                write_pickle(path, saved)
            if any(getattr(r.debug,"spec_format",None) != FORMAT_VERSION for r in saved["records"]):
                saved["records"] = align_records(saved["records"])
                write_pickle(path,saved)
            return saved
    except (OSError, EOFError, pickle.UnpicklingError, KeyError, AttributeError, TypeError, ValueError):
        pass
    return None


def checkpoint_inventory(inputs, directory):
    """Read-only inventory; never opens a PDF or launches a parser."""
    available = []
    for kind, path in input_files(inputs):
        if path.is_file():
            fingerprint, checkpoint = file_checkpoint(kind, path, directory)
            if load_checkpoint(checkpoint, fingerprint) is not None:
                available.append(str(path.resolve()))
    return available


def run_da(project_dir: str, progress=None,
           inputs: Mapping[str, InputSource] | None = None,
           checkpoint_dir: str | Path | None = None, reuse_checkpoints=True,
           file_done=None, page_progress=None) -> ProjectResult:
    """Run configured PDFs; explicit inputs can later come from UI uploads.

    Missing inputs are reported as unread, never replaced by generic extraction or
    old saved JSON. Parser errors propagate so a failed run cannot look complete.
    """
    started = time.monotonic()
    inputs = configured_inputs(project_dir) if inputs is None else inputs
    unsupported = inputs.keys() - PARSERS.keys()
    if unsupported:
        raise ValueError(f"No dedicated DA parser for: {', '.join(sorted(unsupported))}")
    files = input_files(inputs)
    checkpoints = Path(checkpoint_dir) if checkpoint_dir is not None else None
    if checkpoints:
        checkpoints.mkdir(parents=True, exist_ok=True, mode=0o700)
    records, sheets, sources = [], [], []
    checkpoint_hits = 0
    page_checkpoint_hits = 0
    for index, (kind, path) in enumerate(files, 1):
        if progress:
            progress(index, len(files), path.name)
        sources.append({"type": kind, "path": str(path)})
        if not path.is_file():
            sheets.append(SheetReport(path.stem, 0, kind, None, 0, 0, "unread",
                "Fichier configuré introuvable", {"folder": path.parent.name},
                fichier=path.name, tier=3))
            continue
        stat = path.stat()
        fingerprint, checkpoint = file_checkpoint(kind, path, checkpoints) if checkpoints else (None, None)
        if checkpoint and reuse_checkpoints and checkpoint.is_file():
            saved = load_checkpoint(checkpoint, fingerprint)
            if saved is not None:
                records.extend(saved["records"])
                reports = saved.get("sheets", [saved.get("sheet")])
                sheets.extend(reports)
                sources[-1].update(page=reports[-1].page, pages=[s.page for s in reports],
                                   reused_checkpoint=True, last_page_only=last_page_only(kind, path))
                checkpoint_hits += 1
                if page_progress:
                    for report in reports:
                        page_progress(path.name, report.page,
                                      report.diagnostics.get("document_pages", report.page), True, True)
                if file_done:
                    file_done(index, len(files), str(path.resolve()), True)
                continue
        recs, reports = [], []
        with pymupdf.open(path) as document:
            selected_pages = (range(len(document)) if not last_page_only(kind, path)
                              else [len(document)-1])
            for page_index in selected_pages:
                number = page_index + 1
                if page_progress:
                    page_progress(path.name, number, len(document), False, False)
                page_fingerprint = [*fingerprint, "page", number] if fingerprint else None
                page_cache = None
                if checkpoints and kind in {"colonne", "radier"}:
                    page_directory = checkpoints / "pages"
                    page_directory.mkdir(mode=0o700, exist_ok=True)
                    key = hashlib.sha256(json.dumps(page_fingerprint).encode()).hexdigest()
                    page_cache = page_directory / f"{key}.pkl"
                cached = load_checkpoint(page_cache, page_fingerprint) if page_cache and reuse_checkpoints else None
                if (cached is None and page_cache and reuse_checkpoints and kind == "colonne"
                        and COLUMN_SCOPE == "all-pages-v2-floor-boundaries"):
                    # Upgrade completed image-cell checkpoints instead of repeating
                    # the expensive full-page OCR after the floor-layout fix.
                    old_fingerprint = [*fingerprint[:-1], "all-pages-v1", "page", number]
                    old_key = hashlib.sha256(json.dumps(old_fingerprint).encode()).hexdigest()
                    old = load_checkpoint(page_directory / f"{old_key}.pkl", old_fingerprint)
                    if old is not None and old["sheet"].diagnostics.get("cells"):
                        page = document[page_index]
                        if page.rotation:
                            page.remove_rotation()
                        cells = colonne_clp.repair_cached_page(page,
                            [colonne_clp.Cell(**cell) for cell in old["sheet"].diagnostics["cells"]])
                        report = old["sheet"]
                        upgraded = colonne_clp.records(cells, path.name)
                        _, deduplication = colonne_clp.clean_output(cells, path.name)
                        report.records = len(upgraded)
                        report.located = sum(r.element != "UNKNOWN" for r in upgraded)
                        report.diagnostics.update(cells=[asdict(c) for c in cells],
                            deduplication=deduplication, warnings=[c.reason for c in cells if c.reason],
                            upgraded_from="all-pages-v1", column_layout=COLUMN_SCOPE)
                        after = path.stat()
                        if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                            raise RuntimeError(f"Le fichier a changé pendant la lecture : {path.name}")
                        cached = {"fingerprint": page_fingerprint, "records": upgraded, "sheet": report}
                        write_pickle(page_cache, cached)
                if cached is not None:
                    page_records, report = cached["records"], cached["sheet"]
                    page_checkpoint_hits += 1
                else:
                    page = document[page_index]
                    if page.rotation:
                        page.remove_rotation()
                    page_records, diagnostics = PARSERS[kind](page, path.name)
                    for record in page_records:
                        record.debug.tier = 3
                    levels = {r.debug.niveau for r in page_records if r.debug.niveau}
                    report = SheetReport(
                        f"{path.stem} p{number}", number, kind,
                        next(iter(levels)) if len(levels) == 1 else None,
                        len(page_records), sum(r.element != "UNKNOWN" for r in page_records),
                        "extracted" if page_records else "no_callouts",
                        None if page_records else "Aucune armature localisée et lisible sur cette page",
                        {**diagnostics, "folder": path.parent.name, "parser": f"{kind}_clp",
                         "last_page_only": last_page_only(kind, path), "document_pages": len(document)},
                        fichier=path.name, tier=3)
                    after = path.stat()
                    if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                        raise RuntimeError(f"Le fichier a changé pendant la lecture : {path.name}")
                    if page_cache:
                        write_pickle(page_cache, {"fingerprint": page_fingerprint,
                                                  "records": page_records, "sheet": report})
                recs.extend(page_records)
                reports.append(report)
                if page_progress and kind in {"colonne", "radier"}:
                    page_progress(path.name, number, len(document), True, cached is not None)
        if kind == "colonne" and all("cells" in s.diagnostics for s in reports):
            # Reuse the column parser's existing coordinate/storey deduplication
            # across the whole PDF, retaining conflicting specifications.
            cells = [colonne_clp.Cell(**cell) for s in reports for cell in s.diagnostics["cells"]]
            recs = colonne_clp.records(cells, path.name)
            _, deduplication = colonne_clp.clean_output(cells, path.name)
            reports[0].diagnostics["file_deduplication"] = deduplication
            for report in reports:
                kept = [r for r in recs if r.page == report.page]
                report.records = len(kept)
                report.located = sum(r.element != "UNKNOWN" for r in kept)
        sheets.extend(reports)
        records.extend(recs)
        sources[-1].update(page=reports[-1].page, pages=[s.page for s in reports],
                           last_page_only=last_page_only(kind, path))
        if checkpoint:
            after = path.stat()
            if (after.st_mtime_ns, after.st_size) != (stat.st_mtime_ns, stat.st_size):
                raise RuntimeError(f"Le fichier a changé pendant la lecture : {path.name}")
            payload = {"fingerprint": fingerprint, "records": recs, "sheets": reports,
                       "sheet": reports[0]}
            write_pickle(checkpoint, payload)
            if page_progress and kind not in {"colonne", "radier"}:
                page_progress(path.name, reports[0].page,
                              reports[0].diagnostics['document_pages'], True, False)
            if file_done:
                file_done(index, len(files), str(path.resolve()), False)
    # The supplemental sheet is a fallback only. Never revive an older schedule
    # for a coordinate that the current release already covers (even if empty
    # at a particular storey). Keep the suppression evidence for review.
    current_coordinates = {cell["coordinate"] for report in sheets
                           if report.type_element == "colonne" and report.fichier != COLUMN_SUPPLEMENT
                           for cell in report.diagnostics.get("cells", []) if cell.get("coordinate")}
    suppressed = [r.id for r in records if r.type_element == "colonne" and
                  r.fichier == COLUMN_SUPPLEMENT and r.element in current_coordinates]
    records = [r for r in records if r.id not in suppressed]
    for report in sheets:
        if report.type_element == "colonne" and report.fichier == COLUMN_SUPPLEMENT:
            report.diagnostics["superseded_by_current_schedule"] = suppressed
            report.records = report.located = sum(r.fichier == report.fichier and r.page == report.page
                                                  for r in records)
    records.sort(key=lambda r: (r.fichier, *sort_key(r)))
    # Distinct conflicting readings stay visible; identifiers must stay unique.
    used = set()
    for index, record in enumerate(records, 1):
        original = record.id
        suffix = index
        while record.id in used:
            record.id = f"{original.removesuffix('_atelier')}#{suffix}_atelier"
            suffix += 1
        used.add(record.id)
    return ProjectResult(
        Path(project_dir).name, f"DA ({len(files)} fichiers, colonnes : toutes les pages)",
        "imperial", {"basis": "CLP fabricator notation; dedicated parsers convert inches to mm"},
        align_records(records), sheets, round(time.monotonic() - started, 2),
        meta={"parser_version": PARSER_VERSION, "files": len(files), "pages": len(sheets),
              "inputs": sources, "last_page_only": not ({"colonne", "radier"} & inputs.keys()),
              "page_policy": {kind: "all" if kind in {"colonne", "radier"} else "last" for kind in inputs},
              "tiers": {3: len(sheets)},
              "checkpoint_hits": checkpoint_hits,
              "page_checkpoint_hits": page_checkpoint_hits,
              "column_record_format": COLUMN_FORMAT,
              "spec_format": FORMAT_VERSION,
              "pending_types": [], "scope": "five CLP format parsers"})
