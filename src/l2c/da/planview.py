"""DA plan-view sheets (slabs, footings, radier) - shop-drawing side.

Every bar line is placed on the grid with the same line-grid locator the plan uses.
Measured on CLP's DA:
- slabs list every bar band (``14 15M 12-00``) with its spacing on a separate line
  (``@17"``), one page per layer (``ACIER DU BAS`` / ``ACIER DU HAUT`` / ``ACIER
  D'INTÉGRITÉ``); one record per band;
- footings / radier stack ``LONG:``, ``TRAN:``, ``GOUJ:``, ``ATT:`` lines into one block
  per element; one record per block;
- a file's pages share one viewport, but pages 2-3 often show too few bubbles to build a
  grid; such a page borrows its file's best grid, only if the labels both pages DO show
  sit at the same positions (CLP DALLE NIV 4: J at y=806 on all three pages).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from ..geometry.grid import locator
from ..model import Debug, ElementRecord
from ..page import PreparedPage
from ..units import UnitSystem, parse_spacing
from .common import BarLine, parse_bar_line, sheet_identity

_SPACING_ONLY = re.compile(r"^@\s*\d+(?:\s+\d/\d)?\s*(?:\"|''|mm)?\s*(?:c/c)?$")
_LAYER_TITLE = re.compile(r"ACIER (DU BAS|DU HAUT|D'INTEGRITE|D INTEGRITE)|ARMATURE (INFERIEURE|SUPERIEURE)")
_SUFFIX_LAYER = re.compile(r"(BAS|HAUT)\b")

_FILE_GRID: dict[str, object] = {}       # path -> best grid of that file (per run)


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.upper())
                   if unicodedata.category(c) != "Mn")


def _layer_of_page(page: PreparedPage) -> str | None:
    for l in page.lines:
        if m := _LAYER_TITLE.search(_fold(l.text)):
            g = m.group(1) or m.group(2)
            return {"DU BAS": "bas", "DU HAUT": "haut", "INFERIEURE": "bas",
                    "SUPERIEURE": "haut"}.get(g, "intégrité")
    return None


def _grid_for(page: PreparedPage, dafile, points: list[tuple[float, float]]):
    """The grid to place THIS page's bar lines on, by explicit preference:

    1. the page's drawn grid lines, if they place >= half of its bar lines;
    2. the file's best grid, if the labels this page does show sit at the same positions
       (same viewport) and it places >= half;
    3. the page's label-only grid;
    4. otherwise whichever places the most.
    A raw "most placed" count is wrong: the label-only grid places EVERYTHING (nearest
    label), so it won a tie on CLP DALLE NIV 4 p2, where label ``15`` is not printed, and
    shifted a whole column of top bars from J-15 to J-14.4."""
    from ..geometry.grid import extract_grid
    from ..geometry.gridlines import extract_lines

    n = max(1, len(points))

    def coverage(g) -> int:
        return sum(g.locate(x, y)[0] is not None for x, y in points) if g.ok else -1

    lines, labels = extract_lines(page), extract_grid(page)
    chosen = None
    if coverage(lines) >= 0.5 * n:
        chosen = (lines, "page_lines")
    best = _FILE_GRID.get(dafile.path)
    if chosen is None and best is not None and best is not lines:
        agree = [k for k in labels.letters if k in best.letters and abs(labels.letters[k] - best.letters[k]) < 3]
        agree += [k for k in labels.numbers if k in best.numbers and abs(labels.numbers[k] - best.numbers[k]) < 3]
        if (agree or not (labels.letters or labels.numbers)) and coverage(best) >= 0.5 * n:
            chosen = (best, "file_shared")
    if chosen is None and labels.ok:
        chosen = (labels, "page_labels")
    if chosen is None:
        chosen = max([(lines, "page_lines"), (labels, "page_labels")], key=lambda t: coverage(t[0]))
    grid, src = chosen
    # a file's best grid: prefer drawn lines (geometry-checked), then the most labels
    if src == "page_lines" or (src == "page_labels" and best is None):
        rank = (src == "page_lines", len(grid.letters) + len(grid.numbers))
        if best is None or rank > _FILE_GRID.get(dafile.path + "#rank", (False, 0)):
            _FILE_GRID[dafile.path] = grid
            _FILE_GRID[dafile.path + "#rank"] = rank
    return grid, src


def _attach_spacing(page: PreparedPage, bars: list[tuple], system: UnitSystem) -> None:
    """``@17"`` lines -> the nearest bar line of the same orientation lacking a spacing."""
    spacers = [l for l in page.lines if _SPACING_ONLY.match(l.text.strip())]
    for s in spacers:
        cands = [(abs(l.cx - s.cx) + abs(l.cy - s.cy), i) for i, (l, b) in enumerate(bars)
                 if l.vertical == s.vertical and b.armature.espacement_mm is None
                 and b.armature.quantite]
        if not cands:
            continue
        d, i = min(cands)
        if d < 45:
            l, b = bars[i]
            b.armature.espacement_mm = parse_spacing(s.text, system)
            bars[i] = (l, BarLine(b.label, b.armature, f"{b.text} {s.text.strip()}", b.size_from_mark))


