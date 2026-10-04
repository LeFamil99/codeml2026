"""PDF report of the plan / shop-drawing comparison (PLAN SS9.2).

Reads the rows returned by ``l2c.comparison.compare`` and recomputes nothing. Three parts:
a summary, every discrepancy with its evidence, then every other comparison in compact
form, so each row of the comparison appears in the report exactly once.
"""

from __future__ import annotations

import datetime
import html
import os
from collections import Counter
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

TYPE_LABELS = {"colonne": "Colonnes", "semelle": "Semelles", "radier": "Radiers",
               "mur_refend": "Murs de refend", "poutre": "Poutres", "dalle": "Dalles"}
TYPE_ORDER = list(TYPE_LABELS)

STATUS_LABELS = {"changed": "Armatures différentes", "missing_plan": "Absent du plan",
                 "missing_da": "Absent des DA", "review": "À vérifier", "same": "Identique",
                 "out_of_scope": "Hors couverture DA"}
STATUS_DEFINITIONS = {
    "changed": "Les deux lecteurs trouvent des armatures différentes à cet emplacement.",
    "missing_plan": "Élément lu dans les dessins d'atelier, absent du plan.",
    "missing_da": "Élément lu sur le plan, absent des dessins d'atelier.",
    "review": "Lecture incomplète, conflit ou localisation ambiguë : à vérifier sur les sources.",
    "same": "Armatures extraites identiques par les deux lecteurs.",
    "out_of_scope": "Type, niveau ou couche absent des résultats DA chargés : non comparable.",
}
# Discrepancies first; the order of this tuple is the order of the summary.
ISSUE_STATUSES = ("changed", "missing_plan", "missing_da", "review")
STATUS_ORDER = ISSUE_STATUSES + ("same", "out_of_scope")
STATUS_COLORS = {"changed": "#ffe0da", "missing_plan": "#ffe7ba", "missing_da": "#fff3b0",
                 "review": "#e1edff", "same": "#e4f3e6", "out_of_scope": "#ececec"}
INK = "#17212b"
UNMATCHED = "#b42318"
# Reasons that only restate the status; a motif line is printed only for anything more specific.
GENERIC_REASONS = {"Armatures extraites identiques.", "Élément trouvé dans les DA uniquement.",
                   "Élément trouvé dans le plan uniquement.",
                   "Type, niveau ou couche absent des résultats DA chargés."}

PAGE = landscape(A4)
MARGIN = 12 * mm
WIDTH = PAGE[0] - 2 * MARGIN

_styles = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=_styles["Normal"], fontName="Helvetica", fontSize=8, leading=9.6, textColor=colors.HexColor(INK))
SMALL = ParagraphStyle("small", parent=BODY, fontSize=7, leading=8.4)
MOTIF = ParagraphStyle("motif", parent=BODY, fontName="Helvetica-Oblique", fontSize=7, leading=8.4, textColor=colors.HexColor("#52514e"))
HEAD = ParagraphStyle("head", parent=BODY, fontName="Helvetica-Bold", textColor=colors.white)
TITLE = ParagraphStyle("title", parent=BODY, fontName="Helvetica-Bold", fontSize=18, leading=22)
H2 = ParagraphStyle("h2", parent=BODY, fontName="Helvetica-Bold", fontSize=12, leading=15, spaceBefore=8, spaceAfter=4)
H3 = ParagraphStyle("h3", parent=BODY, fontName="Helvetica-Bold", fontSize=9.5, leading=12, spaceBefore=6, spaceAfter=3)
GREY = ParagraphStyle("grey", parent=BODY, textColor=colors.HexColor("#52514e"))
HEADER_BG = colors.HexColor("#243447")
GRID = colors.HexColor("#d6d4cc")


def _text(value) -> str:
    return html.escape(str(value)) if value not in (None, "") else "—"


def _bar(bar: dict, unmatched: bool) -> str:
    quantity = f"{bar['quantite']}-" if bar["quantite"] is not None else ""
    spacing = f" @{bar['espacement_mm']:g} mm" if bar["espacement_mm"] is not None else ""
    length = f" · L={bar['longueur_mm']:g} mm" if bar["longueur_mm"] is not None else ""
    role = f"{bar['role']} " if bar["role"] else ""
    text = html.escape(f"{role}{quantity}{bar['diametre'] or '?'}{spacing}{length}")
    # Bars the other reader does not have are shown in colour: they are the discrepancy.
    return f'<font color="{UNMATCHED}"><b>{text}</b></font>' if unmatched else text


def _bars(bars: list[dict], unmatched: list[dict], separator: str) -> str:
    missing = {id(bar) for bar in unmatched}
    return separator.join(_bar(bar, id(bar) in missing) for bar in bars) or "—"


def _sources(items: list[dict], field: str) -> str:
    return "<br/>".join(_text(v) for v in sorted({item[field] for item in items if item.get(field)})) or "—"


