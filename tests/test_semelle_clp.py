"""Type schedules, geometry, clean JSON and a real blind footing read."""
import json
import os

import pymupdf
import cv2
import numpy as np

from l2c.da.imageread import TextLine
from l2c.da.parsers import semelle_clp as parser
from l2c.da.parsers.dalle_clp import View


def line(x,y,text):
    return TextLine(x-9,y-3,x+9,y+3,text,.99)


def test_schedule_preserves_unequal_directions_and_rejects_conflicts():
    lines=[line(100,10,"LONGITUDINALE"),line(150,10,"TRANSVERSALE"),
           line(10,30,"TYPE F"),line(100,30,"8-25M"),line(150,30,"10-25M"),
           line(10,50,"TYPE D"),line(100,50,"7-25M"),line(150,50,"7-25M")]
    catalog=parser.parse_schedule(lines)
    assert [b["formatted"] for b in catalog["F"]["reinforcement"]]==["8-25M","10-25M"]
    assert catalog["D"]["status"]=="read"
    catalog=parser.parse_schedule(lines+[line(100,50,"9-25M")])
    assert catalog["D"]["status"]=="partial"
    assert [b["role"] for b in catalog["D"]["reinforcement"]]==["TRAN"]


def test_missing_direction_is_never_copied_and_invalid_bars_are_excluded():
    catalog=parser.parse_schedule([line(100,10,"LONGITUDINALE"),line(150,10,"TRANSVERSALE"),
        line(10,30,"TYPE D"),line(100,30,"7-25M"),line(150,30,"7-25H")])
    assert len(catalog["D"]["reinforcement"])==1
    assert catalog["D"]["issues"]==["TRAN unread"]


def test_footing_square_can_anchor_a_displaced_column_and_inset_stays_separate():
    main=View("main",(0,0,200,200),{"A":100},{"6":100,"5":150})
    inset=View("inset",(300,0,500,200),{"A":100},{"6":400,"5":450})
    marker=pymupdf.Rect(120,120,132,132)
    square=pymupdf.Rect(60,60,140,140)
    result=parser.locate_marker(marker,[main,inset],[],[square])
    assert result["coordinate"]=="A-6" and result["view"]=="main"
    assert result["detection"]=="footing_square"
    assert "coordinate" not in parser.locate_marker(marker,[main,inset],[])


def test_final_json_omits_unlocated_empty_and_unread_rows_and_keeps_false_checks():
    bar=dict(role="LONG",formatted="7-25M",quantite=7,diametre="25M")
    row=dict(coordinate="A-6",view="main",type="D",status="partial",confidence=0.,
             type_check=False,reinforcement=[bar],reason=None)
    result=dict(fichier="test.pdf",page=3,footings=[row,
        dict(row,coordinate=None),dict(row,reinforcement=[]),dict(row,status="unread")])
    output=parser.clean_output(result)
    assert len(output)==1 and output[0]["summary"]=="7-25M"
    assert output[0]["type_check"] is False and output[0]["confidence"]==0.
    assert "reason" not in output[0] and "null" not in json.dumps(output)


def test_long_pedestal_segments_anchor_each_crossed_row():
    gray=np.full((400,400),255,dtype=np.uint8)
    cv2.rectangle(gray,(194,70),(206,188),196,-1)
    cv2.rectangle(gray,(194,212),(206,330),196,-1)
    view=View("inset",(0,0,200,200),{"L":50,"J":100,"I":150},{"5":100})
    columns=parser.column_symbols(gray,2,[view])
    assert any(abs(parser.center(r)[0]-100)<1 and abs(parser.center(r)[1]-100)<1 for r in columns)
    marker=pymupdf.Rect(120,120,132,132)
    assert parser.locate_marker(marker,[view],columns)["coordinate"]=="J-5"


def test_partly_erased_hexagon_is_detected_without_dense_hatching():
    gray=np.full((180,180),221,dtype=np.uint8)
    points=np.array([[53,41],[80,41],[93,64],[80,86],[53,86],[40,64]])
    cv2.polylines(gray,[points],True,12,3)
    cv2.rectangle(gray,(86,48),(100,80),255,-1)
    cv2.putText(gray,"E",(58,74),cv2.FONT_HERSHEY_SIMPLEX,.6,12,2)
    found=parser.hexagons(gray,4)
    assert any(r.contains(pymupdf.Point(66/4,64/4)) for r in found)


def test_cli_reads_and_annotates_only_last_page(tmp_path,monkeypatch):
    source=tmp_path/"input.pdf"
    with pymupdf.open() as doc:
        doc.new_page();doc.new_page();doc.save(source)
    called=[]
    def fake(page,filename,*args):
        called.append(page.number+1)
        return dict(fichier=filename,page=page.number+1,footings=[],catalog={},seconds=0)
    monkeypatch.setattr(parser,"parse_page",fake)
    final=tmp_path/"final.json"
    review=tmp_path/"review.pdf"
    assert parser.main([str(source),"--output-json",str(final),"--diagnostics",str(tmp_path/"diag.json"),
        "--json",str(tmp_path/"records.json"),"--annotated",str(review)])==0
    assert called==[2] and json.loads(final.read_text())==[]
    with pymupdf.open(review) as doc:
        assert len(doc)==1


def test_real_last_page_is_blind_and_resolves_type_d(corpus,monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError("normal DA extraction must use pixels only")
    for name in ("get_text","get_drawings","get_texttrace"):
        monkeypatch.setattr(pymupdf.Page,name,forbidden)
    path=os.path.join(corpus,"CLP/DA/Fondations/CLP_SEMELLES FND.pdf")
    with pymupdf.open(path) as doc:
        result=parser.parse_page(doc[-1],os.path.basename(path))
    output=parser.clean_output(result)
    row=next(r for r in output if r["coordinate"]=="M-14.4")
    assert row["coordinate"]=="M-14.4" and row["type"]=="D"
    assert row["summary"]=="7-25M · 7-25M"
    assert [b["role"] for b in row["reinforcement"]]==["LONG","TRAN"]
    assert len(result["views"])==2 and "G.5" in result["views"][0]["letters"]
    assert result["views"][0]["numbers"]["12.7"]>700
    assert next(r for r in output if r["coordinate"]=="I-12")["type"]=="B"
    assert next(r for r in output if r["coordinate"]=="J-5" and r["view"]=="view-1")["type"]=="A"
    assert len(output)==80 and result["deduplication"]["removed_rows"]==5
    assert next(r for r in output if r["coordinate"]=="L-13")["summary"]=="11-25M · 11-25M"
    assert next(r for r in output if r["type"]=="E")["summary"]=="6-20M · 6-20M"
    record=next(r for r in parser.records(result) if r.element=="M-14.4")
    assert record.type_element=="semelle" and record.element=="M-14.4"
    assert record.debug.footing_type=="D"
