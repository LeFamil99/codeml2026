"""Repeated observations collapse; separate elements and conflicting reads survive."""
from dataclasses import replace

from l2c.da.parsers.output import deduplicate, reinforcement_key, view_correspondences
from l2c.da.parsers.colonne_clp import Cell, clean_output as column_output
from l2c.da.parsers.dalle_clp import (
    PageResult, Support, View, clean_output as slab_output, records as slab_records,
)
from l2c.da.parsers.semelle_clp import clean_output as footing_output, records as footing_records


def views():
    return [dict(id="main",bbox=(0,0,400,400),letters={"L":100,"K":200,"J":300,"G":350},
                 numbers={"6":100,"5":200,"4":300},warnings=[]),
            dict(id="inset",bbox=(500,0,900,400),letters={"L":100,"K":200,"J":300,"Q":350},
                 numbers={"6":600,"5":700,"4":800},warnings=[])]


def bars(q=7):
    return [dict(role=role,formatted=f"{q}-25M",quantite=q,diametre="25M")
            for role in ("LONG","TRAN")]


def test_shared_dedup_keeps_directions_locations_and_conflicting_values():
    rows=[dict(coordinate="A-6",reinforcement=bars(),confidence=.8),
          dict(coordinate="A-6",reinforcement=bars(),confidence=.9),
          dict(coordinate="A-7",reinforcement=bars(),confidence=.9),
          dict(coordinate="A-6",reinforcement=bars(11),confidence=.9)]
    output,report=deduplicate(rows,lambda r:(r["coordinate"],),
                              lambda r:reinforcement_key(r["reinforcement"]))
    assert len(output)==3 and report["removed_rows"]==1
    assert len(output[0]["reinforcement"])==2 and output[0]["confidence"]==.9
    assert len(report["conflicts"])==1
    assert output[0]["duplicate_conflict"] is True
    assert "conflicting repeated annotations" in output[0]["reason"]


def test_view_alias_requires_observed_axis_alignment():
    main,inset=views()
    mapping=view_correspondences([main,inset])
    assert mapping["inset"]["letters"]["Q"]=="G"
    assert mapping["inset"]["offset_x"]==500
    inset=dict(inset,numbers={"6":600,"5":710,"4":800})
    assert "inset" not in view_correspondences([main,inset])


def test_footing_alias_collapses_to_main_and_keeps_raw_occurrences():
    def row(view,coordinate):
        return dict(coordinate=coordinate,view=view,type="D",reinforcement=bars(),
                    status="read",confidence=.9,anchor=[200,350],
                    marker_bbox=[200,350,212,361],definition_bbox=[900,500,920,510])
    result=dict(fichier="file.pdf",page=1,views=views(),
                footings=[row("main","G-5"),row("inset","Q-5")])
    output=footing_output(result)
    assert len(output)==1 and output[0]["coordinate"]=="G-5"
    assert len(footing_records(result))==1
    assert len(result["footings"])==2 and result["deduplication"]["removed_rows"]==1
    assert result["deduplication"]["duplicates"][0]["suppressed"][0]["coordinate"]=="Q-5"
    # A coordinate-limited inset experiment still keeps its literal source label.
    result["footings"]=result["footings"][1:]
    assert footing_output(result)[0]["coordinate"]=="Q-5"


def test_slab_exports_dedup_but_keep_layers_and_unknown_views_distinct():
    def support(view,layer="intégrité"):
        s=Support(3,view,"J-5",(10,10,20,20),status="read",level="NIVEAU 3",layer=layer)
        s.bars=[dict(label="NUM",quantite=7,diametre="25M",formatted="7-25M",
                     bbox=(20,20,60,30),repere=None,longueur_mm=None,espacement_mm=None)]
        return s
    result=PageResult("file.pdf",3,[View(**v) for v in views()],
                       [support("main"),support("inset"),support("main","bas")],[],0)
    assert len(slab_output([result]))==2 and len(slab_records(result,"file.pdf"))==2
    assert result.deduplication["removed_rows"]==1
    result.views=[]
    assert len(slab_output([result]))==3


def test_column_repeats_across_pages_collapse_per_storey_and_conflicts_survive():
    c=Cell(1,"A-6","NIVEAU 2",0,0,10,10,"text",summary="7-25M",bars=bars(),confidence=.8)
    output,report=column_output([c,replace(c,page=2,confidence=.9),
        replace(c,level="NIVEAU 3"),replace(c,summary="11-25M",bars=bars(11)),
        replace(c,coordinate=None),replace(c,summary=None)],"file.pdf")
    assert len(output)==3 and report["removed_rows"]==1
    assert output[0]["page"]==2 and len(report["conflicts"])==1
    assert all(v is not None for row in output for v in row.values())


def test_column_cli_writes_unique_results_and_all_source_cells(tmp_path,monkeypatch):
    import json
    from l2c.da.parsers import colonne_clp as parser
    cell=Cell(1,"A-6","NIVEAU 2",0,0,10,10,"text",summary="7-25M",bars=bars(),confidence=.9)
    monkeypatch.setattr(parser,"parse",lambda *a,**k:[cell,replace(cell,page=2)])
    output=tmp_path/"final.json"
    diagnostics=tmp_path/"diagnostics.json"
    assert parser.main(["file.pdf","--json",str(output),"--diagnostics",str(diagnostics),"--quiet"])==0
    assert len(json.loads(output.read_text()))==1
    evidence=json.loads(diagnostics.read_text())
    assert len(evidence["cells"])==2 and evidence["deduplication"]["removed_rows"]==1
