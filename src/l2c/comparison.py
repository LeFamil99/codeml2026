"""Compare parsed observations without borrowing data across readers.

Exact locations, levels and slab layers define groups. Unmatched coverage and
incomplete/spatially ambiguous evidence stay visible for human review.
"""
from collections import defaultdict
import re
import unicodedata


def normalize(value):
    text = unicodedata.normalize("NFKD", str(value or "").replace("–", "-").replace("—", "-")).encode("ascii", "ignore").decode().upper()
    return re.sub(r"\s+", " ", text).strip()


def level(record):
    text = normalize(record.debug.niveau)
    if text in ("FONDATION", "FONDATIONS") or (not text and record.type_element == "semelle"):
        return "FONDATION"
    if text in ("REZ-DE-CHAUSSEE", "RDC", "NIVEAU RDC", "NIV RDC"):
        return "RDC"
    return re.sub(r"^NIV\.?\s*", "NIVEAU ", text) if not text.startswith("NIVEAU") else text


def group_key(record):
    layer = normalize(getattr(record.debug, "layer", ""))
    if record.type_element == "dalle" and not layer:
        layer = normalize(getattr(record.debug, "reinforcement_kind", "")) or "INCONNU"
    coordinate = re.sub(r"\s", "", normalize(record.element)).replace("–", "-")
    coordinate = re.sub(r"(?<=\d)\.0(?=-|$)", "", coordinate)
    return record.type_element, level(record), layer, coordinate


def bars(records):
    result = []
    seen = set()
    for record in records:
        # Drop only repeated observations at the same place; repeated bars in
        # different positions and the LONG/TRAN pair remain distinct.
        identity = (record.fichier, record.page, round(record.x, 1), round(record.y, 1),
                    str(record.armature), str(getattr(record.debug, "roles", [])),
                    getattr(record.debug, "role", None), getattr(record.debug, "direction", None))
        if identity in seen:
            continue
        seen.add(identity)
        roles = getattr(record.debug, "roles", [])
        for i, bar in enumerate(record.armature):
            role = roles[i] if i < len(roles) else getattr(record.debug, "role", "")
            if not role and record.type_element == "semelle":
                role = ("LONG", "TRAN")[i] if i < 2 else "INCONNU"
            if not role and record.type_element == "colonne":
                role = "ETRI" if bar.espacement_mm is not None else "VERT"
            role = normalize(role)
            if role in ("ETRIERS", "ETRI", "ETRIER"):
                role = "ETRI"
            result.append(dict(role=role, **bar.model_dump()))
    return result


def compatible(left, right):
    if left["role"] != right["role"]:
        return False
    for field in ("diametre", "quantite", "espacement_mm", "longueur_mm", "repere"):
        a, b = left[field], right[field]
        if a is not None and b is not None:
            if field.endswith("_mm"):
                if abs(a - b) > .5:
                    return False
            elif a != b:
                return False
    return True


def match_bars(left, right):
    """Maximum one-to-one matching; ordering and unknown values cannot hide extras."""
    matched = {}
    def augment(index, visited):
        candidates = [j for j, bar in enumerate(right) if compatible(left[index], bar)]
        candidates.sort(key=lambda j: sum(left[index][k] != right[j][k] for k in left[index]))
        for j in candidates:
            if j in visited:
                continue
            visited.add(j)
            if j not in matched or augment(matched[j], visited):
                matched[j] = index
                return True
        return False
    for i in range(len(left)):
        augment(i, set())
    pairs = [(i, j) for j, i in matched.items()]
    return pairs, [b for i, b in enumerate(left) if i not in matched.values()], [b for j, b in enumerate(right) if j not in matched]


def evidence(records):
    return [dict(id=r.id, fichier=r.fichier, feuillet=r.feuillet, page=r.page,
                 x=r.x, y=r.y, debug=r.debug.model_dump(mode="json")) for r in records]


def compare(plan, atelier):
    from .record_formats import align_result
    align_result(plan)
    align_result(atelier)
    groups = [defaultdict(list), defaultdict(list)]
    for mapping, dataset in zip(groups, (plan, atelier)):
        for record in dataset.records:
            mapping[group_key(record)].append(record)
    scopes = {key[:3] for key in groups[1]}
    rows = []
    for key in sorted(groups[0].keys() | groups[1].keys()):
        kind, storey, layer, coordinate = key
        a, b = groups[0].get(key, []), groups[1].get(key, [])
        left, right = bars(a), bars(b)
        status, reason = "same", "Armatures extraites identiques."
        pairs, missing, extra = match_bars(left, right)
        if key[:3] not in scopes:
            status, reason = "out_of_scope", "Type, niveau ou couche absent des résultats DA chargés."
        elif coordinate == "UNKNOWN" or (not storey and kind != "poutre") or layer == "INCONNU":
            status, reason = "review", "Localisation, niveau ou couche non résolu : appariement à vérifier."
        elif not a:
            status, reason = "missing_plan", "Élément trouvé dans les DA uniquement."
        elif not b:
            status, reason = "missing_da", "Élément trouvé dans le plan uniquement."
        elif any(getattr(r.debug,"unresolved_roles",False) or getattr(r.debug,"missing_roles",[])
                 for r in a+b):
            status, reason = "review", "Direction ou rôle d'armature non résolu : vérifier les sources."
        elif missing or extra:
            status, reason = "changed", "Armatures différentes ou annotations supplémentaires."
        elif (kind == "poutre" or any(left[i] != right[j] for i, j in pairs)
              or not left or any(not bar['role'] or bar['diametre'] is None or
                                 (bar['quantite'] is None and bar['espacement_mm'] is None)
                                 for bar in left + right)
              or any(getattr(r.debug, "duplicate_conflict", False) or
                                 r.debug.confidence < .65 for r in a + b)):
            status, reason = "review", "Lecture incomplète, conflit ou placement des armatures de poutre à vérifier."
        rows.append(dict(type_element=kind, niveau=storey, layer=layer, element=coordinate,
                         status=status, reason=reason, plan=left, atelier=right,
                         unmatched_plan=missing, unmatched_atelier=extra,
                         plan_sources=evidence(a), atelier_sources=evidence(b)))
    return rows
