from l2c.column_records import COLUMN_FORMAT, align_column_records, align_result
from l2c.comparison import compare
from l2c.model import Armature, Debug, ElementRecord
from l2c.pipeline import ProjectResult


def column(source, bars, roles=None, raw=None):
    return ElementRecord(id=source,source=source,fichier=source+".pdf",feuillet="columns",
        page=1,x=10,y=20,type_element="colonne",element="I-13",armature=bars,
        debug=Debug(niveau="NIVEAU 2",roles=roles or [],raw=raw or []))


def result(records):
    return ProjectResult("CLP","source.pdf","imperial",{},records,[],0.)


def test_identical_primary_specs_share_exact_format_and_keep_fabrication_details():
    plan=column("plan",[Armature(quantite=4,diametre="25M"),
                        Armature(diametre="10M",espacement_mm=152.4)])
    da=column("atelier",[
        Armature(quantite=4,diametre="25M",repere="25Z8-10",longueur_mm=3000),
        Armature(quantite=19,diametre="10M",repere="10ET13X21",espacement_mm=152.4),
        Armature(quantite=1,diametre="10M",repere="10ET12X20"),
        Armature(quantite=2,diametre="25M",repere="25L16X74")],
        ["VERT","ÉTRI","ETRI","GOUJ"])
    rows=compare(result([plan]),result([da]))
    assert rows[0]["status"]=="same"
    assert rows[0]["plan"]==rows[0]["atelier"]
    evidence=rows[0]["atelier_sources"][0]["debug"]
    assert len(evidence["reinforcement_details"])==4
    assert evidence["reinforcement_details"][0]["longueur_mm"]==3000
    assert evidence["record_format"]==COLUMN_FORMAT


def test_parallel_vertical_groups_sum_but_separate_muret_is_excluded():
    split=column("atelier",[Armature(quantite=4,diametre="20M"),
                            Armature(quantite=2,diametre="20M")],["VERT","VERT"])
    assert align_column_records([split])[0].armature==[Armature(quantite=6,diametre="20M")]
    muret=column("atelier",[Armature(quantite=4,diametre="25M"),
                            Armature(quantite=4,diametre="25M")],["VERT","VERT"],
                            ["VERT: 4 25M 25Z7-11","MURET 14","VERT: 4 25M 25Z5-11"])
    aligned=align_column_records([muret])[0]
    assert aligned.armature==[Armature(quantite=4,diametre="25M")]
    assert len(aligned.debug.reinforcement_details)==2


def test_real_quantity_diameter_and_spacing_differences_still_surface():
    baseline=[Armature(quantite=4,diametre="25M"),Armature(diametre="10M",espacement_mm=152.4)]
    for changed in ([Armature(quantite=6,diametre="25M"),baseline[1]],
                    [Armature(quantite=4,diametre="20M"),baseline[1]],
                    [baseline[0],Armature(diametre="10M",espacement_mm=304.8)]):
        row=compare(result([column("plan",baseline)]),
                    result([column("atelier",changed)]))[0]
        assert row["status"]=="changed"


def test_storage_upgrade_is_idempotent_and_does_not_need_source_pdf():
    dataset=result([column("atelier",[Armature(quantite=4,diametre="25M",repere="25Z8-10")])])
    assert align_result(dataset) is True
    assert align_result(dataset) is False
    assert dataset.records[0].armature[0].repere is None
    assert dataset.records[0].debug.reinforcement_details[0]["repere"]=="25Z8-10"
