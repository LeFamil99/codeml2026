import pickle

from l2c import io_json
from l2c.comparison import compare
from l2c.model import Armature,Debug,ElementRecord
from l2c.pipeline import ProjectResult
from l2c.record_formats import FORMAT_VERSION,align_records,align_result


def record(kind,source="plan",roles=None,bars=None,**extra):
    return ElementRecord(id=source,source=source,fichier=source+".pdf",feuillet="sheet",page=1,
        x=10,y=20,type_element=kind,element="P100" if kind=="poutre" else "L-13",
        armature=bars or [Armature(quantite=7,diametre="25M")]*2,
        debug=Debug(roles=roles or [],**extra))


def dataset(records):
    return ProjectResult("CLP","source.pdf","imperial",{},records,[],0.)


def test_footings_use_matching_long_trans_order_and_foundation_level():
    plan=record("semelle",niveau="FONDATIONS",raw=["ARM. LONG.: 7-25M","ARM. TRANS.: 9-25M"],
                bars=[Armature(quantite=7,diametre="25M"),Armature(quantite=9,diametre="25M")])
    da=record("semelle","atelier",roles=["TRANS","LONG"],niveau="FONDATION",
              bars=[Armature(quantite=9,diametre="25M"),Armature(quantite=7,diametre="25M")])
    a,b=align_records([plan,da])
    assert a.armature==b.armature and a.debug.roles==b.debug.roles==["LONG","TRAN"]
    assert a.debug.niveau==b.debug.niveau=="FONDATION"
    assert compare(dataset([plan]),dataset([da])) == []


def test_single_transverse_footing_bar_is_not_mislabeled_longitudinal():
    one=record("semelle",raw=["ARM. TRANS.: 7-25M"],bars=[Armature(quantite=7,diametre="25M")])
    aligned=align_records([one])[0]
    assert aligned.debug.roles==["TRAN"] and aligned.debug.missing_roles==["LONG"]


def test_slab_directions_remain_separate_with_the_same_order_and_layer():
    plan=record("dalle",roles=["NUM","ALP"],niveau="REZ-DE-CHAUSSÉE",layer="intégrité",reinforcement_kind="integrity")
    da=record("dalle","atelier",roles=["alp","num"],niveau="RDC",layer="INTEGRITE",
              bars=[Armature(quantite=7,diametre="25M",repere="25J3-00")]*2)
    a,b=align_records([plan,da])
    assert a.armature==b.armature and len(a.armature)==2
    assert a.debug.roles==b.debug.roles==["NUM","ALP"]
    assert a.debug.layer==b.debug.layer=="INTEGRITE"
    assert b.debug.reinforcement_details[0]["repere"]=="25J3-00"
    assert compare(dataset([plan]),dataset([da])) == []


def test_unknown_slab_layer_and_text_direction_are_not_guessed():
    one=record("dalle",direction="horizontal",reinforcement_kind="slab")
    aligned=align_records([one])[0]
    assert aligned.debug.layer=="INCONNU"
    assert aligned.debug.roles==["INCONNU","INCONNU"]
    assert aligned.debug.direction=="horizontal" and aligned.debug.unresolved_roles


def test_beam_spacing_requirements_ignore_fabrication_piece_counts_and_keep_zones():
    plan=record("poutre",roles=["étriers","étriers"],
                bars=[Armature(diametre="10M",espacement_mm=152.4)]*2)
    da=record("poutre","atelier",roles=["ETRI","ETRI"],
              bars=[Armature(quantite=22,diametre="10M",espacement_mm=152.4,repere="10TT16X35")]*2)
    a,b=align_records([plan,da])
    assert a.armature==b.armature and len(a.armature)==2
    assert b.debug.reinforcement_details[0]["quantite"]==22
    assert compare(dataset([plan]),dataset([da])) == []


