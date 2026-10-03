"""Page preparation: the two coordinate traps, fixed once, for every page.

Trap 1 - non-zero MediaBox origin (118/624 pages = 19%). CLP declares
MediaBox (-1727.7, -1295.4, 1727.7, 1295.4). PyMuPDF normalises to a (0,0) top-left
origin, which is exactly what Appendix A requires; pdfplumber does not (measured
offset dx=-1727.70, dy=+1298.71).

Trap 2 - /Rotate 90 on 406/624 pages (65%). On a rotated page ``get_text`` reports the
ROTATED space while ``get_drawings`` reports the UNROTATED MediaBox space, silently
breaking every text<->geometry association. ``remove_rotation()`` normalises both.

Nothing else in the codebase may call ``get_text`` or ``get_drawings`` directly.
See PLAN SS3.2.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from typing import Literal

import pymupdf

SHEET_ID = re.compile(r"^S-?\d{3}(\.?[A-Za-z])?$")      # S-502, S-600A, S-103.a
UNKNOWN_SHEET = "UNKNOWN"

# Title phrase -> element type, most specific first. Matched on the title PHRASE, not on
# any keyword in the corner: "COLONNE" appears in WP2's floor-plan callouts and "DALLE"
# in EspCa3B's foundation notes, which the old keyword scan mistook for the sheet type.
_TYPE_RULES: list[tuple[re.Pattern, str | None]] = [
    (re.compile(r"D[ÉE]TAILS? TYPIQUES?"), None),
    (re.compile(r"PLAN DES COLONNES"), "colonne"),
    (re.compile(r"[ÉE]L[ÉE]VATIONS? (DES )?POUTRES"), "poutre"),
    (re.compile(r"CONTREVENTEMENT|MURS? DE REFEND|CISAILLEMENT"), "mur_refend"),
    (re.compile(r"PLAN (AGRANDI )?DES RADIERS|VUE EN PLAN DES RADIERS"), "radier"),
    (re.compile(r"PLAN DES FONDATIONS"), "semelle"),
    (re.compile(r"\bARMATURE DU\b|COLLECTEURS"), "dalle"),
]
_LEVEL = re.compile(
    r"\b(NIVEAU\s*\d+|NIV\.?\s*\d+|RDC|REZ-DE-CHAUSSÉE|SOUS-SOL|SS\d|TOIT(?:\s+APPENTIS)?"
    r"|TRÉFOND|FONDATION\w*|RADIER|EMPATTEMENT)\b"
)

AxisRole = Literal["rows", "columns", "undetermined"]


@dataclass(frozen=True)
class Line:
    """A text LINE as the PDF lays it out (one run of spans), in normalised page space."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    size: float
    vertical: bool

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class Word:
    """A text token in normalised page space: origin top-left, y growing downward."""

    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        """Centre y - the value Appendix A asks for."""
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class PreparedPage:
    index: int
    width: float
    height: float
    fichier: str
    _page: pymupdf.Page = field(repr=False)

    @cached_property
    def words(self) -> list[Word]:
        return [Word(*w[:4], w[4]) for w in self._page.get_text("words")]

    @cached_property
    def lines(self) -> list[Line]:
        out = []
        for b in self._page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                text = " ".join(sp["text"] for sp in l["spans"]).strip()
                if text:
                    out.append(Line(*l["bbox"], text, l["spans"][0]["size"],
                                    abs(l["dir"][0]) < 0.5))
        return out

    @cached_property
    def drawings(self) -> list[dict]:
        """Vector paths, in the SAME space as ``words`` (guaranteed by remove_rotation)."""
        return self._page.get_drawings()

    @property
    def has_text_layer(self) -> bool:
        return bool(self.words)

    @cached_property
    def title_block(self) -> list[Word]:
        """Lower-right corner. Recovers the sheet id on ~95% of plan pages."""
        return [w for w in self.words if w.x0 > 0.60 * self.width and w.y0 > 0.78 * self.height]

    @cached_property
    def sheet_id(self) -> str:
        """``S-502`` / ``S-600A`` / ``S-103.a`` or ``UNKNOWN`` - never a guess.

        The sheet number is the LARGEST sheet-id token in the title block (34.9 pt on
        three projects, 25.6 pt on EspCa3B); smaller ones are cross-references such as
        "VOIR S-201", which the old last-token rule picked on 9 pages.
        """
        def pick(words):
            ids = [w for w in words if SHEET_ID.match(w.text)]
            return max(ids, key=lambda w: (round(w.y1 - w.y0, 1), w.x0, w.y0)) if ids else None

        w = pick(self.title_block) or pick(
            [w for w in self.words if w.x0 > 0.45 * self.width and w.y0 > 0.70 * self.height])
        if w is None:
            return UNKNOWN_SHEET
        t = w.text
        return t if t.startswith("S-") else "S-" + t[1:]

    @cached_property
    def title(self) -> str:
        blob = " ".join(w.text for w in sorted(self.title_block, key=lambda w: (w.y0, w.x0)))
        m = re.search(r"TITRE DU DESSIN\s+(.*?)(?:N°|$)", blob)
        return (m.group(1) if m else blob).strip()

    @cached_property
    def type_element(self) -> str | None:
        up = self.title.upper()
        for rx, value in _TYPE_RULES:
            if rx.search(up):
                return value
        return None

    @cached_property
    def niveau(self) -> str | None:
        m = _LEVEL.search(self.title.upper())
        return m.group(1).strip() if m else None

    def axis_convention(self) -> tuple[AxisRole, AxisRole, dict[str, int]]:
        """Do letters label rows or columns? Varies PER PROJECT (PLAN SS5.15b).

        CLP: letters=rows. WP2/EspCa3B: letters=columns. Hardcoding CLP's convention
        transposes every locator on three of the four projects.
        """
        letters: dict[str, list[Word]] = {}
        numbers: dict[str, list[Word]] = {}
        for w in self.words:
            if re.fullmatch(r"[A-Z]", w.text):
                letters.setdefault(w.text, []).append(w)
            elif re.fullmatch(r"\d{1,2}(\.\d)?", w.text):
                numbers.setdefault(w.text, []).append(w)

        def role(group: dict[str, list[Word]]) -> tuple[int, int]:
            col = row = 0
            for occurrences in group.values():
                if len(occurrences) < 2:
                    continue
                xs = [o.cx for o in occurrences]
                ys = [o.cy for o in occurrences]
                if max(xs) - min(xs) < 40 and max(ys) - min(ys) > 200:
                    col += 1   # same x, spread over y -> marks a column
                if max(ys) - min(ys) < 40 and max(xs) - min(xs) > 200:
                    row += 1   # same y, spread over x -> marks a row
            return col, row

        lc, lr = role(letters)
        nc, nr = role(numbers)
        evidence = {"letter_col": lc, "letter_row": lr, "number_col": nc, "number_row": nr}
        if lr > lc or nc > nr:
            return "rows", "columns", evidence
        if lc > lr or nr > nc:
            return "columns", "rows", evidence
        return "undetermined", "undetermined", evidence


def open_document(path: str) -> pymupdf.Document:
    return pymupdf.open(path)


def prepare(doc: pymupdf.Document, index: int, fichier: str) -> PreparedPage:
    """The ONLY way to obtain a page. Applies both coordinate fixes."""
    page = doc[index]
    if page.rotation:
        page.remove_rotation()
    return PreparedPage(
        index=index,
        width=page.rect.width,
        height=page.rect.height,
        fichier=fichier,
        _page=page,
    )


def prepare_all(path: str):
    doc = open_document(path)
    import os

    name = os.path.basename(path)
    for i in range(len(doc)):
        yield prepare(doc, i, name)
