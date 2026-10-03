import pytest
from pydantic import ValidationError
from l2c.model import Armature, ElementRecord


def _record(**kw):
    base = dict(id="S-502_K-6_plan", source="plan", fichier="L2C_PLAN_STR_CLP.pdf",
                feuillet="S-502", page=18, x=1856.0, y=1089.04,
                type_element="colonne", element="K-6",
                armature=[Armature(repere=None, diametre="35M", quantite=4)])
    base.update(kw)
    return ElementRecord(**base)


def test_appendix_a_shape_exactly():
    d = _record().to_schema()
    assert set(d) == {"id", "source", "fichier", "feuillet", "page", "x", "y",
                      "type_element", "element", "armature"}
    assert set(d["armature"][0]) == {"repere", "diametre", "quantite",
                                     "espacement_mm", "longueur_mm"}


def test_debug_is_never_serialised():
    r = _record()
    r.debug.confidence = 0.42
    assert "debug" not in r.to_schema()


def test_coordinates_rounded_for_determinism():
    assert _record().to_schema()["y"] == 1089.0


def test_bad_bar_designator_is_unrepresentable():
    """An M->H glyph decode error cannot reach the JSON (PLAN S5.14: 83% raw error)."""
    with pytest.raises(ValidationError):
        Armature(diametre="25H")
    with pytest.raises(ValidationError):
        Armature(diametre="25")


def test_unknown_element_type_rejected():
    with pytest.raises(ValidationError):
        _record(type_element="mur")