def test_explicit_length_difference_survives_shared_formatting():
    for kind,roles in [("semelle",["LONG","TRAN"]),("dalle",["NUM","ALP"]),
                       ("poutre",["longitudinale","longitudinale"])]:
        plan=record(kind,roles=roles,niveau="NIVEAU 2",layer="intégrité",
                    bars=[Armature(quantite=7,diametre="25M",longueur_mm=3000)]*2,
                    **({"reinforcement_kind":"integrity"} if kind=="dalle" else {}))
        da=record(kind,"atelier",roles=roles,niveau="NIVEAU 2",layer="intégrité",
                  bars=[Armature(quantite=7,diametre="25M",longueur_mm=3500)]*2)
        assert compare(dataset([plan]),dataset([da]))[0]["status"]=="changed"


def test_legacy_cache_and_json_upgrade_are_idempotent_without_source_files(tmp_path):
    records=[record("semelle",niveau="FONDATIONS"),
             record("dalle",roles=["NUM","ALP"],layer="intégrité")]
    result=dataset(records)
    assert align_result(result) and not align_result(result)
    path=tmp_path/"cache.pkl";path.write_bytes(pickle.dumps(result))
    restored=pickle.loads(path.read_bytes())
    assert not align_result(restored)
    assert all(r.debug.spec_format==FORMAT_VERSION for r in restored.records)
    payload=io_json.dump_records(records)
    assert all(set(r)==io_json.APPENDIX_A_FIELDS for r in payload)
    fields={"repere","diametre","quantite","espacement_mm","longueur_mm"}
    assert all(set(b)==fields for r in payload for b in r["armature"])


def test_completed_job_refreshes_an_atomic_saved_format_upgrade(tmp_path):
    from l2c.da.jobs import Job,ProcessReference,write_json,write_pickle
    path=tmp_path/"result.pkl"
    write_json(tmp_path/"state.json",{"status":"completed"})
    initial=dataset([record("semelle",niveau="FONDATIONS")])
    write_pickle(path,initial)
    job=Job(tmp_path,ProcessReference(0,tmp_path))
    first=job.result()
    updated=dataset([record("semelle",niveau="FONDATIONS",
                           bars=[Armature(quantite=11,diametre="25M")]*2)])
    write_pickle(path,updated)
    second=job.result()
    assert second is not first and second.records[0].armature[0].quantite==11
    assert job.result() is second


def test_radier_primary_specs_match_plan_spacing_and_keep_fabrication_evidence():
    plan = record("radier", bars=[Armature(diametre="30M", espacement_mm=279.4)],
                  direction="vertical", layer="2")
    da = record("radier", "atelier", direction="VERTICALE", layer="RANG 2",
                bars=[Armature(diametre="30M", espacement_mm=279.4, quantite=24,
                               repere="30RU19-09")])
    a, b = align_records([plan, da])
    assert a.armature == b.armature
    assert a.debug.roles == b.debug.roles == ["vertical"]
    assert a.debug.niveau == b.debug.niveau == "FONDATION"
    assert a.debug.layer == b.debug.layer == "2"
    assert b.debug.reinforcement_details[0]["quantite"] == 24
    assert compare(dataset([plan]), dataset([da])) == []
    assert not align_result(dataset([a, b]))


def test_radier_direction_layer_and_actual_spacing_differences_remain_visible():
    plan = record("radier", direction="horizontal", layer="2",
                  bars=[Armature(diametre="30M", espacement_mm=279.4)])
    da = record("radier", "atelier", direction="horizontal", layer="2",
                bars=[Armature(diametre="30M", espacement_mm=203.2)])
    assert compare(dataset([plan]), dataset([da]))[0]["status"] == "changed"
    da.debug.direction = "vertical"
    assert compare(dataset([plan]), dataset([da])) == []  # no counterpart in the same direction
    counted = align_records([record("radier", bars=[Armature(quantite=22, diametre="35M")],
                                    direction="horizontal", layer="1")])[0]
    assert counted.armature[0].quantite == 22
