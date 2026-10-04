"""Column schedule coordinates, floor boundaries and checkpoint recovery."""
from dataclasses import replace

import pymupdf

from l2c.da.parsers import colonne_clp as parser


def test_narrow_capital_i_is_recovered_only_in_coordinate_tokens():
    assert parser._coordinate(["1-13"]) == "I-13"
    assert parser._coordinate(["l-2"]) == "I-2"
    assert parser._coordinate(["G.5-11.5"]) == "G.5-11.5"
    assert parser._coordinate(["VERT: 4 25M 25Z13-05"]) is None
    assert parser._coordinate(['L-10 12"X24"',"1-10"]) == "I-10"


def test_coordinate_retry_uses_larger_glyphs_to_separate_l_and_i(monkeypatch):
    monkeypatch.setattr(parser,"read_cell",lambda *a:("text",["1-8.2"],.95))
    box=pymupdf.Rect(100,200,130,206)
    monkeypatch.setattr(parser,"ocr_region",lambda *a,**k:[(box,"1-8.2",.95)])
    def recognize(page,rect,**options):
        assert options["rec_px"]==96
        return "L-8.2",.99
    monkeypatch.setattr(parser,"recognise",recognize)
    coordinate,_,_=parser.read_coordinate(None,pymupdf.Rect(90,190,150,240))
    assert coordinate=="L-8.2"


def labels():
    def item(y, text):
        return pymupdf.Rect(10, y-6, 80, y), text, .99
    return [item(100,"TOIT"), item(180,"NIVEAU 5-TOIT"),
            item(190,"NIVEAU 5"),item(300,"NIVEAU 4"),
            item(400,"NIVEAU 3"),item(500,"NIVEAU 2"),
            item(600,"REZ-DE-CHAUSSÉE"),item(640,"TRÉFONDS"),
            item(800,"SOUS-SOL"),item(820,"RADIER"),item(900,"EMPATTEMENT")]


def test_floor_names_define_rows_and_reference_levels_do_not_split_basement(monkeypatch):
    monkeypatch.setattr(parser,"ocr_region",lambda *a,**k:labels())
    table=parser.Table([100,200],[0,100,190,300,400,500,600,640,820,920,1000])
    ys,names,_,inferred=parser.schedule_layout(None,table)
    assert ys==[0,100,190,300,400,500,600,800,1000]
    assert names==["TOIT","NIVEAU 5","NIVEAU 4","NIVEAU 3", "NIVEAU 2",
                   "REZ-DE-CHAUSSÉE","SOUS-SOL"]
    assert not any(inferred)


def test_cached_header_recovers_coordinate_without_repeating_data_ocr(monkeypatch):
    table=parser.Table([100,200],[0,100,200,300])
    monkeypatch.setattr(parser,"find_tables",lambda page:[table])
    monkeypatch.setattr(parser,"schedule_layout",lambda *a:([0,100,200,300],
                         ["TOIT","NIVEAU 2"],[None,None],[False,False]))
    cell=parser.Cell(1,None,"NIVEAU 2",100,100,200,200,"text",
                     lines=["VERT: 4 25M",'ETRI: 10M @6"'],
                     bars=[dict(label="VERT",quantite=4,diametre="25M")],summary="4-25M")
    header=replace(cell,y0=0,y1=100,lines=["1-13"],bars=[],summary=None)
    monkeypatch.setattr(parser,"read_cell",lambda *a,**k:(_ for _ in ()).throw(AssertionError("Unexpected OCR")))
    result=parser.repair_cached_page(None,[header,cell])
    assert result[1].coordinate=="I-13" and result[1].bars==cell.bars


def test_boundary_upgrade_rereads_only_changed_basement_cell(monkeypatch):
    table=parser.Table([100,200],[0,100,300,400])
    monkeypatch.setattr(parser,"find_tables",lambda page:[table])
    monkeypatch.setattr(parser,"schedule_layout",lambda *a:([0,100,250,400],
                         ["NIVEAU 2","SOUS-SOL"],[None,None],[False,False]))
    unchanged=parser.Cell(1,"A-6","NIVEAU 2",100,0,200,100,"text",summary="4-25M")
    basement=replace(unchanged,level="EMPATTEMENT",y0=100,y1=300)
    calls=[]
    def read(page,rect,*args):
        calls.append(tuple(rect))
        return "text",["VERT: 4 25M"],.99,[],"4-25M",None,False
    monkeypatch.setattr(parser,"read_data_cell",read)
    page=type("Page",(),{"number":0})()
    result=parser.repair_cached_page(page,[unchanged,basement])
    assert calls==[(100.,100.,200.,250.)]
    assert result[0].summary==unchanged.summary and result[1].level=="SOUS-SOL"
