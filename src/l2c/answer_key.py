"""Check a plan extraction against a ``*_dismatch.xlsx`` answer key.

The key is read at run time (never copied into the code: it is confidential data).
Columns: Feuillet | Localisation | Plan L2C | Dessin d'atelier. For every row we look
for a plan record on that sheet at that locator whose bars state the ``Plan L2C`` value.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model import ElementRecord
from .units import UnitSystem, parse_spacing

_QTY = re.compile(r"^(\d+)-(\d{2}M)$")
_NM = re.compile(r"^(\d+)\((\d+)\)$")
_SPC = re.compile(r"^(?:RANG\s*(\d)\s*:\s*)?(\d{2}M)\s*@\s*(.+)$")


@dataclass
class KeyRow:
    feuillet: str
    localisation: str
    plan: str
    atelier: str


@dataclass
class Verdict:
    row: KeyRow
    found: bool
    candidates: int            # records at that sheet + locator
    matched: ElementRecord | None


def load(path: str) -> list[KeyRow]:
    import openpyxl

    ws = openpyxl.load_workbook(path, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    out = []
    for r in rows[1:]:
        if r and r[0]:
            out.append(KeyRow(*(str(c).strip() if c is not None else "" for c in r[:4])))
    return out


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s.replace("''", '"').replace("’", "'")).upper()


def states(rec: ElementRecord, value: str, system: UnitSystem) -> bool:
    v = _norm(value)
    raw = [_norm(t) for t in rec.debug.raw]
    if m := _QTY.match(v):
        return any(a.quantite == int(m.group(1)) and a.diametre == m.group(2) for a in rec.armature)
    if _NM.match(v):
        return any(t.startswith(v) for t in raw)
    if m := _SPC.match(v):
        sp = parse_spacing(value.replace("''", '"'), system)
        ok = any(a.diametre == m.group(2) and a.espacement_mm is not None and sp is not None
                 and abs(a.espacement_mm - sp) < 0.5 for a in rec.armature)
        if ok and m.group(1):
            layer = str(getattr(rec.debug, "layer", "") or "")
            ok = m.group(1) in layer.split("&")[0] or m.group(1) in layer
        return ok
    return any(v in t for t in raw)


def check(records: list[ElementRecord], rows: list[KeyRow], system: UnitSystem) -> list[Verdict]:
    out = []
    for row in rows:
        here = [r for r in records if r.feuillet == row.feuillet and r.element == row.localisation]
        hit = next((r for r in here if states(r, row.plan, system)), None)
        out.append(Verdict(row, hit is not None, len(here), hit))
    return out
