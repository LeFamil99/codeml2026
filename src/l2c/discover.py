"""Content-based file classification.

Must never rely on paths or filename prefixes: LIGREP's shop drawings are prefixed
`GP2_` rather than the project name, and the element folders differ across projects
(`Murs refends` / `Murs cisaillements` / `Semelles et radiers`). See PLAN SS9.3.1.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Literal

from .page import UNKNOWN_SHEET, prepare_all

Role = Literal["plan", "atelier", "unknown"]

_ELEMENT_FOLDER = [
    ("COLONNE", "colonne"),
    ("POUTRE", "poutre"),
    ("REFEND", "mur_refend"),
    ("CISAILLEMENT", "mur_refend"),
    ("MUR", "mur_refend"),
    ("SEMELLE", "semelle"),
    ("RADIER", "radier"),
    ("FONDATION", "semelle"),
    ("DALLE", "dalle"),
]


@dataclass
class FileInfo:
    path: str
    role: Role
    pages: int
    text_pages: int
    element_type: str | None
    sheet_ids: list[str] = field(default_factory=list)
    unit_system: str | None = None
    axis_letters: str | None = None

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    @property
    def outlined_pages(self) -> int:
        return self.pages - self.text_pages

    @property
    def decode_tier(self) -> str:
        if self.text_pages == self.pages:
            return "1 text"
        if self.text_pages == 0:
            return "2 glyph"
        return "1+2 mixed"


def _element_from_path(path: str) -> str | None:
    up = path.upper()
    for needle, value in _ELEMENT_FOLDER:
        if needle in up:
            return value
    return None


def classify(path: str, deep: bool = True) -> FileInfo:
    from .units import detect_unit_system

    pages = list(prepare_all(path)) if deep else []
    n = len(pages)
    text_pages = sum(1 for p in pages if p.has_text_layer)
    ids = [p.sheet_id for p in pages if p.sheet_id != UNKNOWN_SHEET]

    # A plan set is the document carrying a title-block sheet id on most of its pages.
    role: Role = "unknown"
    if n:
        role = "plan" if len(ids) >= max(2, 0.5 * n) else "atelier"

    tokens = [w.text for p in pages for w in p.words]
    unit = detect_unit_system(tokens)[0] if tokens else None

    letters = None
    for p in pages:
        if p.has_text_layer:
            lr, _, _ = p.axis_convention()
            if lr != "undetermined":
                letters = lr
                break

    elem = _element_from_path(path)
    if role == "plan":
        elem = None
    return FileInfo(path, role, n, text_pages, elem, ids, unit, letters)


def discover(root: str, deep: bool = True) -> list[FileInfo]:
    out = []
    for dirpath, _, files in os.walk(root):
        for fn in sorted(files):
            if fn.lower().endswith(".pdf") and not fn.startswith("consignes"):
                out.append(classify(os.path.join(dirpath, fn), deep=deep))
    return sorted(out, key=lambda f: (f.role != "plan", f.path))
