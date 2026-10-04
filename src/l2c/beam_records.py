"""Shared storage unit for plan and DA: one beam view with all its armatures.

This is representation normalization, not extraction. It also upgrades trusted
saved results without reopening PDFs or invalidating expensive OCR checkpoints.
"""
from collections import OrderedDict
import json

from .model import Debug, sort_key

BEAM_FORMAT = "beam-with-armatures-v1"


def align_beam_records(records):
    groups = OrderedDict()
    for record in records:
        if record.type_element != "poutre" or record.element == "UNKNOWN":
            key = ("individual", id(record))
        else:
            key = (record.source, record.fichier, record.feuillet, record.page,
                   record.debug.niveau, record.element, getattr(record.debug, "view", None))
        groups.setdefault(key, []).append(record)
    output = []
    for members in groups.values():
        first = members[0]
        if first.type_element != "poutre":
            output.extend(members)
            continue
        if len(members) == 1 and getattr(first.debug, "record_format", None) == BEAM_FORMAT:
            output.append(first)
            continue
        armatures, roles, annotations = [], [], []
        seen_observations = set()
        for record in members:
            observation = (record.x, record.y,
                json.dumps([a.model_dump() for a in record.armature], sort_keys=True),
                tuple(record.debug.raw), tuple(getattr(record.debug, "roles", [])),
                getattr(record.debug, "role", None),
                json.dumps(getattr(record.debug, "annotations", []), sort_keys=True))
            if observation in seen_observations:
                continue
            seen_observations.add(observation)
            existing = getattr(record.debug, "annotations", [])
            labels = getattr(record.debug, "roles", [])
            for index, bar in enumerate(record.armature):
                role = labels[index] if index < len(labels) else getattr(record.debug, "role", None)
                annotation = dict(existing[index]) if index < len(existing) else {
                    "role": role,
                    "raw": record.debug.raw[index] if index < len(record.debug.raw) else "",
                    "source_id": record.id,
                    "confidence": record.debug.confidence,
                    # Legacy plan records locate one callout; legacy grouped DA
                    # records locate the whole beam, not individual bars.
                    "x": record.x if record.source == "plan" and len(record.armature) == 1 else None,
                    "y": record.y if record.source == "plan" and len(record.armature) == 1 else None,
                    "bbox": record.debug.symbol_bbox if len(record.armature) == 1 else None,
                }
                armatures.append(bar)
                roles.append(role)
                annotations.append(annotation)
        data = first.debug.model_dump()
        data.pop("role", None)
        data.update(roles=roles, annotations=annotations, record_format=BEAM_FORMAT,
                    raw=[a.get("raw", "") for a in annotations],
                    confidence=min(r.debug.confidence for r in members))
        anchor = getattr(first.debug, "beam_anchor", None)
        x, y = anchor if anchor is not None else (first.x, first.y)
        output.append(first.model_copy(update={"armature": armatures,
                                              "debug": Debug(**data), "x": x, "y": y}))
    return sorted(output, key=sort_key)


def align_result(result):
    """Upgrade a ProjectResult in place; report whether its storage changed."""
    beam_records = [r for r in result.records if r.type_element == "poutre"]
    if not beam_records or all(getattr(r.debug, "record_format", None) == BEAM_FORMAT for r in beam_records):
        return False
    result.records = align_beam_records(result.records)
    for sheet in result.sheets:
        if sheet.type_element != "poutre":
            continue
        members = [r for r in result.records if r.type_element == "poutre" and
                   r.feuillet == sheet.feuillet and r.page == sheet.page and
                   (sheet.fichier is None or sheet.fichier == r.fichier)]
        sheet.records = len(members)
        sheet.located = sum(r.element != "UNKNOWN" for r in members)
        sheet.diagnostics.update(records=sheet.records, located=sheet.located,
                                 reinforcement_entries=sum(len(r.armature) for r in members))
    result.meta['beam_record_format'] = BEAM_FORMAT
    return True