def _status(row: dict) -> Paragraph:
    return Paragraph(f"<b>{STATUS_LABELS[row['status']]}</b>", SMALL)


COORDINATE_NOTE = " Coordonnées rapprochées appariées"


def _reason(row: dict) -> str:
    """The reason without the grid-pairing sentence, which the element cell states instead."""
    return row["reason"].split(COORDINATE_NOTE)[0].strip()


def _element(row: dict, detailed: bool) -> Paragraph:
    notes = [_text(row["layer"])] if row["layer"] else []
    if not detailed and row["coordinate_match"] == "proche":
        notes.append(f"DA {_text(row['atelier_element'])}")
    extra = "".join(f"<br/><font size=6.5>{note}</font>" for note in notes)
    return Paragraph(f"<b>{_text(row['element'])}</b>{extra}", BODY)


def _motif(row: dict, detailed: bool) -> str:
    """Empty when the reason only restates the status (or, compact, the element cell says it)."""
    text = row["reason"] if detailed else _reason(row)
    return "" if text in GENERIC_REASONS else text


def _table(rows: list[dict], detailed: bool) -> Table:
    """Detailed: both sides, sources, and a motif line for each specific reason.
    Compact: one line per comparison; identical rows say so in the DA column."""
    if detailed:
        widths = [58, 92, 76, 196, 196, WIDTH - 618]
        header = ["Niveau", "Élément", "Statut", "Plan L2C", "Dessin d'atelier", "Sources (feuillet · fichier)"]
    else:
        header = ["Niveau", "Élément", "Statut", "Armatures : plan L2C", "Armatures : dessin d'atelier"]
        widths = [70, 92, 110, (WIDTH - 272) / 2, (WIDTH - 272) / 2]
    data = [[Paragraph(h, HEAD) for h in header]]
    spans, styles = [], []
    for row in rows:
        # Detailed rows stack bars for reading; compact rows keep them on one line for density.
        style, separator = (BODY, "<br/>") if detailed else (SMALL, " · ")
        plan = Paragraph(_bars(row["plan"], row["unmatched_plan"], separator), style)
        atelier = Paragraph(_bars(row["atelier"], row["unmatched_atelier"], separator), style)
        if not detailed and row["status"] == "same":
            atelier = Paragraph("<font color='#52514e'>identiques au plan</font>", SMALL)
        line = [Paragraph(_text(row["niveau"]), SMALL), _element(row, detailed), _status(row), plan, atelier]
        if detailed:
            sources = (f"<b>Plan</b> {_sources(row['plan_sources'], 'feuillet')}<br/>"
                       f"<b>DA</b> {_sources(row['atelier_sources'], 'fichier')}")
            line.append(Paragraph(sources, SMALL))
        data.append(line)
        index = len(data) - 1
        styles.append(("BACKGROUND", (2, index), (2, index), colors.HexColor(STATUS_COLORS[row["status"]])))
        motif = _motif(row, detailed)
        if motif:
            data.append([Paragraph(f"Motif : {_text(motif)}", MOTIF)] + [""] * (len(header) - 1))
            spans.append(("SPAN", (0, len(data) - 1), (-1, len(data) - 1)))
            styles.append(("TOPPADDING", (0, len(data) - 1), (-1, len(data) - 1), 0))
            styles.append(("BOTTOMPADDING", (0, index), (-1, index), 1))
    table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), .25, GRID),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        *spans, *styles,
    ]))
    return table


def _summary_table(rows: list[dict]) -> Table:
    """Counts per type and status, with the total for each type."""
    kinds = [kind for kind in TYPE_ORDER if any(r["type_element"] == kind for r in rows)]
    header = ["Type"] + [STATUS_LABELS[s] for s in STATUS_ORDER] + ["Total"]
    data = [[Paragraph(h, HEAD) for h in header]]
    for kind in kinds + ["__total__"]:
        group = rows if kind == "__total__" else [r for r in rows if r["type_element"] == kind]
        counts = Counter(r["status"] for r in group)
        label = "Total" if kind == "__total__" else TYPE_LABELS[kind]
        data.append([Paragraph(f"<b>{label}</b>", BODY)] +
                    [Paragraph(str(counts[s]), BODY) for s in STATUS_ORDER] +
                    [Paragraph(f"<b>{len(group)}</b>", BODY)])
    status_width = (WIDTH - 110 - 70) / len(STATUS_ORDER)
    table = Table(data, colWidths=[110] + [status_width] * len(STATUS_ORDER) + [70], repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("GRID", (0, 0), (-1, -1), .25, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 1), (-1, -1), "CENTER"),
        ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f3f2ee")),
        *[("BACKGROUND", (i, 1), (i, -2), colors.HexColor(STATUS_COLORS[s]))
          for i, s in enumerate(STATUS_ORDER, start=1)],
    ]))
    return table