def _blocks(bars: list[tuple]) -> list[list[tuple]]:
    """Stack labelled lines (LONG / TRAN / GOUJ / ATT) of one element into a block."""
    out: list[list[tuple]] = []
    for l, b in sorted(bars, key=lambda t: (round(t[0].x0 / 6), t[0].cy)):
        if out:
            pl, _ = out[-1][-1]
            if abs(pl.x0 - l.x0) < 8 and 0 <= l.cy - pl.cy < 16 and pl.vertical == l.vertical:
                out[-1].append((l, b))
                continue
        out.append([(l, b)])
    return out


def make_extract(kind: str, group: bool):
    def extract(page: PreparedPage, system: UnitSystem, dafile) -> tuple[list[ElementRecord], dict]:
        feuillet, title = sheet_identity(page, dafile.name)
        layer_page = _layer_of_page(page)
        labels: Counter = Counter()
        bars = []
        for l in page.lines:
            if l.x0 > 0.85 * page.width and l.y0 > 0.75 * page.height:
                continue                                   # title block
            b = parse_bar_line(l.text, system)
            if b is not None:
                bars.append((l, b))
                labels[b.label or "(sans étiquette)"] += 1
        _attach_spacing(page, bars, system)
        units = _blocks(bars) if group else [[t] for t in bars]
        centres = [((min(l.x0 for l, _ in u) + max(l.x1 for l, _ in u)) / 2,
                    (min(l.y0 for l, _ in u) + max(l.y1 for l, _ in u)) / 2) for u in units]
        grid, grid_src = _grid_for(page, dafile, centres)

        records: list[ElementRecord] = []
        for k, unit in enumerate(units):
            ls = [l for l, _ in unit]
            x0, y0 = min(l.x0 for l in ls), min(l.y0 for l in ls)
            x1, y1 = max(l.x1 for l in ls), max(l.y1 for l in ls)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            element, cost = grid.locate(cx, cy)
            suffix = next((m.group(1).lower() for _, b in unit
                           if (m := _SUFFIX_LAYER.search(b.text))), None)
            inferred = any(b.size_from_mark for _, b in unit)
            conf = max(0.0, 0.95 - 0.6 * cost - (0.1 if inferred else 0.0)
                       - (0.05 if grid_src == "file_shared" else 0.0))
            key = element or "UNKNOWN"
            records.append(ElementRecord(
                id=f"{feuillet}_{key}_{k + 1}_atelier", source="atelier", fichier=dafile.name,
                feuillet=feuillet, page=page.index + 1, x=cx, y=cy, type_element=kind,
                element=key, armature=[b.armature for _, b in unit],
                debug=Debug(raw=[b.text for _, b in unit], confidence=round(conf, 3),
                            locator_kind="grid" if element else "unknown", niveau=title,
                            roles=[b.label for _, b in unit], layer=suffix or layer_page,
                            direction="vertical" if ls[0].vertical else "horizontal",
                            title=title, folder=dafile.folder, grid_source=grid_src),
            ))
        diag = {"feuillet": feuillet, "title": title, "niveau": title,
                "source": grid.evidence.get("source") if hasattr(grid, "evidence") else None,
                "grid_source": grid_src, "layer": layer_page, "labels": dict(labels),
                "records": len(records), "located": sum(r.element != "UNKNOWN" for r in records),
                "warnings": [] if getattr(grid, "ok", False) else
                ["grille introuvable sur cette page : éléments non localisés"]}
        return records, diag
    return extract


slabs = make_extract("dalle", group=False)
footings = make_extract("semelle", group=True)
radier = make_extract("radier", group=True)
