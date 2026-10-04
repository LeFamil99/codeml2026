"""Beam positions, shared stirrup totals, blind parsing and final JSON contract."""
import json
from pathlib import Path

import cv2
import numpy as np
import pymupdf
import pytest

from l2c.da.imageread import TextLine
from l2c.da.parsers import poutre_clp as reader
from l2c.da.parsers.dalle_clp import Bubble
from l2c.parse import beams
from l2c.page import open_document,prepare
from conftest import plan_path


def line(x,y,text,width=60):
    return TextLine(x,y,x+width,y+6,text,.99)


def test_bar_roles_spacing_and_fragmented_annotations():
    skin=reader.bar_entry(line(0,0,'3 15M 22-09.@8"C.F.'))
    assert skin['role']=='peau' and skin['espacement_mm']==203.2
    assert reader.bar_entry(line(0,0,'2,20M 10-01'))['quantite']==2
    assert reader.bar_entry(line(0,0,'2 25M13-00'))['quantite']==2
    fragments=reader.bar_fragments(line(0,0,'24.5 16 10M 10TT24X35 @18"'))
    assert len(fragments)==1 and fragments[0][1]['quantite']==16
    fragments=reader.bar_fragments(line(0,0,'4 10M 10TT24X33 @18" 5 10M 10TT24X35 @18"'))
    assert [b['quantite'] for _,b in fragments]==[4,5]
    assert all(b['role']=='étriers' for _,b in fragments)
    assert reader.bar_entry(line(0,0,'3 15M 15T21X35 ADD.'))['role']=='étriers'
    assert reader.bar_entry(line(0,0,'25M 25L7-06 F.I.')) is None
    bad=reader.bar_entry(line(0,0,'3 10M 10T13X38 @1861'))
    assert 'espacement_mm' not in bad and bad['reason']


def test_elevation_labels_are_not_beam_or_zone_counts():
    lines=[line(100,250,'P100 - 24" x 38 1/2"'),line(90,140,'2 25M 11-00'),
           line(130,175,'2 20M 8-09'),line(110,215,'4')]
    bubbles=[Bubble(x,90,20,label,.99) for x,label in [(50,'17'),(130,'16'),(210,'15')]]
    rows,diag=reader.assemble(lines,bubbles,'sample.pdf',2,500,400)
    assert len(rows)==1
    assert rows[0]['position']=='17 → 16 → 15'
    assert len(rows[0]['reinforcement'])==2
    assert diag['bar_lines']==2
    assert not any(v is None for v in rows[0].values())


def test_group_total_is_replaced_by_observed_zone_quantities():
    gray=np.full((800,1100),255,np.uint8)
    # Two actual tiny square symbols, drawn in PDF-point coordinates at zoom 2.
    for x,y in [(110,210),(260,210)]:
        cv2.rectangle(gray,(2*x-5,2*y-5),(2*x+5,2*y+5),0,1)
    definition=reader.bar_entry(line(420,199,'29 10M 10TT21X35 @18"'))
    rows=[dict(element='P101',view='row1',y=250,section='24" x 38 1/2"',region=[0,100,200,250],
               reinforcement=[]),
          dict(element='P102',view='row1',y=250,section='24" x 38 1/2"',region=[200,100,550,250],
               reinforcement=[definition])]
    lines=[line(114,199,'6',4),line(264,199,'5',4)]
    result=reader.stirrup_zones(gray,lines,rows)
    assert len(result['zones'])==2
    assert [r['reinforcement'][0]['quantite'] for r in rows]==[6,5]
    assert all(b['quantite']!=29 for r in rows for b in r['reinforcement'])
    assert result['definitions'][0]['bar']['quantite']==29
    assert all(r['reinforcement'][0]['espacement_mm']==457.2 for r in rows)


def test_original_clp_restores_right_edge_beam_and_local_positions(corpus):
    with open_document(plan_path('CLP')) as doc:
        page=prepare(doc,12,'L2C_PLAN_STR_CLP.pdf')
        records,diag=beams.extract(page,'imperial')
    assert diag['beams']==27
    assert {r.element for r in records}=={t[0] for t in beams._titles(page)}
    assert len(records)==27 and sum(len(r.armature) for r in records)==187
    assert any(r.element=='P112' and any(b.espacement_mm==355.6 for b in r.armature) for r in records)
    assert [a['label'] for a in diag['beam_positions']['P100']]==['17','16','15']
    assert [a['label'] for a in diag['beam_positions']['P103']]==['L','K']
    assert [a['label'] for a in diag['beam_positions']['P120']]==['7','6.5','6']


