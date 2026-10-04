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


def test_grid_close_labels_pair_without_rewriting_either_label():
    rows = compare(dataset([record("G-8")]), dataset([record("G.5-8")]))
    assert len(rows) == 1
    row = rows[0]
    assert row['status'] == "same"
    assert row['coordinate_match'] == "proche"
    assert row['plan_element'] == "G-8" and row['atelier_element'] == "G.5-8"
    assert "G-8" in row['reason'] and "G.5-8" in row['reason']


def test_decimal_labels_pair_when_within_tolerance():
    rows = compare(dataset([record("E-12.7", kind="radier")]),
                   dataset([record("E-13", kind="radier")]))
    assert len(rows) == 1 and rows[0]['coordinate_match'] == "proche"


def test_distant_labels_stay_unmatched():
    rows = {r['element']: r for r in compare(dataset([record("G-8")]), dataset([record("G-10")]))}
    assert rows['G-8']['status'] == "missing_da"
    assert rows['G-10']['status'] == "missing_plan"
    assert all(r['coordinate_match'] is None for r in rows.values())


def test_exact_label_wins_over_a_nearby_label():
    rows = compare(dataset([record("G-8")]), dataset([record("G-8"), record("G.5-8")]))
    by_label = {r['atelier_element']: r for r in rows}
    assert by_label['G-8']['coordinate_match'] == "exact"
    assert by_label['G.5-8']['status'] == "missing_plan"
    assert by_label['G.5-8']['coordinate_match'] is None


def test_nearby_labels_on_different_levels_do_not_pair():
    rows = compare(dataset([record("G-8", level="NIVEAU 2")]),
                   dataset([record("G.5-8", level="NIVEAU 3")]))
    assert all(r['coordinate_match'] is None for r in rows)
    assert {r['status'] for r in rows} <= {"missing_da", "missing_plan", "out_of_scope"}
