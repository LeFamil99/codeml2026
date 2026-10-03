"""Grammar shared by every DA parser: one bar line, sheet identity, the label lexicon.

A fabricator's bar line, measured on CLP's DA (all four folders):
    VERT:    4 25M 25Z13-05            label count size mark
    ÉTRI:   28 10M 10ET13X21 @6"       ... + spacing
    LONG: 11 25M 25U12-00   /   TRAN: 44 25M 25RU24-10 @8"BAS
    ATT-LAT: 1 15M 29-06 (2 requis)sens NUM.
    2 15M 17-06                        no label; ``17-06`` is a length (17'-6")
    GOUJ: 25L16X74                     mark only
The label set differs per fabricator (PLAN SS5.6), so labels are COUNTED and reported,
never required. Nothing here can see the plan.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from ..model import Armature
from ..units import BAR_DESIGNATORS, MM_PER_INCH, UnitSystem, parse_spacing

LOCATOR = re.compile(r"^[A-Z]{1,2}(\.\d{1,2})?'?-\d{1,2}(\.\d{1,2})?$")      # K-6, J-10.8, B.2-35

# "VERT:", "ÉTRI:", "ATT-LAT:", "ET.:", and the colon-less "NUM." of CLP integrity steel
_LABEL = r"(?P<label>[A-ZÉÈ][A-ZÉÈ\-]*(?:\.?\s*:|\.(?=\s)))"
_BAR = re.compile(
    rf"^(?:{_LABEL}\s*)?(?P<q>\d+)\s+(?P<size>\d{{2}})\s*M\b\s*"
    r"(?P<mark>[0-9A-Z][0-9A-Z\-X/]*)?\s*"
    r"(?:@\s*(?P<sp>\d+(?:\s+\d/\d)?\s*(?:\"|''|mm)?))?\s*(?P<rest>.*)$")
# "3 15J19-06": count + mark whose leading digits are the size (every CLP mark does this:
# 25Z13-05 -> 25M, 10ET13X21 -> 10M) - accepted, and flagged as size-from-mark
_COUNT_MARK = re.compile(
    rf"^(?:{_LABEL}\s*)?(?P<q>\d+)\s+(?P<mark>(?P<size>\d{{2}})[A-Z]+[0-9A-Z\-X/]*)\s*"
    r"(?:@\s*(?P<sp>\d+(?:\s+\d/\d)?\s*(?:\"|''|mm)?))?\s*(?P<rest>.*)$")
_MARK_ONLY = re.compile(rf"^{_LABEL}\s*(?P<mark>\d{{2}}[A-Z]+[0-9A-Z\-X/]*)\s*(?P<rest>.*)$")
_FT_IN = re.compile(r"^(\d{1,3})-(\d{2})$")                                   # 17-06 = 17'-6"
_SECTION = re.compile(r"^(\d+(?:\s+\d/\d)?)\"?\s*[Xx]\s*(\d+(?:\s+\d/\d)?)\"?$")


@dataclass
class BarLine:
    label: str | None        # VERT / ÉTRI / LONG ... (fabricator's own word)
    armature: Armature
    text: str
    size_from_mark: bool = False


def _length_mm(mark: str | None, system: UnitSystem) -> float | None:
    if mark and system == "imperial" and (m := _FT_IN.match(mark)):
        return round((int(m.group(1)) * 12 + int(m.group(2))) * MM_PER_INCH, 1)
    return None


_DESIG = "10|15|20|25|30|35|45|55"
_STRAY_LEAD = re.compile(r"^[\-._■|~•·●]+\s*(?=\d)")
_SYMBOLS = re.compile(r"\s*[●■•▪◆◇○□▲▼►◄★☆]\s*")       # drawn symbols read as characters
_DOT_SPACE = re.compile(rf"(?<!\S)(\d+)\.(?=(?:{_DESIG})\s*M\b|(?:{_DESIG})[A-Z])")
_LOST_SPACE = re.compile(rf"^(\d{{1,3}}?)({_DESIG})([A-Z]{{1,3}}\d[0-9A-Z\-X/]*)(.*)$")


def _ocr_tolerant(t: str) -> tuple[str, bool]:
    """Read-noise a recogniser produces that the bar grammar can undo SAFELY, because a
    bar quantity is a whole number and a size is one of eight designators:
    a stray leading mark from a leader line (``-3 15J15-03``), a dot read for a space
    (``15.15M`` -> ``15 15M``), a doubled colon (``VERT: :4``), a lost space before a mark whose size is the only valid
    split (``415J5-06`` -> ``4 15J5-06``). Returns (text, changed)."""
    u = _SYMBOLS.sub(" ", t).strip()
    u = re.sub(r":\s*:", ":", u)            # "VERT: :4 25M" - a dashed-line tick read as ':'
    u = _STRAY_LEAD.sub("", u)
    u = _DOT_SPACE.sub(r"\1 ", u)
    if (m := _LOST_SPACE.match(u)) and m.group(1):
        u = f"{m.group(1)} {m.group(2)}{m.group(3)}{m.group(4)}"
    return u, u != t


# characters a recogniser substitutes for an accented capital E (seen: "€T7", "ĘT7" for
# "ÉT7"); marks and labels are compared without accents anyway
_E_LIKE = str.maketrans({"€": "E", "Ę": "E", "É": "E", "È": "E", "Ê": "E"})


def parse_bar_line(text: str, system: UnitSystem) -> BarLine | None:
    t, fixed = _ocr_tolerant(re.sub(r"\s+", " ", text.strip()).translate(_E_LIKE))
    b = _parse(t, system)
    if b is not None and fixed:
        b.size_from_mark = True          # lower confidence downstream: the text was repaired
    return b


def _parse(t: str, system: UnitSystem) -> BarLine | None:
    if (m := _BAR.match(t)) and f"{m.group('size')}M" in BAR_DESIGNATORS:
        mark = (m.group("mark") or "").rstrip(".") or None
        length = _length_mm(mark, system)
        a = Armature(
            quantite=int(m.group("q")), diametre=f"{m.group('size')}M",
            repere=None if (length is not None or not mark) else mark,
            espacement_mm=parse_spacing("@" + m.group("sp"), system) if m.group("sp") else None,
            longueur_mm=length,
        )
        return BarLine(_norm_label(m.group("label")), a, t)
    if (m := _COUNT_MARK.match(t)) and f"{m.group('size')}M" in BAR_DESIGNATORS:
        a = Armature(quantite=int(m.group("q")), diametre=f"{m.group('size')}M",
                     repere=m.group("mark").rstrip("."),
                     espacement_mm=parse_spacing("@" + m.group("sp"), system) if m.group("sp") else None)
        return BarLine(_norm_label(m.group("label")), a, t, size_from_mark=True)
    if (m := _MARK_ONLY.match(t)):
        return BarLine(_norm_label(m.group("label")), Armature(repere=m.group("mark")), t)
    return None


def _norm_label(label: str | None) -> str | None:
    return label.replace(":", "").strip().rstrip(".").upper() if label else None


def section(text: str) -> str | None:
    t = text.strip()
    return t if _SECTION.match(t) else None


def sheet_identity(page, fichier: str) -> tuple[str, str | None]:
    """(feuillet, title). The fabricator's ``DESSIN NO.`` when filled in - blank on every
    CLP DA page - else ``<file stem> p<n>``: never a guess (DA_PLAN ground rule 4)."""
    stem = os.path.splitext(fichier)[0]
    fallback = f"{stem} p{page.index + 1}"
    title = None
    lines = page.lines
    for l in lines:
        if l.text.strip() == "TITRE DU DESSIN":
            right = sorted((o for o in lines if 0 < o.cx - l.cx < 200 and -5 < o.cy - l.cy < 40
                            and o.text.strip() != "STRUCTURE"), key=lambda o: o.cy)
            title = right[0].text.strip() if right else None
        if l.text.strip() in ("DESSIN NO.", "DESSIN NO", "DWG NO."):
            vals = [o for o in lines if o is not l and 0 <= o.cy - l.cy < 30 and abs(o.cx - l.cx) < 40
                    and re.fullmatch(r"[A-Z0-9][A-Z0-9\-_.]{1,15}", o.text.strip())
                    and not o.text.strip().endswith(".")]
            if vals:
                return vals[0].text.strip(), title
    return fallback, title