def test_grouped_plan_review_keeps_each_bar_role_and_spatial_evidence(corpus):
    rows,records=reader.plan_output(plan_path('CLP'))
    assert len(rows)==len(records)==27
    assert sum(len(r['reinforcement']) for r in rows)==187
    assert sum(len(r.armature) for r in records)==187
    p100=next(row for row in rows if row['element']=='P100')
    assert {'peau','longitudinale','étriers'} <= {b['role'] for b in p100['reinforcement']}
    assert len({(b['x'],b['y']) for b in p100['reinforcement']})>1
    assert all(b['raw'] for b in p100['reinforcement'])


def test_normal_page_reader_cannot_use_hidden_text(monkeypatch):
    doc=pymupdf.open();page=doc.new_page(width=500,height=400)
    page.draw_rect(pymupdf.Rect(70,140,180,210))
    def forbidden(*args,**kwargs):raise AssertionError('hidden PDF read in normal extraction')
    monkeypatch.setattr(pymupdf.Page,'get_text',forbidden)
    monkeypatch.setattr(pymupdf.Page,'get_drawings',forbidden)
    monkeypatch.setattr(pymupdf.Page,'get_texttrace',forbidden)
    monkeypatch.setattr(reader,'read_page',lambda src:[line(100,250,'P100 - 24" x 38 1/2"'),
       line(90,140,'2 25M 11-00'),line(130,175,'2 20M 8-09')])
    monkeypatch.setattr(reader,'refine_bar_lines',lambda src,lines,regions=None:lines)
    monkeypatch.setattr(reader,'elevation_axes',lambda src,gray:[Bubble(x,90,20,label,.99)
       for x,label in [(50,'17'),(130,'16'),(210,'15')]])
    rows,diag=reader.parse_page(page,'sample.pdf')
    assert len(rows)==1 and diag['page']==1
    doc.close()


def test_cli_only_reads_last_page_and_writes_json(tmp_path,monkeypatch):
    source=tmp_path/'source.pdf';doc=pymupdf.open();doc.new_page();doc.new_page();doc.save(source);doc.close()
    seen=[]
    def parse(page,filename):
        seen.append(page.number)
        return [],dict(seconds=0,page=page.number+1,fichier=filename)
    monkeypatch.setattr(reader,'parse_page',parse)
    output=tmp_path/'final.json';diag=tmp_path/'diagnostics.json';canonical=tmp_path/'records.json'
    assert reader.main([str(source),'--output-json',str(output),'--diagnostics',str(diag),'--json',str(canonical)])==0
    assert seen==[1] and json.loads(output.read_text())==[]
    assert json.loads(diag.read_text())['page']==2


def test_comparison_keeps_missing_beam_flagged():
    comparison=reader.compare([dict(element='P200')],[])
    assert comparison['findings'][0]['status']=='missing_da_beam'
    assert comparison['findings'][0]['requires_human_review']


def test_shifted_value_does_not_clear_a_spatial_difference_or_extra_steel():
    axes=[dict(label='17',x=0),dict(label='15',x=100)]
    original=[dict(element='P100',axes=axes,position='17 → 15',x=50,y=200,section='24" x 38"',
        reinforcement=[dict(quantite=2,diametre='20M',role='longitudinale',raw='2-20M',x=10,y=100)])]
    atelier=[dict(element='P100',axes=axes,position='17 → 15',x=50,y=200,view='elevation-1',
        section='24" x 38"',reinforcement=[
          dict(quantite=2,diametre='25M',role='longitudinale',raw='2 25M 11-00',bbox=[7,97,11,103]),
          dict(quantite=2,diametre='20M',role='longitudinale',raw='2 20M 11-00',bbox=[93,97,97,103])])]
    result=reader.compare(original,atelier)
    finding=result['findings'][0]
    assert finding['status']=='reinforcement_review' and finding['requires_human_review']
    assert finding['checks'][0]['matched'] is False
    assert finding['checks'][0]['same_beam_value_elsewhere']==['2 20M 11-00']
    assert len(result['unmatched_da_annotations'])==2
    assert all(a['requires_human_review'] for a in result['unmatched_da_annotations'])
