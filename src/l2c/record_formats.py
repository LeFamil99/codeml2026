"""Shared plan/DA storage, display and comparison contracts.

Normalize representation only. Never fill missing specs from the other dataset,
guess a slab layer/direction, or merge bars in distinct directions or beam zones.
Source fabrication details and annotations remain in debug evidence.
"""
import re
import unicodedata

from .beam_records import align_beam_records, align_result as align_beams
from .column_records import align_column_records
from .model import Armature

FORMAT_VERSION = "shared-element-spec-v1"
SUPPORTED = {"colonne","semelle","dalle","poutre","radier"}


def fold(value):
    return re.sub(r"\s+"," ",unicodedata.normalize("NFKD",str(value or ""))
                  .encode("ascii","ignore").decode().upper()).strip()


def normalize_level(value):
    level = fold(value)
    if level in {"FONDATION","FONDATIONS"}:
        return "FONDATION"
    if level in {"REZ-DE-CHAUSSEE","RDC","NIVEAU RDC","NIV RDC"}:
        return "RDC"
    return re.sub(r"^NIV\.?\s*","NIVEAU ",level) if level and not level.startswith("NIVEAU") else level or None


def normalize_role(kind, value):
    role = fold(value).strip(" .:_-")
    if kind == "radier":
        return {"HORIZONTAL":"horizontal", "HORIZONTALE":"horizontal",
                "VERTICAL":"vertical", "VERTICALE":"vertical"}.get(role,"INCONNU")
    if kind == "poutre":
        return {"LONG":"longitudinale","LONGITUDINALE":"longitudinale",
                "PEAU":"peau","SKIN":"peau","ETRI":"étriers",
                "ETRIER":"étriers","ETRIERS":"étriers"}.get(role,"INCONNU")
    if kind == "semelle":
        return {"LONG":"LONG","LONGITUDINALE":"LONG","LONGITUDINAL":"LONG",
                "TRAN":"TRAN","TRANS":"TRAN","TRANSVERSALE":"TRAN",
                "TRANSVERSAL":"TRAN"}.get(role,"INCONNU")
    # NUM and ALP are explicit reinforcement directions. The orientation of
    # the printed text alone is insufficient evidence to assign either.
    return role if role in {"NUM","ALP"} else "INCONNU"


def footing_roles(record):
    hints = []
    for text in record.debug.raw:
        if re.search(r"ARM\.?\s*LONG",fold(text)):
            hints.append("LONG")
        elif re.search(r"ARM\.?\s*TRANS",fold(text)):
            hints.append("TRAN")
    if len(hints) == len(record.armature):
        return hints
    return ["LONG","TRAN"] if len(record.armature) == 2 else ["INCONNU"] * len(record.armature)


def align_records(records):
    records = align_column_records(align_beam_records(records))
    output = []
    for record in records:
        if record.type_element not in SUPPORTED or getattr(record.debug,"spec_format",None) == FORMAT_VERSION:
            output.append(record)
            continue
        debug = record.debug.model_copy(deep=True)
        debug.niveau = normalize_level(debug.niveau)
        debug.spec_format = FORMAT_VERSION
        if record.type_element == "colonne":
            output.append(record.model_copy(update={"debug":debug}))
            continue
        kind = record.type_element
        if kind == "radier":
            debug.niveau = debug.niveau or "FONDATION"
            layer = fold(getattr(debug, "layer", None))
            layer = re.sub(r"^RANGS?\s*", "", layer).strip(" :")
            debug.layer = layer or "INCONNU"
            debug.direction = normalize_role(kind, getattr(debug, "direction", None))
        labels = getattr(debug,"roles",[])
        if kind == "radier":
            labels = [debug.direction] * len(record.armature)
        if not labels and kind == "semelle":
            labels = footing_roles(record)
        if kind == "semelle" and not getattr(debug,"footing_type",None):
            match = next((re.search(r"\bTYPE\s+([A-Z])\b",fold(text))
                          for text in debug.raw if re.search(r"\bTYPE\s+([A-Z])\b",fold(text))),None)
            if match:
                debug.footing_type = match.group(1)
        if kind == "dalle":
            layer = fold(getattr(debug,"layer",None) or getattr(debug,"reinforcement_kind",None))
            debug.layer = layer if layer in {"INTEGRITE","HAUT","BAS"} else "INCONNU"
        details, entries = [], []
        annotations = getattr(debug,"annotations",[])
        for i, bar in enumerate(record.armature):
            label = labels[i] if i < len(labels) else getattr(debug,"role",None)
            role = normalize_role(kind,label)
            annotation = dict(annotations[i]) if i < len(annotations) else {}
            detail = dict(role=role,source_role=label,**bar.model_dump(),source_index=i,
                          annotation=annotation)
            details.append(detail)
            fields = bar.model_dump()
            # Fabricator shape/mark identifiers are not plan design requirements.
            # Explicit physical lengths retain exactly the same mm meaning.
            fields["repere"] = None
            if (kind == "radier" or (kind == "poutre" and role in {"peau","étriers"})) and bar.espacement_mm is not None:
                fields["quantite"] = None  # pieces cut vs a spacing requirement
            entries.append((role,Armature(**fields),i))
        orders = {"semelle":["LONG","TRAN"],"dalle":["NUM","ALP"],
                  "poutre":["longitudinale","peau","étriers"],
                  "radier":["horizontal","vertical"]}[kind]
        entries.sort(key=lambda item: orders.index(item[0]) if item[0] in orders else len(orders))
        debug.roles = [role for role,_,_ in entries]
        debug.reinforcement_details = details
        if annotations:
            debug.annotations = [dict(annotations[i],role=role) if i < len(annotations)
                                 else {"role":role} for role,_,i in entries]
        debug.missing_roles = ([role for role in ("LONG","TRAN") if role not in debug.roles]
                               if kind == "semelle" else
                               [role for role in ("NUM","ALP") if role not in debug.roles]
                               if kind == "dalle" and debug.layer == "INTEGRITE" else [])
        debug.unresolved_roles = any(role == "INCONNU" for role in debug.roles)
        output.append(record.model_copy(update={"armature":[bar for _,bar,_ in entries],"debug":debug}))
    return output


def align_result(result):
    changed = align_beams(result)
    if any(r.type_element in SUPPORTED and getattr(r.debug,"spec_format",None) != FORMAT_VERSION
           for r in result.records):
        result.records = align_records(result.records)
        result.meta["spec_format"] = FORMAT_VERSION
        changed = True
    return changed
