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
    assert rows['L-13']['status'] == "changed"
    assert [b['quantite'] for b in rows['L-13']['plan']] == [9, 9]
    assert [b['quantite'] for b in rows['L-13']['atelier']] == [11, 11]
    assert rows['A-1']['status'] == "same"
    assert rows['B-2']['status'] == "missing_da"
    assert rows['C-3']['status'] == "missing_plan"
    assert rows['L-13']['plan_sources'][0]['fichier'] == "plan.pdf"


def test_slabs_compare_only_same_level_layer_and_keep_both_directions():
    integral = record(kind="dalle", level="REZ-DE-CHAUSSÉE", layer="intégrité", roles=["NUM", "ALP"])
    ordinary = integral.model_copy(update={"debug": Debug(niveau="RDC", reinforcement_kind="slab")})
    upstairs = integral.model_copy(update={"debug": Debug(niveau="NIVEAU 2", layer="intégrité")})
    da = integral.model_copy(update={"debug": Debug(niveau="NIV RDC", layer="integrite", roles=["ALP", "NUM"])})
    rows = compare(dataset([integral, ordinary, upstairs]), dataset([da]))
    assert [r['status'] for r in rows].count("out_of_scope") == 2
    shared = next(r for r in rows if r['status'] != "out_of_scope")
    assert shared['status'] == "same" and len(shared['plan']) == 2


def test_unknown_values_conflicts_and_beam_spatial_matching_require_review():
    for changed in (
        record(quantity=None),
        record(duplicate_conflict=True),
        record(kind="poutre", element="P100", level=None, roles=["longitudinale"] * 2),
    ):
        assert compare(dataset([changed]), dataset([changed]))[0]['status'] == "review"
    known = record()
    unknown = known.model_copy(update={"armature": [Armature(diametre="25M")] * 2})
    assert compare(dataset([known]), dataset([unknown]))[0]['status'] == "review"


def test_repeated_observation_is_removed_but_independent_annotations_stay():
    one = record()
    rows = compare(dataset([one, one.model_copy()]), dataset([one]))
    assert rows[0]['status'] == "same" and len(rows[0]['plan']) == 2
    another = one.model_copy(update={"x": 20})
    rows = compare(dataset([one, another]), dataset([one]))
    assert rows[0]['status'] == "changed" and len(rows[0]['plan']) == 4


def test_comparison_ui_uses_loaded_results_without_submitting_another_job(
        tmp_path, monkeypatch, connected_parsers, background_jobs):
    from pathlib import Path
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    import l2c.pipeline
    from test_da_dashboard import sources
    from conftest import wait_for_da
    project = sources(tmp_path)
    (project / "L2C_PLAN_STR_CLP.pdf").touch()
    monkeypatch.setattr(l2c.pipeline, "run_plan", lambda *a, **k: dataset([record()]))
    st.cache_data.clear()
    try:
        at = AppTest.from_file(str(Path(__file__).parents[1] / "app/streamlit_app.py"), default_timeout=20).run()
        at.sidebar.text_input[0].set_value(str(project)).run()
        at.segmented_control[0].set_value("Comparaison").run()
        assert not at.exception and not connected_parsers
        assert "terminée" in at.info[-1].value
        at.segmented_control[0].set_value("Dessins d'atelier").run()
        wait_for_da(at)
        at.segmented_control[0].set_value("Comparaison").run()
        assert not at.exception and len(connected_parsers) == 4
        metrics = {m.label: m.value for m in at.metric}
        assert metrics['Armatures différentes'] == '1'
        assert 'L-13' in list(at.dataframe[0].value['Élément'])
        at.run()
        assert not at.exception and len(connected_parsers) == 4
    finally:
        st.cache_data.clear()
