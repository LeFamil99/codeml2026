"""CLP beam elevations: local image OCR, last page only, independent of the plan.

Elevation titles identify beams; their own circle strip supplies numeric OR letter
positions. Grey supports and tiny stirrup-shape squares are not separate beams.
The optional original-plan comparison runs strictly after blind DA extraction.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import logging
import math
import os
from pathlib import Path
import re
import time

import cv2
import numpy as np
import pymupdf

from l2c.da.common import parse_bar_line
from l2c.da.imageread import PageImage, TextLine, _join
from l2c.da.parsers.colonne_clp import _ocr
from l2c.da.parsers.dalle_clp import Bubble, circle_candidates, read_bubbles, recognise_line, fold
from l2c.da.parsers.output import deduplicate, reinforcement_key
from l2c.model import Armature, Debug, ElementRecord
from l2c.units import parse_spacing

DEFAULT_FILE = os.path.expanduser('~/Downloads/l2c-participants/CLP/DA/Poutres/CLP_POUTRES.pdf')
DEFAULT_PLAN = os.path.expanduser('~/Downloads/l2c-participants/CLP/L2C_PLAN_STR_CLP.pdf')
TITLE = re.compile(r'^P\s*-?\s*(\d{2,4}[A-Z]?)\s*[-:]\s*(.+)$', re.I)
AXIS = re.compile(r'^(?:[A-Z]|\d{1,2}(?:\.\d{1,2})?)$')
log = logging.getLogger('poutre_clp')


def read_page(src: PageImage) -> list[TextLine]:
    """Overlapping detection tiles; join edge fragments before recognition."""
    boxes = []
    for y in range(20, int(src.height*.94), 200):
        for x in range(0, int(src.width*.99), 200):
            clip = pymupdf.Rect(x, y, min(x+250, src.width*.99), min(y+250, src.height*.94))
            img = src.render(clip, 5)
            if (img < 180).mean() < .0004:
                continue
            result = _ocr()(img, use_det=True, use_cls=False, use_rec=False)
            if result.boxes is None:
                continue
            for poly in result.boxes:
                xs, ys = [clip.x0+p[0]/5 for p in poly], [clip.y0+p[1]/5 for p in poly]
                a,b,c,d = map(float, (min(xs),min(ys),max(xs),max(ys)))
                vertical=(d-b)>1.5*(c-a) and not (c-a<8 and d-b<10)
                boxes.append(TextLine(a,b,c,d,'',0,vertical))
        log.info('detected strip y=%d', y)
    boxes = _join(boxes)
    lines = []
    for i, box in enumerate(boxes):
        line = recognise_line(src, box)
        if line.text and line.confidence >= .5:
            lines.append(line)
        if i % 100 == 0:
            log.info('recognised %d/%d text regions', i, len(boxes))
    return sorted(lines, key=lambda l:(l.y0,l.x0))


def elevation_axes(src: PageImage, gray: np.ndarray) -> list[Bubble]:
    bubbles = circle_candidates(gray, 2)
    read_bubbles(src, bubbles)
    # Long decimal labels touch the rim: recover arcs on observed header strips.
    strips = sorted({round(b.y/10)*10 for b in bubbles})
    for y in strips:
        if sum(abs(b.y-y)<12 for b in bubbles) < 3:
            continue
        y0,y1=max(0,round((y-16)*2)),min(gray.shape[0],round((y+16)*2))
        rings=cv2.HoughCircles(gray[y0:y1],cv2.HOUGH_GRADIENT,1,24,
                               param1=100,param2=30,minRadius=17,maxRadius=26)
        if rings is not None:
            for x,cy,r in rings[0]:
                b=Bubble(float(x/2),float((cy+y0)/2),float(r))
                if not any(math.dist((b.x,b.y),(a.x,a.y))<5 for a in bubbles):
                    bubbles.append(b)
    for b in bubbles:
        # Wider numeric crop, tighter letter crop: avoid circle arcs and leaders.
        if b.label is None or not AXIS.fullmatch(b.label):
            widths=(.3,.57)
        else:
            widths=(.57,) if b.label[0].isdigit() else (.3,)
        for width in widths:
            rx=max(15.,b.diameter*width) if width>.4 else b.diameter*width
            ry=max(5.,b.diameter*.24)
            rect=pymupdf.Rect(b.x-rx,b.y-ry,b.x+rx,b.y+ry)
            r=_ocr()(src.render(rect,12),use_det=False,use_cls=False,use_rec=True)
            if not r.txts:
                continue
            text=str(r.txts[0]).strip().strip('() ').upper()
            if AXIS.fullmatch(text) and r.scores[0]>=.6:
                b.label,b.confidence=text,float(r.scores[0])
                if text.isalpha() or (text[0].isdigit() and width>.4):
                    break
    return bubbles


def bar_entry(line: TextLine) -> dict | None:
    text=fold(line.text)
    # Drafting marks may be read as a comma; a lost space after M is harmless
    # when a length/mark follows. Neither repair guesses a quantity or diameter.
    text=re.sub(r'^(\d+)[,](?=\d{2}M\b)',r'\1 ',text)
    text=re.sub(r'(\d{2}M)(?=\d)',r'\1 ',text)
    text=re.sub(r'\.(?=@)','',text)
    text=text.lstrip('[ ')
    parsed=parse_bar_line(text,'imperial')
    if not parsed or not parsed.armature.diametre or not parsed.armature.quantite:
        return None
    a=parsed.armature
    if a.espacement_mm is None and '@' in text:
        a.espacement_mm=parse_spacing(text,'imperial')
    invalid_spacing=a.espacement_mm is not None and not 0<a.espacement_mm<2000
    if invalid_spacing:a.espacement_mm=None
    role='peau' if (a.diametre=='15M' and
                     re.search(r'C\.?\s*(?:F\.?|$)|CH\.?\s*FACE|F\.[EI]\.',text)) else \
         'étriers' if (a.repere and re.match(r'\d{2}(?:TT|T|ET|E)\d',a.repere)) else 'longitudinale'
    data={k:v for k,v in a.model_dump().items() if v is not None}
    data.update(role=role,formatted=f'{a.quantite}-{a.diametre}',raw=line.text,
                confidence=line.confidence,bbox=list(line.rect))
    if invalid_spacing:data['reason']='implausible OCR spacing; local reread required'
    return data


def bar_fragments(line: TextLine) -> list[tuple[TextLine,dict]]:
    """A detector sometimes joins adjacent stirrup zones; parse all labels."""
    starts=list(re.finditer(r'(?<![\dA-Z-])\d+[ . ,]+(?:10|15|20|25|30|35|45|55)\s*M',fold(line.text)))
    if len(starts)<2:
        b=bar_entry(line)
        if b:
            return [(line,b)]
        if not starts:
            return []
    result=[]
    for i,m in enumerate(starts):
        end=starts[i+1].start() if i+1<len(starts) else len(line.text)
        fragment=TextLine(line.x0+(line.x1-line.x0)*m.start()/len(line.text),line.y0,
                          line.x0+(line.x1-line.x0)*end/len(line.text),line.y1,
                          line.text[m.start():end].strip(),line.confidence,line.vertical)
        if b:=bar_entry(fragment):result.append((fragment,b))
    return result


def read_local(src: PageImage, rect: pymupdf.Rect, zoom=8) -> list[TextLine]:
    """Pad tiny horizontal crops before detection to avoid extreme upscaling."""
    img=src.render(rect,zoom)
    side=max(img.shape[:2]);padded=np.full((side,side,3),255,np.uint8)
    padded[:img.shape[0],:img.shape[1]]=img
    result=_ocr()(padded,use_det=True,use_cls=False,use_rec=False)
    boxes=[]
    for poly in result.boxes if result.boxes is not None else []:
        xs=[rect.x0+p[0]/zoom for p in poly];ys=[rect.y0+p[1]/zoom for p in poly]
        b=TextLine(float(min(xs)),float(min(ys)),float(max(xs)),float(max(ys)),'',0,False)
        if b.rect.intersects(rect):boxes.append(b)
    groups=[]
    for b in sorted(boxes,key=lambda b:(b.cy,b.x0)):
        for group in groups:
            a=group[0];overlap=min(a.y1,b.y1)-max(a.y0,b.y0)
            gap=max(b.x0-max(l.x1 for l in group),min(l.x0 for l in group)-b.x1)
            if overlap>.4*min(a.y1-a.y0,b.y1-b.y0) and gap<8:
                group.append(b);break
        else:groups.append([b])
    return [recognise_line(src,TextLine(min(b.x0 for b in g),min(b.y0 for b in g),
            max(b.x1 for b in g),max(b.y1 for b in g),'',0,False)) for g in groups]


def refine_bar_lines(src: PageImage, lines: list[TextLine], regions=None) -> list[TextLine]:
    """Look again where a visible bar size lacks its quantity (e.g. face bars)."""
    recovered=[];superseded=set()
    for line in lines:
        if regions and not any(pymupdf.Rect(r['region']).contains(pymupdf.Point(line.cx,line.cy)) for r in regions):
            continue
        possible_lost_m=re.search(r'\b\d+\s+(?:10|15|20|25|30|35|45|55)\s+\d{1,3}-\d{2}\b',line.text)
        if not re.search(r'(?:\d{2}|\d[SO])\s*M(?![A-Z])',fold(line.text)) and not possible_lost_m:
            continue
        bars=bar_fragments(line)
        incomplete_spacing=bool(bars and any(b['role'] in ('étriers','peau') and 'ADD' not in fold(b['raw'])
                                             and 'espacement_mm' not in b for _,b in bars))
        if bars and not incomplete_spacing:
            continue
        box=(line.rect+(-8,-3,30 if incomplete_spacing else 8,3)) & pymupdf.Rect(0,0,src.width,src.height)
        reread=[l for l in read_local(src,box,8) if bar_fragments(l)]
        # Isolated quantities are tall narrow boxes and the shared detector can
        # rotate them. This band is known to be horizontal: also recognise it as
        # one line, with a tight vertical crop keeping its leader out.
        horizontal=TextLine(box.x0,line.cy-4,box.x1,line.cy+4,'',0,False)
        direct=recognise_line(src,horizontal)
        if bar_fragments(direct):
            reread.append(direct)
        tight=recognise_line(src,TextLine(box.x0,line.cy-2.7,box.x1,line.cy+2.7,'',0,False))
        if bar_fragments(tight):reread.append(tight)
        if incomplete_spacing:
            complete=[l for l in reread if any(b.get('espacement_mm') for _,b in bar_fragments(l))]
            if complete:reread=[max(complete,key=lambda l:l.confidence)]
        recovered.extend(reread)
        if incomplete_spacing and any(b.get('espacement_mm') for l in reread for _,b in bar_fragments(l)):
            superseded.add(id(line))
    log.info('recovered %d local bar labels',len(recovered))
    return [l for l in lines if id(l) not in superseded]+recovered


def section_width(section: str) -> float | None:
    m=re.match(r'\s*(\d+)(?:\s+(\d)/(\d))?',section)
    if not m:
        return None
    return int(m[1])+(int(m[2])/int(m[3]) if m[2] else 0)


def square_centres(gray: np.ndarray) -> list[tuple[float,float]]:
    contours,_=cv2.findContours((gray<130).astype(np.uint8)*255,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    squares=[]
    for c in contours:
        x,y,w,h=cv2.boundingRect(c)
        poly=cv2.approxPolyDP(c,.035*cv2.arcLength(c,True),True)
        if not (3<w/2<9 and 3<h/2<9 and .8<w/h<1.2 and len(poly)==4
                and cv2.contourArea(c)/(w*h)>.65):continue
        cx,cy=(x+w/2)/2,(y+h/2)/2
        if not any(math.dist((cx,cy),a)<2 for a in squares):squares.append((cx,cy))
    return squares


def recover_symbol_labels(src: PageImage, gray: np.ndarray, lines: list[TextLine], rows: list[dict]):
    """Read the horizontal caption bands above observed stirrup symbols again.

    Two agreeing crop reads are required before replacing a fused/partial line.
    This catches bars lost between overlapping tiles without native PDF evidence.
    """
    corrections=[]
    for x,y in square_centres(gray):
        if not any(r['region'][0]<x<r['region'][2] and r['y']-70<y<r['y']-10 for r in rows):continue
        for dy in (8,14):
            first=recognise_line(src,TextLine(x-26,y-dy-2.7,x+42,y-dy+2.7,'',0,False))
            second=recognise_line(src,TextLine(x-24,y-dy-2.7,x+40,y-dy+2.7,'',0,False))
            a,b=bar_entry(first),bar_entry(second)
            if not a or not b or min(first.confidence,second.confidence)<.8:continue
            key=lambda v:(v['quantite'],v['diametre'],v.get('espacement_mm'),v.get('repere'))
            if key(a)!=key(b) or (a['role']=='étriers' and not a.get('espacement_mm')):continue
            if not re.search(r'@\s*\d+(?:\s+\d/\d)?\s*["\']',first.text):continue
            best=max((first,second),key=lambda l:l.confidence)
            old=[l for l in lines if abs(l.cy-best.cy)<2.2 and abs(l.cx-best.cx)<40
                 and re.search(r'\d{2}\s*M',fold(l.text))]
            if old:
                corrections.append(dict(bbox=list(best.rect),read=best.text,previous=[l.text for l in old]))
                lines=[l for l in lines if l not in old]
            lines.append(best)
    return lines,corrections


def stirrup_zones(gray: np.ndarray, lines: list[TextLine], rows: list[dict], src: PageImage | None = None) -> dict:
    """Resolve the small square zone symbols to the shared stirrup definitions.

    A note such as 29 10M 10TT21X35 is a total across P101/P102, not 29
    stirrups in each beam. The squares carry zone quantities. Shape width is
    the observed beam section minus the cover; leaders must share a baseline.
    """
    squares=square_centres(gray)
    quantities=[l for l in lines if re.fullmatch(r'\d{1,2}',l.text.strip()) and l.confidence>=.65]
    definitions=[]
    for row in rows:
        for b in row['reinforcement']:
            m=re.fullmatch(r'10(?:TT|T)(\d+)X(\d+)',b.get('repere',''))
            if m and b.get('espacement_mm'):
                definitions.append(dict(bar=b,element=row['element'],view=row['view'],
                    width=int(m[1]),height=int(m[2]),cy=(b['bbox'][1]+b['bbox'][3])/2))
    zones=[];used=set();unresolved=[];explicit=[]
    for cx,cy in squares:
        owners=[r for r in rows if r['region'][0]<cx<r['region'][2] and
                r['y']-70<cy<r['y']-10]
        if len(owners)!=1:continue
        row=owners[0];width=section_width(row['section'])
        printed=[d for d in definitions if d['view']==row['view'] and
                 abs((d['bar']['bbox'][0]+d['bar']['bbox'][2])/2-cx)<40 and 3<cy-d['cy']<17]
        if printed:
            explicit.append(dict(element=row['element'],x=cx,y=cy,
                                 reason='count/size/spacing already printed above symbol'))
            continue
        section_parts=re.split(r'\s*[xX]\s*',row['section'],maxsplit=1)
        height=section_width(section_parts[1]) if len(section_parts)==2 else None
        nearby=[l for l in quantities if cx-12<l.cx<cx+46 and cy-14<l.cy<cy-3]
        nearby=[l for l in nearby if not any(pymupdf.Rect(d['bar']['bbox']).contains(
            pymupdf.Point(l.cx,l.cy)) for d in definitions)]
        defs=[(i,d) for i,d in enumerate(definitions) if d['view']==row['view'] and
              width is not None and 2.5<=width-d['width']<=4.5 and
              height is not None and 2<=height-d['height']<=5 and 3<cy-d['cy']<17]
        if not nearby and defs and src is not None:
            patch=pymupdf.Rect(cx-14,cy-14,cx+48,cy-3)
            for l in read_local(src,patch,8):
                if not re.fullmatch(r'\d{1,2}',l.text):continue
                upright=recognise_line(src,TextLine(l.x0,l.y0,l.x1,l.y1,'',0,False))
                if re.fullmatch(r'\d{1,2}',upright.text) and upright.confidence>=.65:
                    nearby.append(upright)
        if not nearby or not defs:
            unresolved.append(dict(element=row['element'],x=cx,y=cy,reason='zone count or leader definition unresolved'))
            continue
        quantity=min(nearby,key=lambda l:abs(l.cy-(cy-8))+abs(l.cx-(cx+8))*.2)
        if src is not None:
            upright=recognise_line(src,TextLine(quantity.x0,quantity.y0,quantity.x1,quantity.y1,'',0,False))
            if re.fullmatch(r'\d{1,2}',upright.text) and upright.confidence>=.65:
                quantity=upright
        if int(quantity.text)<=0:
            continue
        i,definition=min(defs,key=lambda item:abs(item[1]['cy']-(cy-8))*10+
                         abs((item[1]['bar']['bbox'][0]+item[1]['bar']['bbox'][2])/2-cx)*.01)
        used.add(i)
        b=dict(definition['bar'])
        b.update(quantite=int(quantity.text),formatted=f'{int(quantity.text)}-10M',
            bbox=[cx-3,cy-3,cx+3,cy+3],raw=quantity.text+' / '+definition['bar']['raw'],
            confidence=min(quantity.confidence,b['confidence']),zone_symbol=[cx,cy],
            quantity_bbox=list(quantity.rect),
            definition_bbox=definition['bar']['bbox'],definition_total=definition['bar']['quantite'])
        row['reinforcement'].append(b)
        zones.append(dict(element=row['element'],**b))
    # Remove a group-total note from individual beam bars when its zone symbols
    # have resolved it. Its total, full text and source box stay in diagnostics.
    for i in used:
        b=definitions[i]['bar']
        for row in rows:
            row['reinforcement']=[a for a in row['reinforcement'] if a is not b]
    for row in rows:
        row['summary']=' · '.join(b['formatted'] for b in row['reinforcement'])
    return dict(zones=zones,definitions=definitions,unresolved=unresolved,explicit_captions=explicit)


def check_source(page: pymupdf.Page, rows: list[dict], diagnostics: dict) -> dict:
    """Post-extraction text oracle. It reports errors; it never repairs output."""
    from l2c.page import prepare
    native=prepare(page.parent,page.number,diagnostics['fichier'])
    lines=[TextLine(l.x0,l.y0,l.x1,l.y1,l.text,1,l.vertical) for l in native.lines]
    reference=[b for l in lines for _,b in bar_fragments(l)]
    checks=[]
    for row in rows:
        for b in row['reinforcement']:
            if b.get('zone_symbol'):
                if 'quantity_bbox' not in b:
                    checks.append(dict(element=row['element'],raw=b['raw'],kind='zone_quantity',
                                       equal=False,oracle=[],reason='quantity box absent from older run'))
                    continue
                box=pymupdf.Rect(b['quantity_bbox'])+(-1,-1,1,1)
                words=[w.text for w in native.words if box.contains(pymupdf.Point(w.cx,w.cy))
                       and re.fullmatch(r'\d{1,2}',w.text)]
                checks.append(dict(element=row['element'],raw=b['raw'],kind='zone_quantity',
                    equal=str(b['quantite']) in words,oracle=words,bbox=list(box)))
            else:
                box=pymupdf.Rect(b['bbox'])+(-4,-3,4,3)
                nearby=[a for a in reference if box.intersects(pymupdf.Rect(a['bbox']))]
                equal=any(a['quantite']==b['quantite'] and a['diametre']==b['diametre']
                    and a.get('espacement_mm')==b.get('espacement_mm') for a in nearby)
                checks.append(dict(element=row['element'],raw=b['raw'],kind='bar_count_size_spacing',
                    equal=equal,oracle=[a['raw'] for a in nearby],bbox=list(box)))
    return dict(checked=len(checks),matched=sum(c['equal'] for c in checks),checks=checks,
                scope='Final counts/sizes/spacings and zone quantities; not complete source coverage or beam/leader truth')


def assemble(lines: list[TextLine], bubbles: list[Bubble], filename: str, page: int,
             width: float, height: float) -> tuple[list[dict],dict]:
    titles=[]
    bars=[pair for l in lines for pair in bar_fragments(l)]
    for line in lines:
        m=TITLE.match(line.text.strip())
        if not m or line.vertical:
            continue
        # Plan-view labels in the upper inset repeat the same marks, but have no
        # horizontal reinforcement annotation band directly over their title.
        near=[l for l,b in bars if not l.vertical and line.cy-165<l.cy<line.cy-10
              and abs(l.cx-line.cx)<240]
        if len(near)<2:
            continue
        titles.append(dict(element='P'+m[1].upper(),section=m[2].strip(),
                           x=line.cx,y=line.cy,title_bbox=list(line.rect)))
    rows=[]
    for title in sorted(titles,key=lambda t:(t['y'],t['x'])):
        for row in rows:
            if abs(title['y']-np.mean([t['y'] for t in row]))<30:
                row.append(title)
                break
        else:
            rows.append([title])
    candidates=[]
    unassigned=[]
    for ri,row in enumerate(rows):
        row.sort(key=lambda t:t['x'])
        cy=float(np.median([t['y'] for t in row]))
        possible=[b for b in bubbles if b.label and AXIS.fullmatch(b.label)
                  and cy-260<b.y<cy-70]
        header=[b for b in possible if sum(abs(b.y-a.y)<12 for a in possible)>=3]
        for i,title in enumerate(row):
            left=(row[i-1]['x']+title['x'])/2 if i else 0
            right=(row[i+1]['x']+title['x'])/2 if i+1<len(row) else width*.98
            title.update(view=f'elevation-{ri+1}',region=[left,cy-170,right,cy])
            # Nearest header circles to the title interval boundaries retain the
            # shared support axis in both neighbouring beams.
            axes=sorted([b for b in header if left-35<b.x<right+35 and
                         abs(b.x-title['x'])<180],key=lambda b:b.x)
            title['axes']=[dict(label=b.label,x=b.x,y=b.y,confidence=b.confidence) for b in axes]
            title['position']=' → '.join(b.label for b in axes)
            title['reinforcement']=[]
            candidates.append(title)
    for line,bar in bars:
        available=[t for t in candidates if t['region'][1]<line.cy<t['region'][3]-8
                   and t['region'][0]<=line.cx<t['region'][2]]
        if len(available)==1:
            available[0]['reinforcement'].append(bar)
        else:
            unassigned.append(dict(text=line.text,bbox=list(line.rect),reason='outside/ambiguous elevation region'))
    populated=[]
    for t in candidates:
        # Duplicate OCR boxes must refer to the same printed label, not just the
        # same quantity in another beam zone. Keep distinct source positions.
        unique=[]
        for b in sorted(t['reinforcement'],key=lambda b:(b['bbox'][1],b['bbox'][0])):
            if any(reinforcement_key([b])==reinforcement_key([a]) and
                   abs(b['bbox'][0]-a['bbox'][0])<3 and abs(b['bbox'][1]-a['bbox'][1])<3
                   for a in unique):
                continue
            unique.append(b)
        t['reinforcement']=unique
        if not unique or not t['axes']:
            continue
        t.update(fichier=filename,page=page,status='partial',
                 summary=' · '.join(b['formatted'] for b in unique),
                 confidence=min(b['confidence'] for b in unique),
                 reason='OCR coverage and zone assignment require review')
        populated.append(t)
    output,dedup=deduplicate(populated,lambda r:(r['fichier'],r['page'],r['element']),
                             lambda r:reinforcement_key(r['reinforcement']))
    return output,dict(candidates=candidates,unassigned=unassigned,deduplication=dedup,
                       title_count=len(titles),bar_lines=len(bars))


def parse_page(page: pymupdf.Page, filename: str) -> tuple[list[dict],dict]:
    t=time.monotonic()
    src=PageImage(page)
    gray=cv2.cvtColor(src.render(page.rect,2),cv2.COLOR_RGB2GRAY)
    bubbles=elevation_axes(src,gray)
    log.info('read %d circle candidates',len(bubbles))
    lines=read_page(src)
    preliminary,_=assemble(lines,bubbles,filename,page.number+1,src.width,src.height)
    lines=refine_bar_lines(src,lines,preliminary)
    output,diag=assemble(lines,bubbles,filename,page.number+1,src.width,src.height)
    lines,corrections=recover_symbol_labels(src,gray,lines,output)
    output,diag=assemble(lines,bubbles,filename,page.number+1,src.width,src.height)
    diag['caption_rereads']=corrections
    diag['stirrups']=stirrup_zones(gray,lines,output,src)
    diag.update(fichier=filename,page=page.number+1,seconds=round(time.monotonic()-t,2),
                lines=[asdict(l) for l in lines],bubbles=[asdict(b) for b in bubbles])
    return output,diag


def plan_output(plan: str) -> tuple[list[dict],list[ElementRecord]]:
    from l2c.page import open_document,prepare
    from l2c.parse.beams import extract,_titles
    doc=open_document(plan)
    output=[];records=[]
    for i in range(len(doc)):
        page=prepare(doc,i,Path(plan).name)
        if page.type_element!='poutre':
            continue
        recs,diag=extract(page,'imperial')
        records.extend(recs)
        for mark,cx,cy,section in _titles(page):
            found=[r for r in recs if r.element==mark]
            if not found:
                continue
            axes=diag['beam_positions'].get(mark,[])
            bars=[]
            for r in found:
                for index,a in enumerate(r.armature):
                    data={k:v for k,v in a.model_dump().items() if v is not None}
                    annotation=r.debug.annotations[index]
                    data.update(role=r.debug.roles[index],raw=annotation['raw'],
                                x=annotation['x'],y=annotation['y'])
                    data['formatted']=f'{a.quantite}-{a.diametre}' if a.quantite else \
                                      f'{a.diametre}@{a.espacement_mm:g}mm' if a.espacement_mm else a.diametre
                    bars.append(data)
            output.append(dict(fichier=page.fichier,page=i+1,feuillet=page.sheet_id,
                element=mark,section=section,axes=axes,position=' → '.join(a['label'] for a in axes),
                reinforcement=bars,summary=' · '.join(b['formatted'] for b in bars),x=cx,y=cy))
    doc.close()
    return output,records


def compare(original: list[dict],atelier: list[dict]) -> dict:
    """No forced parity: missing views and uncertain labels stay review findings."""
    pm={r['element']:r for r in original};dm={r['element']:r for r in atelier}
    findings=[];matched_da=set()
    def evidence_key(mark,b):return mark,tuple(b['bbox']),b['raw']
    for mark in sorted(pm.keys()|dm.keys()):
        p,d=pm.get(mark),dm.get(mark)
        if p is None or d is None:
            findings.append(dict(element=mark,status='missing_da_beam' if d is None else 'extra_da_beam',
                                 requires_human_review=True))
            continue
        pax={a['label']:a['x'] for a in p['axes']};dax={a['label']:a['x'] for a in d['axes']}
        common=sorted(pax.keys()&dax.keys())
        if len(common)<2:
            findings.append(dict(element=mark,status='alignment_unresolved',requires_human_review=True))
            continue
        scale,offset=np.polyfit([pax[k] for k in common],[dax[k] for k in common],1)
        pool=[]
        for beam in atelier:
            if beam['view']!=d['view']:continue
            for b in beam['reinforcement']:
                box=b['bbox'];pool.append(dict(b,element=beam['element'],cx=(box[0]+box[2])/2,cy=(box[1]+box[3])/2,
                    evidence=[evidence_key(beam['element'],b)]))
        # Separate inner/outer face bars (1 + 1) can describe the same two-bar
        # layer as a plan's 2-25M. Both physical annotations remain in DA output.
        paired=[]
        for i,b in enumerate(pool):
            if b['role']!='longitudinale' or b.get('quantite')!=1:continue
            for a in pool[i+1:]:
                if (a['role']==b['role'] and a.get('quantite')==1 and a['diametre']==b['diametre']
                    and abs(a['bbox'][0]-b['bbox'][0])<8 and abs(a['cy']-b['cy'])<10):
                    paired.append(dict(b,quantite=2,raw=b['raw']+' + '+a['raw'],
                                       formatted='2-'+b['diametre'],pair=True,evidence=b['evidence']+a['evidence']))
        pool+=paired
        checks=[]
        for b in p['reinforcement']:
            x=float(scale*b['x']+offset);y=float(d['y']+scale*(b['y']-p['y']))
            possible=[a for a in pool if a['role']==b['role'] and a['diametre']==b['diametre']
                and (not b.get('quantite') or b['quantite']==a.get('quantite'))
                and (not b.get('espacement_mm') or abs(b['espacement_mm']-a.get('espacement_mm',-999))<.1)
                and (abs(a['cx']-x)<70 or (b['role']=='peau' and a['element']==mark))
                and (b['role']!='longitudinale' or abs(a['cy']-y)<30)]
            closest=min(possible,key=lambda a:abs(a['cx']-x)+abs(a['cy']-y),default=None)
            check=dict(plan=b['raw'],role=b['role'],plan_x=b['x'],plan_y=b['y'],
                       expected_da_x=round(x,2),expected_da_y=round(y,2),matched=closest is not None)
            if closest:
                check.update(da=closest['raw'],da_element=closest['element'],paired_faces=closest.get('pair',False))
                matched_da.update(closest['evidence'])
                if b['role']=='peau':
                    for a in possible:
                        if a['element']==mark:matched_da.update(a['evidence'])
            elif b['role']=='longitudinale':
                shifted=[a for a in pool if a['element']==mark and a['role']==b['role']
                    and a['diametre']==b['diametre'] and a.get('quantite')==b.get('quantite')
                    and abs(a['cy']-y)<30]
                if shifted:
                    check['same_beam_value_elsewhere']=[a['raw'] for a in shifted]
                    check['reason']='Same value is present on this beam, but label position does not align; human review required'
            checks.append(check)
        missing=[c for c in checks if not c['matched']]
        section_equal=re.sub(r'\s+','',p['section'])==re.sub(r'\s+','',d['section'])
        findings.append(dict(element=mark,plan_position=p['position'],da_position=d['position'],
            plan_section=p['section'],da_section=d['section'],section_text_equal=section_equal,
            specified_values_checked=len(checks),specified_values_matched=len(checks)-len(missing),
            status='specified_values_found' if not missing else 'reinforcement_review',
            requires_human_review=True,checks=checks,
            reason='Spatial count/diameter/spacing coverage; shared supports may link neighbouring beams. Extra DA bars, zone multiplicity, geometry and section revisions still require review'))
    extra=[dict(element=r['element'],raw=b['raw'],role=b['role'],bbox=b['bbox'],
                status='unmatched_da_annotation',requires_human_review=True,
                reason='Could be extra reinforcement, shifted label, or an original-parser omission')
           for r in atelier for b in r['reinforcement'] if evidence_key(r['element'],b) not in matched_da]
    return dict(original_beams=len(pm),da_beams=len(dm),shared_beams=len(pm.keys()&dm.keys()),
                specified_values_checked=sum(f.get('specified_values_checked',0) for f in findings),
                specified_values_matched=sum(f.get('specified_values_matched',0) for f in findings),
                unmatched_da_annotations=extra,findings=findings)


def records(rows: list[dict]) -> list[ElementRecord]:
    from l2c.beam_records import align_beam_records
    output=[]
    for r in rows:
        bars=[Armature(**{k:b[k] for k in Armature.model_fields if k in b}) for b in r['reinforcement']]
        output.append(ElementRecord(id=f"poutre_{r['page']}_{r['element']}_atelier",source='atelier',
            fichier=r['fichier'],feuillet=f"{Path(r['fichier']).stem} p{r['page']}",page=r['page'],
            x=r['x'],y=r['y'],type_element='poutre',element=r['element'],armature=bars,
            debug=Debug(raw=[b['raw'] for b in r['reinforcement']],decode_path='ocr',
                        locator_kind='elevation',confidence=r['confidence'],axes=r['axes'],
                        position=r['position'],section=r.get('section'), view=r.get('view'),
                        annotations=[dict(role=b['role'],raw=b.get('raw',''),bbox=b.get('bbox'),
                            x=(b['bbox'][0]+b['bbox'][2])/2 if b.get('bbox') else None,
                            y=(b['bbox'][1]+b['bbox'][3])/2 if b.get('bbox') else None,
                            confidence=b.get('confidence',r['confidence'])) for b in r['reinforcement']],
                        roles=[b['role'] for b in r['reinforcement']])))
    return align_beam_records(output)


def save(path: str, value) -> None:
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('file',nargs='?',default=DEFAULT_FILE)
    ap.add_argument('--output-json',default='out/poutre_clp_output.json')
    ap.add_argument('--diagnostics',default='out/poutre_clp_diagnostics.json')
    ap.add_argument('--json',default='out/poutre_clp.json')
    ap.add_argument('--compare-plan',help='Original plan PDF; read only after blind DA extraction')
    ap.add_argument('--comparison-json',default='out/poutre_clp_comparison.json')
    ap.add_argument('--plan-output-json',default='out/poutre_clp_plan_output.json')
    ap.add_argument('--annotated',help='One-page source review PDF')
    ap.add_argument('--check',action='store_true',help='Post-OCR hidden-text validation only')
    args=ap.parse_args(argv)
    outputs=[args.output_json,args.diagnostics,args.json]
    if args.compare_plan:outputs.extend([args.comparison_json,args.plan_output_json])
    if args.annotated:outputs.append(args.annotated)
    paths=[Path(p).resolve() for p in outputs]
    if len(set(paths))!=len(paths) or any(p in [Path(args.file).resolve(),Path(args.compare_plan).resolve() if args.compare_plan else None] for p in paths):
        ap.error('input and output paths must be distinct')
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(message)s')
    cv2.setNumThreads(1)
    with pymupdf.open(args.file) as doc:
        page=doc[-1];page.remove_rotation()
        rows,diag=parse_page(page,Path(args.file).name)
        if args.check:diag['validation']=check_source(page,rows,diag)
        save(args.output_json,rows);save(args.diagnostics,diag)
        save(args.json,[r.to_schema() for r in records(rows)])
        if args.annotated:
            review=pymupdf.open();review.insert_pdf(doc,from_page=len(doc)-1,to_page=len(doc)-1)
            shape=review[0].new_shape()
            for r in rows:
                shape.draw_rect(pymupdf.Rect(r['region']));shape.finish(color=(0,.4,1),width=.7)
                shape.insert_text((r['x'],r['y']+16),r['element'],fontsize=8,color=(0,.4,1))
            shape.commit();Path(args.annotated).parent.mkdir(parents=True,exist_ok=True)
            review.save(args.annotated);review.close()
    if args.compare_plan:
        original,_=plan_output(args.compare_plan)
        save(args.plan_output_json,original);save(args.comparison_json,compare(original,rows))
    print(f"{len(rows)} beams / {sum(len(r['reinforcement']) for r in rows)} reinforcement labels; {diag['seconds']}s; final JSON: {args.output_json}")
    return 0


if __name__=='__main__':
    raise SystemExit(main())
