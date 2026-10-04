"""Compare parsed observations without borrowing data across readers.

Exact locations, levels and slab layers define groups. Unmatched coverage and
incomplete/spatially ambiguous evidence stay visible for human review.
"""
import collections
from collections import defaultdict
import math
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
    if record.type_element == "radier":
        layer = f"{layer} · {normalize(getattr(record.debug, 'direction', ''))}"
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


def length_missing(left, right, field):
    return field == "longueur_mm" and (left[field] is None or right[field] is None)


def same_bar(left, right):
    """A length stated on one side only is not a difference: the other side does not give it."""
    return all(left[f] == right[f] or length_missing(left, right, f) for f in left)


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


SLAB_LAYERS = ("HAUT", "BAS")
SLAB_BOTH = "HAUT+BAS"


def grid_position(coordinate):
    """(letter axis, number axis) of a label such as G.5-8 or A-12.7; None if unparsed."""
    match = re.fullmatch(r"([A-Z])(?:\.(\d+))?-(\d+(?:\.\d+)?)", coordinate)
    if not match:
        return None
    letter, fraction, number = match.groups()
    return ord(letter) - ord("A") + (float(f"0.{fraction}") if fraction else 0), float(number)


def grid_steps(keys):
    """Ordered grid positions printed on either side. A half-letter line (G.5) shares the
    position of its whole letter (G), so E to I is four positions with G.5 between."""
    positions = [grid_position(key[3]) for key in keys]
    letters = sorted({math.floor(p[0]) for p in positions if p})
    numbers = sorted({p[1] for p in positions if p})
    return {v: i for i, v in enumerate(letters)}, {v: i for i, v in enumerate(numbers)}


def positions_apart(left, right, steps, reach):
    """Grid lines at most `reach` positions apart on one axis, on the same line on the other
    (15.8 and 16 at reach 1; G and H at reach 2)."""
    letters, numbers = steps
    (la, na), (lb, nb) = left, right
    la, lb = math.floor(la), math.floor(lb)
    if la == lb and 1 <= abs(numbers[na] - numbers[nb]) <= reach:
        return True
    return na == nb and 1 <= abs(letters[la] - letters[lb]) <= reach


def within_one_unit(left, right):
    """Less than one unit apart on both axes (G.5-12.5 and H-12.7)."""
    return abs(left[0] - right[0]) < 1 and abs(left[1] - right[1]) < 1


def slab_scope(key):
    """Unlayered plan slab steel counts as HAUT+BAS for matching; everything else is itself."""
    return key[:2] + (SLAB_BOTH if key[0] == "dalle" and "INCONNU" in key[2] else key[2],)


def nearby_pairs(plan_keys, atelier_keys, steps, reach, identical):
    """Greedy one-to-one pairing of unmatched groups within `reach` grid positions, same type/level/layer.
    At equal distance, pairs whose bars are identical come first."""
    candidates = []
    for plan_key in plan_keys:
        left = grid_position(plan_key[3])
        for atelier_key in atelier_keys:
            if slab_scope(plan_key)[:3] != slab_scope(atelier_key)[:3]:
                continue
            right = grid_position(atelier_key[3])
            if left is None or right is None:
                continue
            if not (positions_apart(left, right, steps, reach) or
                    (reach == 1 and within_one_unit(left, right))):
                continue
            distance = abs(left[0] - right[0]) + abs(left[1] - right[1])
            candidates.append(((distance, not identical(plan_key, atelier_key)), plan_key, atelier_key))
    pairs, used_plan, used_atelier = [], set(), set()
    for _, plan_key, atelier_key in sorted(candidates, key=lambda c: c[0]):
        if plan_key in used_plan or atelier_key in used_atelier:
            continue
        pairs.append((plan_key, atelier_key))
        used_plan.add(plan_key)
        used_atelier.add(atelier_key)
    return pairs


def per_diameter(entries):
    """Slab totals: the plan gives counts per callout, the DA splits them into pieces."""
    totals = {}
    for bar in entries:
        if bar["diametre"] not in totals:
            totals[bar["diametre"]] = dict(bar, role="", repere=None, espacement_mm=None,
                                           longueur_mm=None, quantite=0)
        totals[bar["diametre"]]["quantite"] += bar["quantite"] or 0
    return list(totals.values())


def bars_match(left, right):
    pairs, missing, extra = match_bars(left, right)
    return bool(left) and not missing and not extra and all(same_bar(left[i], right[j]) for i, j in pairs)


CHANGED = "Armatures différentes ou annotations supplémentaires."


def compare(plan, atelier):
    """Changed rows only: both readers have the element, and their bars differ."""
    return compare_with_totals(plan, atelier)[0]


