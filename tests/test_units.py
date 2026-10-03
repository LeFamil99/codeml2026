import pytest
from l2c.units import (BAR_DESIGNATORS, detect_unit_system, parse_imperial_length,
                       parse_spacing)


@pytest.mark.parametrize("text,mm", [
    ("157' - 9\"", 48082.2),
    ("6\"", 152.4),
    ("16 1/4\"", 412.8),
    ("100", None),
])
def test_parse_imperial_length(text, mm):
    assert parse_imperial_length(text) == mm


def test_parse_spacing_both_systems():
    assert parse_spacing('10M@6" c/c', "imperial") == 152.4     # CLP dialect
    assert parse_spacing("10M@100 c/c", "metric") == 100.0      # EspCa3B dialect
    assert parse_spacing("ARM.: 4-25M", "metric") is None       # no spacing stated -> None


def test_bar_designators_are_closed():
    """The lexicon that makes an M->H decode error unrepresentable (PLAN S5.14)."""
    assert "25M" in BAR_DESIGNATORS
    assert "25H" not in BAR_DESIGNATORS
    assert len(BAR_DESIGNATORS) == 8


def test_detect_unit_system():
    system, _ = detect_unit_system(['LIG.: 10M@6" c/c', "EL.: 157' - 9\""])
    assert system == "imperial"
    system, _ = detect_unit_system(["LIG.: 10M@100 c/c", "COL. 300x300"])
    assert system == "metric"
