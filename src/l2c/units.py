"""Unit conversion: the single source of truth.

The schema (consignes, Appendix A) requires millimetres, while the drawings are mixed:
CLP is imperial, WP2/LIGREP/EspCa3B are metric (PLAN SS5.6). Every conversion in the
codebase goes through this module so the imperial/metric split is handled in exactly
one place.
"""

from __future__ import annotations

import re
from typing import Literal

MM_PER_INCH = 25.4
MM_PER_FOOT = 304.8

UnitSystem = Literal["imperial", "metric"]

# Canadian rebar designators: a CLOSED set. This is the lexicon that makes the
# glyph decoder reliable (PLAN SS5.8/SS5.14 - 83% M->H error rate without it).
BAR_DESIGNATORS: frozenset[str] = frozenset(
    {"10M", "15M", "20M", "25M", "30M", "35M", "45M", "55M"}
)

#: ``5' - 9"`` / ``157'-9"`` / ``9'``
_FEET_INCHES = re.compile(r"""^\s*(\d+)\s*'\s*(?:-\s*)?(?:(\d+)(?:\s+(\d+)/(\d+))?\s*")?\s*$""")
#: ``6"`` / ``16 1/4"``
_INCHES = re.compile(r"""^\s*(\d+)(?:\s+(\d+)/(\d+))?\s*"\s*$""")


def inches_to_mm(inches: float) -> float:
    return inches * MM_PER_INCH


def parse_imperial_length(text: str) -> float | None:
    """``157' - 9"`` or ``16 1/4"`` -> millimetres. ``None`` if not imperial."""
    m = _FEET_INCHES.match(text)
    if m:
        total = int(m.group(1)) * MM_PER_FOOT
        if m.group(2):
            total += int(m.group(2)) * MM_PER_INCH
        if m.group(3) and m.group(4):
            total += int(m.group(3)) / int(m.group(4)) * MM_PER_INCH
        return round(total, 1)
    m = _INCHES.match(text)
    if m:
        total = int(m.group(1)) * MM_PER_INCH
        if m.group(2) and m.group(3):
            total += int(m.group(2)) / int(m.group(3)) * MM_PER_INCH
        return round(total, 1)
    return None


def parse_spacing(text: str, system: UnitSystem) -> float | None:
    """Spacing token -> millimetres.

    ``10M@6" c/c`` -> 152.4 (imperial);  ``10M@100 c/c`` -> 100.0 (metric).
    Returns ``None`` when no spacing is stated - never a fabricated value.
    """
    m = re.search(r"@\s*(\d+(?:\s+\d+/\d+)?)\s*(\"|mm)?", text)
    if not m:
        return None
    value, suffix = m.group(1), m.group(2)
    if suffix == '"' or (suffix is None and system == "imperial"):
        return parse_imperial_length(f'{value}"')
    return float(value.split()[0])


def detect_unit_system(tokens: list[str]) -> tuple[UnitSystem, dict[str, int]]:
    """Census-based detection, pre-fills the pre-flight form (PLAN SS5.16).

    Measured: CLP 716 imperial vs 177 metric spacing tokens; the other three
    projects 926-1441 metric vs 14-27 imperial.
    """
    blob = " ".join(tokens)
    imperial = len(re.findall(r'@\s?\d{1,2}"', blob)) + len(re.findall(r"\d+'\s*-", blob))
    metric = len(re.findall(r"@\s?\d{2,3}\b", blob)) + len(re.findall(r"\b\d{3}x\d{3}\b", blob))
    evidence = {"imperial_tokens": imperial, "metric_tokens": metric}
    return ("imperial" if imperial > metric else "metric"), evidence
