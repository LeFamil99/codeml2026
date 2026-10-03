"""What DA files a project has, what each one is, and how each page can be read.

Measured 2026-10-03 on the four dev projects: 137 files, 421 pages, 81 with a text
layer, 340 with outlined glyphs (each character drawn as its own filled path, no font).
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass

import pymupdf

# folder names are the fabricator's own (four projects, four vocabularies)
_FOLDER_TYPES: list[tuple[str, str]] = [
    ("COLONNE", "colonne"),
    ("DALLE", "dalle"),
    ("POUTRE", "poutre"),
    ("REFEND", "mur_refend"),
    ("CISAILLEMENT", "mur_refend"),
    ("MUR", "mur_refend"),
    ("RADIER", "radier"),
    ("SEMELLE", "semelle"),
    ("FONDATION", "semelle"),
]

TIER_LABELS = {1: "texte", 2: "glyphes vectoriels", 3: "image (OCR)", 4: "illisible"}


def _fold(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s.upper())
                   if unicodedata.category(c) != "Mn")


def folder_type(folder: str, filename: str = "") -> str | None:
    """Element type from the fabricator's folder name; the file name refines the mixed
    foundation folders (CLP ``Fondations/CLP_RADIERS.pdf`` is a radier)."""
    name = _fold(filename)
    if "RADIER" in name:
        return "radier"
    if "SEMELLE" in name or "EMPATTEMENT" in name:
        return "semelle"
    f = _fold(folder)
    for needle, kind in _FOLDER_TYPES:
        if needle in f:
            return kind
    return None


_FILL_OP = re.compile(rb"(?<![A-Za-z])f\*?(?![A-Za-z])")


def page_tier(page: pymupdf.Page) -> int:
    """1 = text layer; 2 = outlined glyphs (many small filled paths); 3 = raster; 4 = none.

    Tier 2 is decided by counting fill operators in the content stream (0.10 s on a 6 MB
    WP2 page vs 0.49 s for get_drawings); content drawn through form XObjects is not in
    that stream, so a low count falls back to the exact get_cdrawings() census."""
    if len(page.get_text("words")) > 20:
        return 1
    if len(_FILL_OP.findall(page.read_contents())) > 200:
        return 2
    small = 0
    for d in page.get_cdrawings():
        r = pymupdf.Rect(d["rect"])
        if d.get("fill") is not None and 1 < r.height < 40 and r.width < 40:
            small += 1
            if small > 200:
                return 2
    if page.get_images():
        return 3
    return 4


@dataclass
class DAFile:
    path: str
    folder: str
    name: str
    type_element: str | None
    tiers: list[int]

    @property
    def pages(self) -> int:
        return len(self.tiers)


def find_da_dir(project_dir: str) -> str | None:
    for name in os.listdir(project_dir):
        d = os.path.join(project_dir, name)
        if os.path.isdir(d) and _fold(name) in ("DA", "DESSINS D'ATELIER", "ATELIER"):
            return d
    return None


def inventory(project_dir: str) -> list[DAFile]:
    root = find_da_dir(project_dir)
    if root is None:
        return []
    out = []
    for dp, _, files in os.walk(root):
        for f in sorted(files):
            if not f.lower().endswith(".pdf"):
                continue
            path = os.path.join(dp, f)
            folder = os.path.relpath(dp, root)
            with pymupdf.open(path) as doc:
                tiers = [page_tier(pg) for pg in doc]
            out.append(DAFile(path, folder, f, folder_type(folder, f), tiers))
    out.sort(key=lambda d: (d.folder, d.name))
    return out
