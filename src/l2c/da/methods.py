"""Parsing methods for shop drawings (DA).

A method turns the uploaded PDFs of its categories into element records. The CLP method reads
the five CLP formats. Another drawing family is added by registering a Method here: its runner
takes (project_dir, inputs=..., progress=..., **options) and returns a ProjectResult.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .dashboard import run_da

CLP_CATEGORIES = ("colonne", "dalle", "semelle", "poutre", "radier")


@dataclass(frozen=True)
class Method:
    id: str
    label: str
    categories: tuple[str, ...]
    runner: Callable


METHODS = {
    "clp": Method("clp", "CLP — formats d'atelier CLP", CLP_CATEGORIES, run_da),
}
DEFAULT_METHOD = "clp"


def get(method_id: str) -> Method:
    if method_id not in METHODS:
        raise ValueError(f"Méthode d'analyse inconnue : {method_id}")
    return METHODS[method_id]
