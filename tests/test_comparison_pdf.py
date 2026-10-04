import pymupdf

from l2c.pipeline import ProjectResult
from l2c.report.comparison_pdf import build_comparison_pdf

BAR = dict(role="VERT", repere=None, diametre="25M", quantite=4, espacement_mm=None, longueur_mm=None)


def row(status, element="A-1", kind="colonne", reason="Armatures extraites identiques.", plan=None,
        atelier=None, unmatched_plan=(), unmatched_atelier=(), match="exact", atelier_element=None):
    plan = [dict(BAR)] if plan is None else plan
    atelier = [dict(BAR)] if atelier is None else atelier
    return dict(type_element=kind, niveau="NIVEAU 2", layer="", element=element, status=status,
                reason=reason, plan=plan, atelier=atelier, unmatched_plan=list(unmatched_plan),
                unmatched_atelier=list(unmatched_atelier),
                plan_sources=[dict(feuillet="S-500", fichier="plan.pdf")] if status != "missing_plan" else [],
                atelier_sources=[dict(feuillet="p1", fichier="CLP_COLONNES.pdf")] if status != "missing_da" else [],
                plan_element=element, atelier_element=atelier_element or element, coordinate_match=match)


def project(name):
    return ProjectResult(name, f"/x/L2C_PLAN_STR_{name}.pdf", None, {}, [], [], 0.)


def text_of(pdf: bytes) -> str:
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def test_report_is_pdf_with_every_row_and_summary():
    rows = [
        row("changed", "A-1", plan=[dict(BAR, quantite=4)], atelier=[dict(BAR, quantite=6)],
            unmatched_plan=[BAR], unmatched_atelier=[dict(BAR, quantite=6)],
            reason="Armatures différentes ou annotations supplémentaires."),
        row("missing_da", "B-2", reason="Élément trouvé dans le plan uniquement.", atelier=[]),
        row("missing_plan", "C-3", kind="semelle", reason="Élément trouvé dans les DA uniquement.", plan=[]),
        row("review", "D-4", kind="poutre", reason="Lecture incomplète ou conflit : vérifier les sources."),
        row("same", "E-5"),
        row("same", "F-6", match="proche", atelier_element="F.5-6",
            reason="Armatures extraites identiques. Coordonnées rapprochées appariées : plan F-6, DA F.5-6."),
        row("out_of_scope", "G-7", kind="dalle", reason="Type, niveau ou couche absent des résultats DA chargés.",
            atelier=[]),
    ]
    pdf = build_comparison_pdf(rows, project("CLP"), project("CLP"))
    assert pdf.startswith(b"%PDF")
    text = text_of(pdf)
    for element in ("A-1", "B-2", "C-3", "D-4", "E-5", "F-6", "G-7"):
        assert element in text
    assert "Rapport de comparaison — CLP" in text
    assert "2. Écarts à traiter (4)" in text
    assert "3. Autres comparaisons (3" in text
    assert "DA F.5-6" in text                      # grid pairing shown in the element cell
    assert "Coordonnées rapprochées" not in text.split("3. Autres")[1]  # but not repeated as a motif line


def test_identical_rows_show_bars_once_and_empty_input_still_renders():
    pdf = build_comparison_pdf([row("same", "E-5")], project("CLP"), project("CLP"))
    assert "VERT 4-25M" in text_of(pdf)
    empty = build_comparison_pdf([], project("CLP"), project("CLP"))
    assert empty.startswith(b"%PDF") and "Aucun écart détecté." in text_of(empty)
