"""JSON emission and validation - the Appendix-A contract (PLAN SS9.1)."""

from __future__ import annotations

import json
import os
from typing import Any

from pydantic import ValidationError

from .model import ElementRecord
from .record_formats import align_records

APPENDIX_A_FIELDS = {"id", "source", "fichier", "feuillet", "page", "x", "y",
                     "type_element", "element", "armature"}



PIPELINE_VERSION = "0.3.2-grouped-beams"

def dump_records(records: list[ElementRecord]) -> list[dict[str, Any]]:
    return [r.to_schema() for r in align_records(records)]


def write_records(path: str, records: list[ElementRecord]) -> int:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    payload = dump_records(records)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
    return len(payload)


def validate_file(path: str) -> tuple[int, list[str]]:
    """Re-read a JSON file and check every record against the schema.

    Lets the jury verify conformance without running the pipeline.
    """
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    errors: list[str] = []
    if not isinstance(payload, list):
        return 0, ["top level must be a flat JSON array of records"]
    for i, row in enumerate(payload):
        extra = set(row) - APPENDIX_A_FIELDS
        missing = APPENDIX_A_FIELDS - set(row)
        if extra:
            errors.append(f"[{i}] unexpected field(s): {sorted(extra)}")
        if missing:
            errors.append(f"[{i}] missing field(s): {sorted(missing)}")
        try:
            ElementRecord(**row)
        except ValidationError as e:
            errors.append(f"[{i}] {e.errors()[0]['msg']}")
    return len(payload), errors


def write_run_manifest(path: str, result, extra: dict | None = None) -> None:
    import hashlib
    import platform
    from datetime import datetime, timezone

    manifest = {
        "pipeline_version": PIPELINE_VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "project": result.project,
        "plan_file": result.plan_file,
        "unit_system": result.unit_system,
        "unit_evidence": result.unit_evidence,
        "scope": "plan side, all six element types; DA reading and comparison are later stages",
        "totals": result.totals,
        "elapsed_s": result.elapsed_s,
    }
    manifest.update(extra or {})
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2, sort_keys=True)
        fh.write("\n")
