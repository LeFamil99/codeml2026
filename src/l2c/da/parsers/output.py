"""Shared final-output deduplication. Diagnostics retain every source occurrence."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable


def reinforcement_key(bars: list[dict]) -> tuple:
    """Preserve roles, multiplicity and fabrication attributes, ignoring OCR prose."""
    fields = ("role", "quantite", "diametre", "espacement_mm", "longueur_mm", "repere")
    return tuple(sorted((tuple(b.get(k) for k in fields) for b in bars), key=repr))


def deduplicate(rows: list[dict], identity: Callable, signature: Callable,
                quality: Callable = lambda row: (row.get("confidence") or 0,)) -> tuple[list[dict], dict]:
    """Remove identical observations of one element; preserve conflicting reads.

    Identity describes the element and level/layer. Signature describes its actual
    reinforcement, not its confidence, OCR wording, page/view or bounding box.
    Equal quantities in two directions remain two entries inside one record.
    """
    groups = defaultdict(list)
    for row in rows:
        groups[(identity(row), signature(row))].append(row)
    kept, duplicates, signatures = [], [], defaultdict(set)
    provenance_fields = ("fichier", "page", "view", "coordinate", "element", "position", "storey", "niveau",
                         "layer", "type", "summary", "confidence")
    def provenance(row):
        return {k: row[k] for k in provenance_fields if row.get(k) is not None}
    for (element, value), occurrences in groups.items():
        best = max(occurrences, key=quality)
        kept.append(dict(best))
        signatures[element].add(value)
        if len(occurrences) > 1:
            discarded = list(occurrences)
            discarded.remove(best)
            duplicates.append(dict(kept=provenance(best),
                                   suppressed=[provenance(row) for row in discarded]))
    conflicts = [dict(identity=list(element), variants=len(values))
                 for element, values in signatures.items() if len(values) > 1]
    for row in kept:
        if len(signatures[identity(row)]) > 1:
            row["duplicate_conflict"] = True
            reason = "conflicting repeated annotations; review diagnostics"
            row["reason"] = f"{row['reason']}; {reason}" if row.get("reason") else reason
            if row.get("status") == "read":
                row["status"] = "partial"
    return kept, dict(input_rows=len(rows), output_rows=len(kept),
                      removed_rows=len(rows)-len(kept), duplicates=duplicates, conflicts=conflicts)


def view_correspondences(views: list[dict], tolerance: float = 3.) -> dict[str, dict]:
    """Infer repeated details using observed axes only, never an original plan.

    Three shared number labels must agree on x translation and three shared row
    labels on y translation. Alternate row names (CLP inset Q / main G) are then
    matched by their measured positions. Unaligned views remain independent.
    """
    if not views:
        return {}
    primary = max(views, key=lambda v: len(v["letters"])*len(v["numbers"]))
    result = {primary["id"]: dict(group=primary["id"], primary=True,
                                 letters={k:k for k in primary["letters"]},
                                 numbers={k:k for k in primary["numbers"]})}
    for view in views:
        if view is primary:
            continue
        offsets = []
        for name in ("numbers", "letters"):
            common = view[name].keys() & primary[name].keys()
            values = sorted(view[name][k]-primary[name][k] for k in common)
            if len(values) < 3 or values[-1]-values[0] > tolerance:
                break
            offsets.append(values[len(values)//2])
        if len(offsets) != 2:
            continue
        maps = {}
        for name, offset in zip(("numbers", "letters"), offsets):
            maps[name] = {}
            for label, position in view[name].items():
                matches = [k for k,p in primary[name].items()
                           if abs(position-offset-p) <= tolerance]
                if len(matches) == 1:
                    maps[name][label] = matches[0]
        result[view["id"]] = dict(group=primary["id"], primary=False,
                                 offset_x=offsets[0], offset_y=offsets[1], **maps)
    return result


def grid_identity(row: dict, correspondences: dict) -> tuple:
    mapping = correspondences.get(row.get("view"))
    coordinate = row["coordinate"]
    group = row.get("view")
    if mapping:
        letter, number = coordinate.split("-", 1)
        if letter in mapping["letters"] and number in mapping["numbers"]:
            coordinate = f"{mapping['letters'][letter]}-{mapping['numbers'][number]}"
            group = mapping["group"]
    return group, coordinate


def grid_quality(row: dict, correspondences: dict) -> tuple:
    return (bool(correspondences.get(row.get("view"), {}).get("primary")),
            row.get("status") == "read", row.get("confidence") or 0)
