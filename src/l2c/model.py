"""Canonical records and the Appendix-A output contract.

``Armature``/``ElementRecord`` mirror the consignes' schema EXACTLY. Debug material
lives on ``ElementRecord.debug``, which is excluded from serialisation so the graded
artifact stays on-schema (PLAN SS9.1).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .units import BAR_DESIGNATORS

Source = Literal["plan", "atelier"]
TypeElement = Literal["colonne", "poutre", "mur_refend", "semelle", "radier", "dalle"]
LocatorKind = Literal["grid", "elevation", "attribute", "unknown"]


class Armature(BaseModel):
    """One reinforcement entry. Field names and types are fixed by Appendix A."""

    model_config = ConfigDict(extra="forbid")

    repere: str | None = None
    diametre: str | None = None
    quantite: int | None = None
    espacement_mm: float | None = None
    longueur_mm: float | None = None

    @field_validator("diametre")
    @classmethod
    def _closed_vocabulary(cls, v: str | None) -> str | None:
        """Reject anything outside the closed designator set.

        This is the guard that makes an `M`->`H` decode error impossible to emit
        (PLAN SS5.14, measured 83% raw error rate on EspCa3B).
        """
        if v is not None and v not in BAR_DESIGNATORS:
            raise ValueError(
                f"{v!r} is not a Canadian bar designator; expected one of "
                f"{sorted(BAR_DESIGNATORS)}"
            )
        return v


class Debug(BaseModel):
    """Provenance and confidence. Never serialised into the deliverable."""

    model_config = ConfigDict(extra="allow")

    raw: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    decode_path: Literal["text_layer", "glyph_decoded", "ocr", "unread"] = "text_layer"
    locator_kind: LocatorKind = "unknown"
    niveau: str | None = None
    symbol_bbox: tuple[float, float, float, float] | None = None
    tier: int = 1


class ElementRecord(BaseModel):
    """One identified piece of information, per Appendix A."""

    model_config = ConfigDict(extra="forbid")

    id: str
    source: Source
    fichier: str
    feuillet: str
    page: int
    x: float
    y: float
    type_element: TypeElement
    element: str
    armature: list[Armature] = Field(default_factory=list)

    debug: Debug = Field(default_factory=Debug, exclude=True)

    def to_schema(self) -> dict[str, Any]:
        """Appendix-A dict: debug stripped, floats rounded for deterministic output."""
        d = self.model_dump(mode="json")
        d["x"] = round(self.x, 1)
        d["y"] = round(self.y, 1)
        return d


def sort_key(r: ElementRecord) -> tuple:
    """Deterministic ordering so two runs produce byte-identical JSON (PLAN SS9.1)."""
    return (r.feuillet, r.page, round(r.y, 1), round(r.x, 1), r.element)
