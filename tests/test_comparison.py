from l2c.comparison import compare
from l2c.model import ElementRecord, Debug, Armature
from l2c.pipeline import ProjectResult
from test_da_dashboard import connected_parsers


def record(element="L-13", quantity=9, kind="semelle", level="FONDATIONS", **debug):
    return ElementRecord(id="sample", source="plan", fichier="plan.pdf", feuillet="S-100",
                         page=1, x=1, y=2, type_element=kind, element=element,
                         armature=[Armature(quantite=quantity, diametre="25M")] * 2,
                         debug=Debug(niveau=level, **debug))


def dataset(records):
    return ProjectResult("CLP", "source.pdf", "imperial", {}, records, [], 0.)


def test_true_footing_difference_missing_elements_and_level_normalization():
    plan = dataset([record(), record("A-1"), record("B-2")])
    da = dataset([record(quantity=11, level="FONDATION"), record("A-1", level="FONDATION"), record("C-3")])
    rows = {r['element']: r for r in compare(plan, da)}
    assert set(rows) == {"L-13"} and rows['L-13']['status'] == "changed"
    assert [b['quantite'] for b in rows['L-13']['plan']] == [9, 9]
    assert [b['quantite'] for b in rows['L-13']['atelier']] == [11, 11]
    assert rows['L-13']['plan_sources'][0]['fichier'] == "plan.pdf"


def test_slabs_compare_only_same_level_layer_and_keep_both_directions():
    integral = record(kind="dalle", level="REZ-DE-CHAUSSÉE", layer="intégrité", roles=["NUM", "ALP"],
                      reinforcement_kind="integrity")
    ordinary = integral.model_copy(update={"debug": Debug(niveau="RDC", reinforcement_kind="slab")})
    upstairs = integral.model_copy(update={"debug": Debug(niveau="NIVEAU 2", layer="intégrité",
                                                          reinforcement_kind="integrity")})
    da = integral.model_copy(update={"debug": Debug(niveau="NIV RDC", layer="integrite", roles=["ALP", "NUM"])})
    # Only integrity steel is compared, and identical bars are not reported.
    assert compare(dataset([integral, ordinary, upstairs]), dataset([da])) == []


def test_repeated_observation_is_removed_but_independent_annotations_stay():
    one = record()
    assert compare(dataset([one, one.model_copy()]), dataset([one])) == []
    another = one.model_copy(update={"x": 20})
    rows = compare(dataset([one, another]), dataset([one]))
    assert rows[0]['status'] == "changed" and len(rows[0]['plan']) == 4


def test_one_sided_cut_lengths_are_not_a_difference_but_conflicting_lengths_are():
    plan = record(kind="dalle", level="NIVEAU 2", layer="intégrité", roles=["NUM", "ALP"],
                  reinforcement_kind="integrity")
    da = plan.model_copy(update={"armature": [
        Armature(quantite=9, diametre="25M", longueur_mm=3429)] * 2})
    assert compare(dataset([plan]), dataset([da])) == []
    conflict = plan.model_copy(update={"armature": [Armature(quantite=9, diametre="25M", longueur_mm=3429)] * 2})
    other = plan.model_copy(update={"armature": [Armature(quantite=9, diametre="25M", longueur_mm=3000)] * 2})
    row, = compare(dataset([conflict]), dataset([other]))
    assert row['status'] == "changed"


