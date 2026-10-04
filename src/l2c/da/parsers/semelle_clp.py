"""CLP isolated footings: circled grid, hexagonal type, reinforcement schedule.

Local pixel OCR only; the last page of ONE PDF is read. ``--check`` reads hidden
text afterwards for independent validation, never to repair the extraction.
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

from l2c.da.imageread import PageImage, TextLine
from l2c.da.parsers import colonne_clp as ocr
from l2c.da.parsers.dalle_clp import (
    Bubble, View, build_views, center, circle_candidates, fold, grey_rectangles,
    read_bubbles, read_region,
)
from l2c.model import Debug, ElementRecord
from l2c.units import BAR_DESIGNATORS
from l2c.da.parsers.output import (
    deduplicate, reinforcement_key, view_correspondences, grid_identity, grid_quality,
)

DEFAULT_FILE = os.path.expanduser(
    "~/Downloads/l2c-participants/CLP/DA/Fondations/CLP_SEMELLES FND.pdf")
log = logging.getLogger("semelle_clp")
TYPE = re.compile(r"^TYPE\s*[-:]?\s*([A-Z])\s*$")
BAR = re.compile(r"^(\d+)\s*[-–—]\s*(\d{2}M)$")


def parse_schedule(lines: list[TextLine]) -> dict[str, dict]:
    """Associate cells by their row and named directional columns, never by order.

    Conflicting or incomplete definitions remain diagnostic entries. No doubling
    of a single direction, and no assumption that A..G have particular values.
    """
    headers = {}
    for line in lines:
        name = fold(line.text).replace(" ", "")
        for role, word in (("LONG", "LONGITUDINALE"), ("TRAN", "TRANSVERSALE")):
            if name.endswith(word):
                # OCR sometimes joins ÉPAISSEUR and LONGITUDINALE. Position the
                # directional header within that observed text rather than using
                # the merged box's centre (which belongs between two columns).
                headers[role] = line.x1-(line.x1-line.x0)*len(word)/len(name)/2
    types = [(TYPE.fullmatch(fold(line.text)), line) for line in lines]
    types = [(m.group(1), line) for m, line in types if m]
    catalog = {}
    for label, line in types:
        bars = []
        issues = []
        for role in ("LONG","TRAN"):
            if role not in headers:
                continue
            x=headers[role]
            candidates = [l for l in lines if abs(l.cy - line.cy) < max(3, (line.y1-line.y0)*.65)
                          and abs(l.cx - x) < 20 and BAR.fullmatch(fold(l.text).replace(" ", ""))]
            values = set()
            for cell in candidates:
                m = BAR.fullmatch(fold(cell.text).replace(" ", ""))
                q, diameter = int(m.group(1)), m.group(2)
                if q > 0 and diameter in BAR_DESIGNATORS:
                    values.add((q, diameter))
            if len(values) == 1:
                q, diameter = next(iter(values))
                source = max(candidates, key=lambda l: l.confidence)
                bars.append(dict(role=role, quantite=q, diametre=diameter,
                                 formatted=f"{q}-{diameter}", bbox=list(source.rect),
                                 raw=source.text, confidence=source.confidence))
            else:
                issues.append(f"{role} {'conflicting' if values else 'unread'}")
        if len(headers) < 2:
            issues.append("directional table headers unread")
        entry = dict(type=label, reinforcement=bars, bbox=list(line.rect),
                     status="read" if len(bars) == 2 and not issues else "partial",
                     issues=issues)
        signature=lambda entries: [(b["role"],b["quantite"],b["diametre"]) for b in entries]
        if label in catalog and signature(catalog[label]["reinforcement"]) != signature(bars):
            catalog[label] = dict(type=label, reinforcement=[], bbox=list(line.rect),
                                  status="unread", issues=["conflicting type definitions"])
        else:
            catalog[label] = entry
    return catalog


def table_regions(gray: np.ndarray, zoom: float) -> list[pymupdf.Rect]:
    ink = (gray < 100).astype(np.uint8)*255
    horizontal = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
                                  np.ones((1, round(100*zoom)), np.uint8))
    contours, _ = cv2.findContours(horizontal, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    groups = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if not 150 < w/zoom < 450 or y/zoom < gray.shape[0]/zoom*.55:
            continue
        rect = pymupdf.Rect(x/zoom, y/zoom, (x+w)/zoom, (y+h)/zoom)
        for group in groups:
            if abs(rect.x0-group[0].x0) < 5 and abs(rect.x1-group[0].x1) < 5:
                group.append(rect)
                break
        else:
            groups.append([rect])
    bands=[]
    for group in groups:
        for r in sorted(group,key=lambda r:r.y0):
            if not bands or abs(r.x0-bands[-1][0].x0)>5 or r.y0-bands[-1][-1].y1>25:
                bands.append([r])
            else:
                bands[-1].append(r)
    return [pymupdf.Rect(min(r.x0 for r in g)-2, min(r.y0 for r in g)-25,
                         max(r.x1 for r in g)+2, max(r.y1 for r in g)+2)
            for g in bands if len(g) >= 7 and max(r.y1 for r in g)-min(r.y0 for r in g) < 180]


def hexagons(gray: np.ndarray, zoom: float) -> list[pymupdf.Rect]:
    contours, _ = cv2.findContours((gray < 160).astype(np.uint8)*255,
                                   cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    result = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        perimeter = cv2.arcLength(c, True)
        hull=cv2.convexHull(c)
        polygon = cv2.approxPolyDP(hull, .035*cv2.arcLength(hull,True), True)
        if (7 < w/zoom < 18 and .85 < w/h < 1.3 and len(polygon) == 6
                and .60 < cv2.contourArea(hull)/(w*h) < .85):
            r = pymupdf.Rect(x/zoom, y/zoom, (x+w)/zoom, (y+h)/zoom)
            if not any(math.dist(center(r), center(a)) < 4 for a in result):
                result.append(r)
    # A dimension's opaque white text box can erase the right-hand hexagon edge.
    # Match its remaining left chevron and horizontal caps. Dense hatching is
    # rejected by ink coverage; recognised labels still need a measured anchor.
    w,h=round(13.5*zoom),round(12*zoom)
    template=np.zeros((h,w),np.float32)
    points=np.array([[round(w*.74),1],[round(w*.24),1],[0,h//2],
                     [round(w*.24),h-2],[round(w*.74),h-2]])
    cv2.polylines(template,[points],False,1,max(1,round(.5*zoom)))
    ink=(gray<160).astype(np.float32)
    score=cv2.matchTemplate(ink,template,cv2.TM_CCORR)/template.sum()
    maxima=cv2.dilate(score,np.ones((round(5*zoom),round(5*zoom)),np.uint8))
    ys,xs=np.nonzero((score>=.85)&(score>=maxima-1e-6))
    for x,y in zip(xs.tolist(),ys.tolist()):
        if ink[y:y+h,x:x+w].mean()>.40:
            continue
        r=pymupdf.Rect(x/zoom,y/zoom,(x+w)/zoom,(y+h)/zoom)
        if not any(math.dist(center(r),center(a))<5 for a in result):
            result.append(r)
    return sorted(result, key=lambda r: (r.y0, r.x0))


def read_type(src: PageImage, rect: pymupdf.Rect) -> tuple[str | None, float]:
    x, y = center(rect)
    crop = pymupdf.Rect(x-rect.width*.29, y-rect.height*.32,
                        x+rect.width*.29, y+rect.height*.32)
    result = ocr._ocr()(src.render(crop, 14), use_det=False, use_cls=False, use_rec=True)
    texts, scores = getattr(result, "txts", None), getattr(result, "scores", None)
    text = str(texts[0]).strip().upper() if texts else ""
    if re.fullmatch("[A-Z]", text) and scores[0] >= .5:
        return text,float(scores[0])
    # Tight and wider crops handle type letters touching a leader/revision cloud.
    for factor,shift in ((.22,0),(.38,0),(.29,-.75),(.29,.75),(.29,-1.5),(.29,1.5)):
        crop=pymupdf.Rect(x+shift-rect.width*factor,y-rect.height*.32,
                          x+shift+rect.width*factor,y+rect.height*.32)
        result=ocr._ocr()(src.render(crop,18),use_det=False,use_cls=False,use_rec=True)
        texts,scores=getattr(result,"txts",None),getattr(result,"scores",None)
        text=str(texts[0]).strip().upper() if texts else ""
        if re.fullmatch("[A-Z]",text) and scores[0]>=.5:
            return text,float(scores[0])
    return None,0.


def read_axes(src: PageImage, gray: np.ndarray, zoom: float) -> tuple[list[View], list[Bubble]]:
    bubbles = circle_candidates(gray, zoom)
    # Fractional numbers can break the circle contour. Hough finds its remaining
    # arc; restrict the expensive search to observed numeric-label strips.
    read_bubbles(src, bubbles)
    strips = sorted({round(b.y/5)*5 for b in bubbles if b.label and b.label[0].isdigit()})
    for y in strips:
        if sum(abs(b.y-y) < 5 for b in bubbles) < 3:
            continue
        y0, y1 = max(0, round((y-18)*zoom)), min(gray.shape[0], round((y+18)*zoom))
        patch = gray[y0:y1]
        rings = cv2.HoughCircles(patch, cv2.HOUGH_GRADIENT, 1, 18*zoom,
                                param1=100, param2=35, minRadius=round(9*zoom), maxRadius=round(13*zoom))
        if rings is not None:
            for x, cy, radius in rings[0]:
                candidate = Bubble(float(x/zoom), float((cy+y0)/zoom), float(2*radius/zoom))
                if not any(math.dist((candidate.x,candidate.y),(b.x,b.y)) < 5 for b in bubbles):
                    read_bubbles(src, [candidate])
                    bubbles.append(candidate)
    # Recover letter bubbles cut by a leader or grid stroke in the inset.
    letter_strips=sorted({round(b.x/5)*5 for b in bubbles if b.label and b.label.isalpha()})
    for x in letter_strips:
        if sum(abs(b.x-x)<5 and bool(b.label) for b in bubbles)<3:
            continue
        x0,x1=max(0,round((x-18)*zoom)),min(gray.shape[1],round((x+18)*zoom))
        rings=cv2.HoughCircles(gray[:,x0:x1],cv2.HOUGH_GRADIENT,1,18*zoom,
                               param1=100,param2=35,minRadius=round(9*zoom),maxRadius=round(13*zoom))
        if rings is not None:
            for cx,y,radius in rings[0]:
                candidate=Bubble(float((cx+x0)/zoom),float(y/zoom),float(2*radius/zoom))
                if not any(math.dist((candidate.x,candidate.y),(b.x,b.y))<5 for b in bubbles):
                    read_bubbles(src,[candidate]);bubbles.append(candidate)
    for b in bubbles:
        if b.label is None and any(abs(b.x-x)<5 for x in letter_strips):
            r=pymupdf.Rect(b.x-b.diameter*.27,b.y-b.diameter*.27,
                           b.x+b.diameter*.27,b.y+b.diameter*.27)
            result=ocr._ocr()(src.render(r,14),use_det=False,use_cls=False,use_rec=True)
            texts=getattr(result,"txts",None)
            text=str(texts[0]).strip().upper() if texts else ""
            if re.fullmatch("[A-Z]",text):
                b.label,b.confidence=text,float(result.scores[0])
    # Wider recognition keeps decimal suffixes extending toward the circle rim.
    for b in bubbles:
        if b.label and b.label[0].isdigit():
            r = pymupdf.Rect(b.x-b.diameter*.6, b.y-b.diameter*.24,
                             b.x+b.diameter*.6, b.y+b.diameter*.24)
            result = ocr._ocr()(src.render(r,10), use_det=False, use_cls=False, use_rec=True)
            texts = getattr(result,"txts",None)
            label = str(texts[0]).strip().strip("() ") if texts else ""
            if re.fullmatch(r"\d{1,2}(?:\.\d{1,2})?",label):
                b.label, b.confidence = label, float(result.scores[0])
    views = build_views(bubbles)
    # CLP's detached foundation inset has one top strip and a stepped right-hand
    # letter strip, rather than the slab reader's four opposite strips.
    for y in strips:
        numbers = {b.label:b.x for b in bubbles if b.label and re.fullmatch(r"\d+(?:\.\d+)?",b.label)
                   and abs(b.y-y) < 5 and not any(v.bbox[0] < b.x < v.bbox[2]
                   and v.bbox[1]-5 < b.y < v.bbox[3]+5 for v in views)}
        if len(numbers) < 3:
            continue
        nx0, nx1 = min(numbers.values()), max(numbers.values())
        rows = {b.label:b.y for b in bubbles if b.label and re.fullmatch(r"[A-Z](?:\.\d+)?",b.label)
                and nx1+10 < b.x < nx1+260 and y+20 < b.y < src.height*.7}
        if len(rows) >= 3:
            spacing = np.median(np.diff(sorted(rows.values())))
            views.append(View(f"view-{len(views)+1}", (nx0-40,y,nx1+260,max(rows.values())+float(spacing)),rows,numbers))
    return views,bubbles


def column_symbols(gray: np.ndarray,zoom: float,views=()) -> list[pymupdf.Rect]:
    result=[r for r in grey_rectangles(gray,zoom) if r.width<20 and r.height<25]
    contours,_=cv2.findContours((gray<100).astype(np.uint8)*255,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    for c in contours:
        x,y,w,h=cv2.boundingRect(c)
        hull=cv2.convexHull(c)
        polygon=cv2.approxPolyDP(hull,.03*cv2.arcLength(hull,True),True)
        if 4<w/zoom<18 and 4<h/zoom<25 and len(polygon)==4 and cv2.contourArea(hull)/(w*h)>.7:
            rect=pymupdf.Rect(x/zoom,y/zoom,(x+w)/zoom,(y+h)/zoom)
            if not any(math.dist(center(rect),center(r))<4 for r in result):
                result.append(rect)
    # Long pedestals in the inset cross several rows. Their grey fill supplies
    # the measured column x; create separate anchors at the rows they cross.
    mask=cv2.inRange(gray,180,205)
    mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((round(1.5*zoom),round(1.5*zoom)),np.uint8))
    mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((round(3*zoom),round(3*zoom)),np.uint8))
    _,_,stats,_=cv2.connectedComponentsWithStats(mask,connectivity=8)
    for x,y,w,h,area in stats[1:]:
        if not (5<w/zoom<20 and 25<h/zoom<600 and area/(w*h)>.6):
            continue
        cx=float((x+w/2)/zoom)
        for view in views:
            for cy in view.letters.values():
                # Short dark caps separate successive pedestal segments at a row.
                if y/zoom-8<=cy<=(y+h)/zoom+8 and pymupdf.Rect(view.bbox).contains(pymupdf.Point(cx,cy)):
                    rect=pymupdf.Rect(cx-w/zoom/2,cy-4,cx+w/zoom/2,cy+4)
                    if view.locate(rect) and not any(math.dist(center(rect),center(r))<4 for r in result):
                        result.append(rect)
    return result


def footing_squares(gray: np.ndarray,zoom: float) -> list[pymupdf.Rect]:
    # The large opening removes narrow wall connections joining separate squares.
    result=[]
    for low,high,opening in ((210,235,20),(150,200,12)):
        mask=cv2.inRange(gray,low,high)
        mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((round(opening*zoom),round(opening*zoom)),np.uint8))
        mask=cv2.morphologyEx(mask,cv2.MORPH_CLOSE,np.ones((round(16*zoom),round(16*zoom)),np.uint8))
        _,_,stats,_=cv2.connectedComponentsWithStats(mask,connectivity=8)
        result.extend(pymupdf.Rect(float(x/zoom),float(y/zoom),float((x+w)/zoom),float((y+h)/zoom))
            for x,y,w,h,area in stats[1:] if 30<w/zoom<180 and 30<h/zoom<150 and area/(w*h)>.65)
    return result


def locate_marker(marker: pymupdf.Rect, views: list[View], columns: list[pymupdf.Rect],squares=()) -> dict:
    x,y = center(marker)
    matches = [v for v in views if pymupdf.Rect(v.bbox).contains(pymupdf.Point(x,y))]
    if len(matches) != 1:
        return dict(reason="type marker outside a unique grid view")
    view = matches[0]
    footprints=[r for r in squares if (r+(-5,-5,5,5)).contains(pymupdf.Point(x,y))
                and view.locate(r)]
    if len(footprints)==1 and .6<footprints[0].width/footprints[0].height<1.7:
        footprint=footprints[0]
        return dict(view=view.id,coordinate=view.locate(footprint)[0],anchor=list(center(footprint)),
                    symbol_bbox=list(footprint),detection="footing_square")
    # Markers sit at the lower/right corner. Use the actual small column symbol
    # above/left, never the letter marker itself as the grid anchor.
    candidates = []
    for col in columns:
        cx,cy = center(col)
        if -80 < x-cx < 100 and -8 < y-cy < 85 and pymupdf.Rect(view.bbox).contains(pymupdf.Point(cx,cy)):
            located = view.locate(col+(-20,-16,20,16))
            if located:
                candidates.append((math.dist((x,y),(cx,cy)),col,located[0]))
    if not candidates:
        # Tall wall pedestals do not produce compact column contours. An isolated
        # square gives a second measured anchor, provided its grid lookup is unique.
        if len(footprints)==1:
            footprint=footprints[0]
            coordinate=view.locate(footprint)[0]
            return dict(view=view.id,coordinate=coordinate,anchor=list(center(footprint)),
                        symbol_bbox=list(footprint),detection="footing_square")
        return dict(view=view.id,reason="no nearby column symbol with a verified grid coordinate")
    candidates.sort(key=lambda item:item[0])
    _,col,coordinate = candidates[0]
    if len(candidates)>1 and candidates[1][2]!=coordinate and candidates[1][0]-candidates[0][0]<6:
        return dict(view=view.id,reason="ambiguous neighbouring column symbols")
    return dict(view=view.id,coordinate=coordinate,anchor=list(center(col)),symbol_bbox=list(col),detection="column_symbol")


def parse_page(page: pymupdf.Page, filename: str, coordinates=None, check=False) -> dict:
    started = time.time()
    if page.rotation:
        page.remove_rotation()
    src = PageImage(page)
    zoom=4.
    gray=cv2.cvtColor(src.render(page.rect,zoom),cv2.COLOR_RGB2GRAY)
    views,bubbles=read_axes(src,gray,zoom)
    regions=table_regions(gray,zoom)
    table_lines=[]
    catalog={}
    for region in regions:
        lines=read_region(src,region,6)
        found=parse_schedule(lines)
        if len(found)>len(catalog):
            catalog,table_lines=found,lines
    columns=column_symbols(gray,zoom,views)
    squares=footing_squares(gray,zoom)
    markers=hexagons(gray,zoom)
    footings=[]
    for marker in markers:
        label,confidence=read_type(src,marker)
        row=dict(type=label,marker_bbox=list(marker),confidence=confidence,status="unread",
                 **locate_marker(marker,views,columns,squares))
        if coordinates and row.get("coordinate") not in coordinates:
            continue
        definition=catalog.get(label)
        if definition and definition["reinforcement"]:
            row["reinforcement"]=definition["reinforcement"]
            row["definition_bbox"]=definition["bbox"]
            row["summary"]=" · ".join(b["formatted"] for b in definition["reinforcement"])
            row["status"]="read" if definition["status"]=="read" and row.get("coordinate") else "partial"
            row["confidence"]=round(min([confidence]+[b["confidence"] for b in definition["reinforcement"]]),3)
            if definition["issues"]:
                row["reason"]="; ".join(definition["issues"])
        else:
            row.setdefault("reason","type label unread" if not label else f"no reinforcement definition for type {label}")
        footings.append(row)
    # Multiple contour fragments must not create multiple records at one grid cell.
    groups={}
    for row in footings:
        if row.get("coordinate") and row.get("type") in catalog:
            groups.setdefault((row["view"],row["coordinate"]),[]).append(row)
    for group in groups.values():
        signatures={(row.get("type"),reinforcement_key(row.get("reinforcement",[]))) for row in group}
        if len(group)>1 and len(signatures)>1:
            for row in group:
                row["status"]="unread"
                row["reason"]="multiple type markers assigned to this coordinate; review geometry"
    result=dict(fichier=filename,page=page.number+1,views=[asdict(v) for v in views],
                catalog=catalog,table_lines=[asdict(l) for l in table_lines],squares=[list(r) for r in squares],
                footings=footings,bubbles=[asdict(b) for b in bubbles],
                warnings=[] if catalog and views else ["schedule or grid unread"],
                seconds=round(time.time()-started,2))
    if check:
        validate(page,result)
    return result


def validate(page: pymupdf.Page, result: dict) -> None:
    """Validation only, called after all image extraction is complete."""
    for row in result["footings"]:
        marker=pymupdf.Rect(row["marker_bbox"])
        interior=marker+(marker.width*.2,marker.height*.2,-marker.width*.2,-marker.height*.2)
        words=page.get_text("words",clip=marker+(-2,-2,2,2))
        labels=[w[4] for w in words if re.fullmatch("[A-Z]",w[4])
                and interior.contains(pymupdf.Point((w[0]+w[2])/2,(w[1]+w[3])/2))]
        row["oracle_type_labels"]=labels
        row["type_check"]=labels==[row.get("type")]
    for definition in result["catalog"].values():
        for bar in definition["reinforcement"]:
            text=page.get_text("text",clip=pymupdf.Rect(bar["bbox"])+(-1,-1,1,1)).strip()
            bar["count_size_check"]=text.replace(" ","")==bar["formatted"]
    # Coverage audit of marker-sized type letters in the observed drawing views.
    # This runs after extraction and cannot add or alter an output footing.
    references=[]
    for word in page.get_text("words"):
        if word[4] not in result["catalog"] or not 5<word[3]-word[1]<10:
            continue
        x,y=(word[0]+word[2])/2,(word[1]+word[3])/2
        if any(min(v["numbers"].values())-50<x<max(v["numbers"].values())+70
               and min(v["letters"].values())-30<y<max(v["letters"].values())+50
               for v in result["views"]):
            references.append(word)
    unmatched=[]
    for word in references:
        point=pymupdf.Point((word[0]+word[2])/2,(word[1]+word[3])/2)
        if not any(row.get("coordinate") and row["status"]=="read" and row.get("type")==word[4]
                   and (pymupdf.Rect(row["marker_bbox"])+(-1,-1,1,1)).contains(point)
                   for row in result["footings"]):
            unmatched.append(dict(type=word[4],bbox=list(word[:4])))
    result["validation"]=dict(reference_type_labels=len(references),
                              located_type_labels=len(references)-len(unmatched),
                              unmatched_type_labels=unmatched,
                              schedule_matches=sum(b["count_size_check"] for d in result["catalog"].values()
                                                   for b in d["reinforcement"]),
                              schedule_entries=sum(len(d["reinforcement"]) for d in result["catalog"].values()))


def clean_output(result: dict) -> list[dict]:
    output=[]
    for row in result["footings"]:
        bars=[{k:b[k] for k in ("role","formatted","quantite","diametre")}
              for b in row.get("reinforcement",[]) if isinstance(b.get("quantite"),int)
              and b["quantite"]>0 and b.get("diametre") in BAR_DESIGNATORS]
        if not bars or not row.get("coordinate") or not row.get("type") or row["status"]=="unread":
            continue
        entry={k:row[k] for k in ("coordinate","view","type","status","confidence","reason","type_check")
               if row.get(k) is not None}
        entry.update(fichier=result["fichier"],page=result["page"],niveau="FONDATION",
                     summary=" · ".join(b["formatted"] for b in bars),reinforcement=bars)
        output.append(entry)
    mappings=view_correspondences(result.get("views",[]))
    output,report=deduplicate(output,
        identity=lambda row:(row["fichier"],row["niveau"],*grid_identity(row,mappings)),
        signature=lambda row:(row["type"],reinforcement_key(row["reinforcement"])),
        quality=lambda row:grid_quality(row,mappings))
    report["view_correspondences"]=mappings
    result["deduplication"]=report
    return output


def records(result: dict) -> list[ElementRecord]:
    output=[]
    for row in clean_output(result):
        evidence=next(s for s in result["footings"] if s.get("coordinate")==row["coordinate"]
                      and s.get("view")==row["view"] and s.get("type")==row["type"]
                      and s.get("reinforcement") and s["status"]!="unread")
        x,y=evidence["anchor"]
        output.append(ElementRecord(id=f"{result['fichier']}_{result['page']}_{row['view']}_{row['coordinate']}_atelier",
            source="atelier",fichier=result["fichier"],feuillet=f"{Path(result['fichier']).stem} p{result['page']}",
            page=result["page"],x=x,y=y,type_element="semelle",element=row["coordinate"],
            armature=[{k:b[k] for k in ("quantite","diametre")} for b in row["reinforcement"]],
            debug=Debug(decode_path="ocr",locator_kind="grid",niveau="FONDATION",confidence=row["confidence"],
                        footing_type=row["type"],roles=[b["role"] for b in row["reinforcement"]],view=row["view"],
                        marker_bbox=evidence["marker_bbox"],definition_bbox=evidence["definition_bbox"],
                        duplicate_conflict=row.get("duplicate_conflict",False))))
    return output


def annotate(page: pymupdf.Page,result: dict) -> None:
    shape=page.new_shape()
    final=clean_output(result)
    suppressed={(r.get("view"),r.get("coordinate"),r.get("type"))
                for event in result["deduplication"]["duplicates"] for r in event["suppressed"]}
    shape.insert_text((20,20),f"{len(final)} unique footings; blue markers = repeated source details",fontsize=10)
    for row in result["footings"]:
        r=pymupdf.Rect(row["marker_bbox"])
        repeated=(row.get("view"),row.get("coordinate"),row.get("type")) in suppressed
        color=(0,.35,1) if repeated else (0,.6,0) if row["status"]=="read" else (1,.4,0)
        shape.draw_rect(r+(-2,-2,2,2));shape.finish(color=color,width=.6)
        label=f"{row.get('coordinate','?')} {row.get('type') or '?'}"+(" (repeat)" if repeated else "")
        shape.insert_text((r.x0,r.y1+6),label,fontsize=5,color=color)
        if row.get("anchor"):
            shape.draw_line(center(r),row["anchor"]);shape.finish(color=color,width=.5)
    shape.commit()


def main(argv=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file",nargs="?",default=DEFAULT_FILE)
    parser.add_argument("--coordinate",action="append")
    parser.add_argument("--threads",type=int,default=4)
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--output-json",default="out/semelle_clp_output.json",help="FINAL sanitized JSON")
    parser.add_argument("--diagnostics",default="out/semelle_clp_diagnostics.json")
    parser.add_argument("--json",default="out/semelle_clp.json",help="Appendix-A records")
    parser.add_argument("--annotated",help="last-page review PDF")
    args=parser.parse_args(argv)
    source=Path(args.file).expanduser()
    if not source.is_file():
        parser.error("provide one PDF file")
    paths=[Path(p).resolve() for p in (args.output_json,args.diagnostics,args.json,args.annotated) if p]
    if len(set(paths))!=len(paths) or source.resolve() in paths:
        parser.error("input and all output paths must differ")
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(message)s",datefmt="%H:%M:%S")
    ocr.OCR_THREADS=max(1,args.threads);ocr._ocr.cache_clear();cv2.setNumThreads(1)
    with pymupdf.open(source) as doc:
        log.info("Reading last page ONLY (%d/%d)",len(doc),len(doc))
        result=parse_page(doc[-1],source.name,args.coordinate,args.check)
        final=clean_output(result)
        for path,data in ((args.output_json,final),(args.diagnostics,result),
                          (args.json,[r.to_schema() for r in records(result)])):
            dest=Path(path);dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        if args.annotated:
            annotate(doc[-1],result)
            dest=Path(args.annotated);dest.parent.mkdir(parents=True,exist_ok=True)
            with pymupdf.open() as review:
                review.insert_pdf(doc,from_page=len(doc)-1,to_page=len(doc)-1);review.save(dest)
    print(f"{len(final)} footings exported; {len(result['footings'])} candidates; {len(result['catalog'])} types; {result['seconds']}s")
    print(f"FINAL OUTPUT: {args.output_json}\nDiagnostics: {args.diagnostics}")
    return 0


if __name__=="__main__":
    raise SystemExit(main())