def compare_with_totals(plan, atelier):
    """(changed rows, number of elements compared per type): compared means both readers have it."""
    from .record_formats import align_result
    align_result(plan)
    align_result(atelier)
    groups = [defaultdict(list), defaultdict(list)]
    for mapping, dataset in zip(groups, (plan, atelier)):
        for record in dataset.records:
            # The DA reads only the integrity slab sheet, so only plan integrity steel is compared.
            if mapping is groups[0] and record.type_element == "dalle" and \
                    getattr(record.debug, "reinforcement_kind", None) != "integrity":
                continue
            mapping[group_key(record)].append(record)
    # Plan slab steel names neither a layer nor a direction. It is compared with the
    # DA's HAUT and BAS sheets together, ignoring direction, at the same element and level.
    plan_slab_levels = {key[1] for key in groups[0] if key[0] == "dalle" and "INCONNU" in key[2]}
    for key in [k for k in groups[1] if k[0] == "dalle" and k[2] in SLAB_LAYERS and k[1] in plan_slab_levels]:
        groups[1][(key[0], key[1], SLAB_BOTH, key[3])].extend(groups[1].pop(key))
    # Pass 1: identical labels. Pass 2: slab steel against the DA union. Pass 3: grid-close labels,
    # one grid position at a time (1 to 4). Labels are never rewritten; each unit keeps both originals.
    keys = sorted(groups[0].keys() | groups[1].keys())
    units = [(key, key) for key in keys if key in groups[0] and key in groups[1]]
    lone_plan = [key for key in keys if key in groups[0] and key not in groups[1]]
    lone_atelier = [key for key in keys if key in groups[1] and key not in groups[0]]
    atelier_set = set(lone_atelier)
    slab_pairs = [(key, (key[0], key[1], SLAB_BOTH, key[3])) for key in lone_plan
                  if key[0] == "dalle" and "INCONNU" in key[2] and (key[0], key[1], SLAB_BOTH, key[3]) in atelier_set]
    used_plan = {plan_key for plan_key, _ in slab_pairs}
    used_atelier = {atelier_key for _, atelier_key in slab_pairs}
    lone_plan = [key for key in lone_plan if key not in used_plan]
    lone_atelier = [key for key in lone_atelier if key not in used_atelier]
    steps = grid_steps(keys)

    def identical(plan_key, atelier_key):
        return bars_match(bars(groups[0].get(plan_key, [])), bars(groups[1].get(atelier_key, [])))

    paired = list(slab_pairs)
    for reach in (1, 2, 3, 4):
        stage = nearby_pairs(lone_plan, lone_atelier, steps, reach, identical)
        paired += stage
        staged_plan = {plan_key for plan_key, _ in stage}
        staged_atelier = {atelier_key for _, atelier_key in stage}
        lone_plan = [key for key in lone_plan if key not in staged_plan]
        lone_atelier = [key for key in lone_atelier if key not in staged_atelier]
    units += paired
    rows = []
    compared = collections.Counter()
    for plan_key, atelier_key in units:
        slab = bool(plan_key[0] == "dalle" and "INCONNU" in plan_key[2])
        key = atelier_key if slab else plan_key
        kind, storey, layer, coordinate = key
        a, b = groups[0].get(plan_key, []), groups[1].get(atelier_key, [])
        if not a or not b:
            continue
        compared[(plan_key or atelier_key)[0]] += 1
        left, right = bars(a), bars(b)
        if slab:
            left, right = per_diameter(left), per_diameter(right)
        # Integrity steel is compared per direction: a direction read on one side only
        # (NUM alone, or ALP alone) is compared with the same direction on the other side.
        if kind == "dalle" and not slab and left and right:
            common = {bar["role"] for bar in left} & {bar["role"] for bar in right}
            left = [bar for bar in left if bar["role"] in common]
            right = [bar for bar in right if bar["role"] in common]
        _, missing, extra = match_bars(left, right)
        if not (missing or extra):
            continue
        if plan_key == atelier_key:
            match = "exact"
        elif plan_key[3] == atelier_key[3]:
            match = "couche"
        else:
            match = "proche"
        rows.append(dict(type_element=kind, niveau=storey, layer=layer, element=coordinate,
                         status="changed", reason=CHANGED, plan=left, atelier=right,
                         unmatched_plan=missing, unmatched_atelier=extra,
                         plan_sources=evidence(a), atelier_sources=evidence(b),
                         plan_element=plan_key[3], atelier_element=atelier_key[3],
                         coordinate_match=match))
    return rows, dict(compared)
