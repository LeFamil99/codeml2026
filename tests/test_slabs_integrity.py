"""Original-plan integrity: source detail values, circled labels and directions."""

import os

import pymupdf
import pytest

from l2c.model import ElementRecord
from l2c.page import prepare
from l2c.parse import slabs, slab_integrity


def test_catalog_uses_document_table_values_and_rejects_conflicts():
    def document(quantity):
        doc = pymupdf.open()
        page = doc.new_page(width=600, height=600)
        for x, y, text in [(100, 100, "IDENTIFICATION"), (240, 100, "ARMATURE"),
                           (133, 120, "A"), (240, 120, f"{quantity}-20M CH. DIR."),
                           (65, 300, "101"),
                           (100, 300, "DETAIL - ARMATURE D'INTEGRITE POUR DALLE STRUCTURALE")]:
            page.insert_text((x, y), text, fontsize=10)
        return doc
    with document(7) as doc:
        catalog, conflicts = slab_integrity.read_catalog([prepare(doc, 0, "source.pdf")])
        assert catalog["A"]["quantite"] == 7 and catalog["A"]["diametre"] == "20M"
        assert conflicts == []
        with document(8) as other:
            catalog, conflicts = slab_integrity.read_catalog([
                prepare(doc, 0, "source.pdf"), prepare(other, 0, "other.pdf")])
            assert "A" not in catalog and conflicts == ["A"]


@pytest.mark.parametrize("index,sheet,counts", [(23, "S-601", {"A": 27, "B": 43}),
                                               (24, "S-602", {"A": 30, "B": 40})])
def test_clp_integrity_labels_resolve_to_each_direction(corpus, index, sheet, counts):
    from collections import Counter
    path = os.path.join(corpus, "CLP/L2C_PLAN_STR_CLP.pdf")
    with pymupdf.open(path) as doc:
        page = prepare(doc, index, os.path.basename(path))
        records, diag = slabs.extract(page, "imperial")
        integrity = [r for r in records if r.debug.reinforcement_kind == "integrity"]
        assert page.sheet_id == sheet
        assert Counter(r.debug.integrity_type for r in integrity) == counts
        assert not diag["integrity"]["unresolved"]
        assert len({r.id for r in records}) == len(records)
        for record in integrity:
            assert record.debug.detail_reference["feuillet"] == "S-003"
            assert record.debug.detail_reference["detail"] == "101"
            assert record.debug.roles == ["NUM", "ALP"]
            expected = 2 if record.debug.integrity_type == "A" else 3
            assert [(a.quantite, a.diametre) for a in record.armature] == [(expected, "15M")] * 2
            ElementRecord.model_validate(record.to_schema())
        j15 = [r for r in integrity if r.element == "J-15"]
        assert len(j15) == 1 and j15[0].debug.integrity_type == "B"
        if sheet == "S-601":
            numeric = next(r for r in records if r.debug.raw[0] == "11(5)")
            assert numeric.debug.reinforcement_kind == "slab"
            assert numeric.debug.parenthesized_count == 5


def test_missing_integrity_table_is_reported_without_invented_quantities(corpus):
    path = os.path.join(corpus, "CLP/L2C_PLAN_STR_CLP.pdf")
    with pymupdf.open(path) as doc:
        doc._l2c_slab_integrity_catalog = ({}, [])
        records, diag = slabs.extract(prepare(doc, 23, os.path.basename(path)), "imperial")
        assert not any(r.debug.reinforcement_kind == "integrity" for r in records)
        assert len(diag["integrity"]["unresolved"]) == 70