def _legend_table() -> Table:
    data = [[Paragraph(f"<b>{STATUS_LABELS[s]}</b>", SMALL), Paragraph(STATUS_DEFINITIONS[s], SMALL)]
            for s in STATUS_ORDER]
    table = Table(data, colWidths=[118, WIDTH * 0.5 - 118], hAlign="LEFT")
    table.setStyle(TableStyle([
        *[("BACKGROUND", (0, i), (0, i), colors.HexColor(STATUS_COLORS[s]))
          for i, s in enumerate(STATUS_ORDER)],
        ("GRID", (0, 0), (-1, -1), .25, GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    return table


def _on_page(project: str, generated: str):
    def draw(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#52514e"))
        canvas.drawString(MARGIN, 7 * mm, f"L2C · rapport de comparaison · {project} · généré le {generated}")
        canvas.drawRightString(PAGE[0] - MARGIN, 7 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return draw


def _sheet_notes(plan, atelier) -> list[str]:
    notes = []
    unread = [s for s in atelier.sheets if s.status == "unread"]
    if unread:
        notes.append(f"{len(unread)} page(s) des dessins d'atelier non lue(s) : "
                     "leurs éléments n'ont pas été comparés.")
    skipped = [s for s in plan.sheets if s.status == "skipped"]
    if skipped:
        notes.append(f"{len(skipped)} feuillet(s) du plan hors périmètre (détails typiques, plans généraux) : non comparés.")
    return notes


def build_comparison_pdf(rows: list[dict], plan, atelier) -> bytes:
    """Return the comparison report as PDF bytes. ``plan`` and ``atelier`` are ``ProjectResult``."""
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    issues = [r for r in rows if r["status"] in ISSUE_STATUSES]
    others = [r for r in rows if r["status"] not in ISSUE_STATUSES]
    counts = Counter(r["status"] for r in rows)
    pages_read = sum(1 for s in atelier.sheets if s.status == "extracted")

    story = [
        Paragraph(f"Rapport de comparaison — {_text(plan.project)}", TITLE),
        Spacer(1, 3),
        Paragraph(f"Plan L2C : {_text(os.path.basename(plan.plan_file))} · "
                  f"Dessins d'atelier : {len(atelier.sheets)} page(s) traitée(s), {pages_read} lue(s) · "
                  f"{len(rows)} comparaisons · généré le {generated}", GREY),
        Spacer(1, 8),
        Paragraph("1. Synthèse", H2),
        _summary_table(rows),
        Spacer(1, 8),
    ]
    legend = [_legend_table()]
    notes = _sheet_notes(plan, atelier)
    notes_flow = [Paragraph(f"• {_text(note)}", SMALL) for note in notes] or \
        [Paragraph("• Toutes les pages du plan et des dessins d'atelier ont été traitées.", SMALL)]
    story += [KeepTogether([Paragraph("Définitions", H3), *legend]), Spacer(1, 4),
              KeepTogether([Paragraph("Couverture et limites", H3), *notes_flow]),
              Paragraph("Méthode : les éléments sont appariés par type, niveau, couche et coordonnée "
                        "exacte ; à défaut, par coordonnée voisine sur la grille (jusqu'à 4 positions). "
                        "Une armature présente d'un seul côté est affichée en rouge gras. "
                        "Les totaux comptent les groupes de données, pas les non-conformités confirmées.", GREY),
              PageBreak()]

    story.append(Paragraph(f"2. Écarts à traiter ({len(issues)})", H2))
    if not issues:
        story.append(Paragraph("Aucun écart détecté.", BODY))
    for kind in TYPE_ORDER:
        group = [r for r in issues if r["type_element"] == kind]
        if not group:
            continue
        group.sort(key=lambda r: STATUS_ORDER.index(r["status"]))
        story.append(Paragraph(f"{TYPE_LABELS[kind]} ({len(group)})", H3))
        story.append(_table(group, detailed=True))

    story += [PageBreak(), Paragraph(f"3. Autres comparaisons ({len(others)} : identiques et hors couverture)", H2),
              Paragraph(f"Identiques : {counts['same']} · Hors couverture DA : {counts['out_of_scope']}. "
                        "Ces lignes ne contiennent pas d'écart d'armature ; pour les éléments identiques, "
                        "la colonne DA renvoie au plan.", GREY)]
    for kind in TYPE_ORDER:
        group = [r for r in others if r["type_element"] == kind]
        if not group:
            continue
        group.sort(key=lambda r: STATUS_ORDER.index(r["status"]))
        story.append(Paragraph(f"{TYPE_LABELS[kind]} ({len(group)})", H3))
        story.append(_table(group, detailed=False))

    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=PAGE, leftMargin=MARGIN, rightMargin=MARGIN,
                            topMargin=MARGIN, bottomMargin=14 * mm,
                            title=f"Rapport de comparaison {plan.project}", author="L2C Review")
    doc.build(story, onFirstPage=_on_page(plan.project, generated),
              onLaterPages=_on_page(plan.project, generated))
    return buffer.getvalue()
