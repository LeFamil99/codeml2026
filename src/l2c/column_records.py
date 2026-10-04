"""One shared column specification for original plans and fabrication schedules.

Primary output: vertical quantity/diameter and tie diameter/spacing. Bar marks,
lengths, tie piece counts, dowels and slab ties remain in the source evidence.
This is a representation upgrade; it never opens a PDF or reruns OCR.
"""
from collections import OrderedDict
import re

from .model import Armature

COLUMN_FORMAT = "column-primary-spec-v1"


def align_column_records(records):
    output = []
    for record in records:
        if record.type_element != "colonne" or getattr(record.debug,"record_format",None) == COLUMN_FORMAT:
            output.append(record)
            continue
        roles = getattr(record.debug,"roles",[])
        details = []
        for i, bar in enumerate(record.armature):
            role = roles[i] if i < len(roles) else None
            if not role:
                role = "ETRI" if bar.espacement_mm is not None else "VERT"
            role = str(role).upper().replace("É","E")
            if role.startswith("ETRI"):
                role = "ETRI"
            details.append(dict(role=role, **bar.model_dump()))
        limit = len(details)
        # A separate muret drawn in the RDC band is additional fabrication,
        # not four more parallel verticals in the building column.
        if record.source == "atelier":
            boundary = next((i for i,t in enumerate(record.debug.raw)
                             if re.match(r"^MURET\b",t.strip(),re.I)),None)
            if boundary is not None:
                from .da.common import parse_bar_line
                limit = sum(parse_bar_line(t,"imperial") is not None
                            for t in record.debug.raw[:boundary])
        verticals, ties = OrderedDict(), OrderedDict()
        for detail in details[:limit]:
            if detail["role"] == "VERT" and detail["espacement_mm"] is None:
                diameter = detail["diametre"]
                quantity = detail["quantite"]
                if diameter in verticals:
                    old = verticals[diameter]
                    verticals[diameter] = old+quantity if old is not None and quantity is not None else None
                else:
                    verticals[diameter] = quantity
            elif detail["role"] == "ETRI" and detail["espacement_mm"] is not None:
                key = detail["diametre"],detail["espacement_mm"]
                ties[key] = None
        primary = [Armature(diametre=diameter,quantite=quantity)
                   for diameter,quantity in verticals.items()]
        primary += [Armature(diametre=diameter,espacement_mm=spacing)
                    for diameter,spacing in ties]
        debug = record.debug.model_copy(deep=True)
        debug.record_format = COLUMN_FORMAT
        debug.reinforcement_details = details
        debug.roles = ["VERT"] * len(verticals) + ["ETRI"] * len(ties)
        debug.primary_reinforcement_count = limit
        output.append(record.model_copy(update={"armature":primary,"debug":debug}))
    return output


def align_result(result):
    columns = [r for r in result.records if r.type_element == "colonne"]
    if not columns or all(getattr(r.debug,"record_format",None) == COLUMN_FORMAT for r in columns):
        return False
    result.records = align_column_records(result.records)
    result.meta["column_record_format"] = COLUMN_FORMAT
    return True
