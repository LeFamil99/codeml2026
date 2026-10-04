from l2c.comparison import compare, grid_position
from l2c.model import ElementRecord, Debug, Armature
from l2c.pipeline import ProjectResult


def record(element="G-8", quantity=9, kind="semelle", level="FONDATIONS", **debug):
    return ElementRecord(id="sample", source="plan", fichier="plan.pdf", feuillet="S-100",
                         page=1, x=1, y=2, type_element=kind, element=element,
                         armature=[Armature(quantite=quantity, diametre="25M")] * 2,
                         debug=Debug(niveau=level, **debug))


def dataset(records):
    return ProjectResult("CLP", "source.pdf", "imperial", {}, records, [], 0.)


def test_grid_position_reads_half_and_decimal_grid_labels():
    assert grid_position("G.5-8") == (6.5, 8.0)
    assert grid_position("A-12.7") == (0, 12.7)
    assert grid_position("UNKNOWN") is None


def test_grid_close_labels_pair_and_report_the_changed_bars():
    row, = compare(dataset([record("G-8", quantity=9)]), dataset([record("G.5-8", quantity=11)]))
    assert row['status'] == "changed" and row['coordinate_match'] == "proche"
    assert row['plan_element'] == "G-8" and row['atelier_element'] == "G.5-8"


def test_decimal_labels_pair_when_within_tolerance():
    row, = compare(dataset([record("E-12.7", kind="radier", quantity=9)]),
                   dataset([record("E-13", kind="radier", quantity=11)]))
    assert row['coordinate_match'] == "proche"


def test_labels_five_positions_apart_are_not_paired():
    middle = [record(f"G-{n}") for n in (7, 8, 9, 10)]
    plan = dataset([record("G-6", quantity=9)] + middle)
    da = dataset([record("G-11", quantity=11)] + middle)
    assert compare(plan, da) == []


def test_labels_three_positions_apart_pair_in_stage_three():
    plan = dataset([record("G-7", quantity=9), record("G-8"), record("G-9")])
    da = dataset([record("G-10", quantity=11), record("G-8"), record("G-9")])
    row, = compare(plan, da)
    assert row['plan_element'] == "G-7" and row['atelier_element'] == "G-10"


def test_labels_two_positions_apart_pair_after_one_position_pass():
    plan = dataset([record("G-8", quantity=9), record("G-9")])
    da = dataset([record("G-10", quantity=11), record("G-9")])
    row, = compare(plan, da)
    assert row['atelier_element'] == "G-10"


def test_exact_label_wins_over_a_nearby_label():
    plan = dataset([record("G-8")])
    da = dataset([record("G-8"), record("G.5-8", quantity=11)])
    assert compare(plan, da) == []


def test_nearby_labels_on_different_levels_do_not_pair():
    rows = compare(dataset([record("G-8", level="NIVEAU 2", quantity=9)]),
                   dataset([record("G.5-8", level="NIVEAU 3", quantity=11)]))
    assert rows == []


def test_general_plan_slab_steel_is_not_compared_because_the_da_reads_only_integrity():
    general = record("A-8", kind="dalle", level="NIVEAU 2").model_copy(update={"debug": Debug(
        niveau="NIVEAU 2", layer="INCONNU", reinforcement_kind="slab")})
    integrity = record("A-9", kind="dalle", level="NIVEAU 2", layer="INTEGRITE", roles=["NUM", "ALP"])
    integrity = integrity.model_copy(update={"debug": Debug(niveau="NIVEAU 2", layer="INTEGRITE",
                                                            roles=["NUM", "ALP"],
                                                            reinforcement_kind="integrity")})
    da = dataset([record("A-9", kind="dalle", level="NIVEAU 2", layer="INTEGRITE", roles=["NUM", "ALP"])])
    assert compare(dataset([general, integrity]), da) == []


def test_column_one_grid_position_apart_pairs():
    row, = compare(dataset([record("A-16", kind="colonne", level="NIVEAU 2", quantity=4)]),
                   dataset([record("A-15.8", kind="colonne", level="NIVEAU 2", quantity=6)]))
    assert row['coordinate_match'] == "proche"


def test_labels_less_than_one_unit_apart_on_both_axes_pair():
    row, = compare(dataset([record("H-12.7", kind="colonne", level="NIVEAU 2", quantity=6)]),
                   dataset([record("G.5-12.5", kind="colonne", level="NIVEAU 2", quantity=8)]))
    assert row['coordinate_match'] == "proche"


def test_exchanged_specifications_are_not_hidden_as_identical():
    def column(element, diameter, quantity, spacing):
        return record(element, kind="colonne", level="NIVEAU 4").model_copy(update={"armature": [
            Armature(quantite=quantity, diametre=diameter), Armature(diametre="10M", espacement_mm=spacing)]})
    plan = dataset([column("L-15", "25M", 4, 152.4), column("L-16", "35M", 6, 203.2)])
    da = dataset([column("L-15", "35M", 6, 203.2), column("L-16", "25M", 4, 152.4)])
    rows = {r['element']: r for r in compare(plan, da)}
    assert set(rows) == {"L-15", "L-16"} and all(r['status'] == "changed" for r in rows.values())


def test_integrity_direction_read_on_one_side_is_compared_only_with_the_same_direction():
    def slab(roles, quantity):
        return record("A-6", kind="dalle", level="NIVEAU 2", layer="INTEGRITE", roles=roles,
                      reinforcement_kind="integrity").model_copy(update={"armature": [
                          Armature(quantite=quantity, diametre="15M")] * len(roles)})
    plan = dataset([slab(["NUM", "ALP"], 3)])
    assert compare(plan, dataset([slab(["NUM"], 3)])) == []
    row, = compare(plan, dataset([slab(["NUM", "ALP"], 5)]))
    assert row['status'] == "changed" and [b['role'] for b in row['plan']] == ["NUM", "ALP"]
