"""Dashboard components shared by the plan section and the shop-drawing section.

Both sides produce the same ``ProjectResult`` shape (``l2c.pipeline`` / ``l2c.da``), so
every view here takes a ``Dataset`` spec that only changes labels, the column the
per-unit views group by (plan: sheet, DA: file) and the JSON file name.
"""

from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass

import altair as alt
import pandas as pd
import streamlit as st

import theme
from l2c import io_json

TYPE_LABELS = {"colonne": "Colonnes", "semelle": "Semelles", "radier": "Radiers",
               "mur_refend": "Murs de refend", "poutre": "Poutres", "dalle": "Dalles"}
TIER_LABELS = {1: "texte", 2: "glyphes vectoriels", 3: "image (OCR)", 4: "illisible"}


def render_comparison(plan, atelier):
    from l2c.comparison import compare
    rows = compare(plan, atelier)
    labels = {"same": "Identique", "changed": "Armatures différentes",
              "missing_plan": "Absent du plan", "missing_da": "Absent des DA",
              "review": "À vérifier", "out_of_scope": "Hors couverture DA"}
    st.caption("Comparaison des données extraites par les deux lecteurs, par élément, niveau et couche. "
               "Les différences servent à corriger les lecteurs et à repérer les écarts à examiner.")
    plan_columns = [r for r in plan.records if r.type_element == 'colonne']
    da_columns = [r for r in atelier.records if r.type_element == 'colonne']
    if plan_columns or da_columns:
        st.caption(f"Colonnes — Plan : {len(plan_columns)} enregistrements sur "
                   f"{len({r.element for r in plan_columns if r.element != 'UNKNOWN'})} coordonnées ; "
                   f"DA : {len(da_columns)} enregistrements sur "
                   f"{len({r.element for r in da_columns if r.element != 'UNKNOWN'})} coordonnées. "
                   "L'unité commune est une coordonnée et un niveau. "
                   "Même format : quantité/diamètre des verticales et diamètre/espacement des étriers. "
                   "Les détails de fabrication restent dans les sources.")
        if atelier.meta.get('page_policy', {}).get('colonne', 'last' if atelier.meta.get('last_page_only') else '') == 'last' and da_columns:
            st.info("Colonnes : la lecture DA est limitée à la dernière page du tableau. "
                    "Les totaux ne représentent donc pas nécessairement les mêmes coordonnées que le plan complet.")
    counts = {status: sum(row['status'] == status for row in rows) for status in labels}
    for column, status in zip(st.columns(len(labels)), labels):
        column.metric(labels[status], counts[status])
    st.info("Radiers et types, niveaux ou couches sans données DA restent hors couverture. "
            "Les poutres nécessitent une vérification du placement des barres, même lorsque leurs valeurs concordent.")
    for sheet in atelier.sheets:
        if sheet.status in ("unread", "no_callouts"):
            st.warning(f"{sheet.fichier} : {sheet.reason or sheet.status}")
    chosen_statuses = st.multiselect("Résultats", list(labels),
        default=["changed", "missing_plan", "missing_da", "review"],
        format_func=labels.get, key="comparison_status")
    kinds = st.multiselect("Types", sorted({row['type_element'] for row in rows}),
                          format_func=lambda kind: TYPE_LABELS[kind], key="comparison_types")
    storeys = st.multiselect("Niveaux", sorted({row['niveau'] for row in rows}),
                            format_func=lambda text: text or "Non indiqué", key="comparison_levels")
    selected = [r for r in rows if r['status'] in chosen_statuses and
                (not kinds or r['type_element'] in kinds) and (not storeys or r['niveau'] in storeys)]

    def formatted(bars):
        values = []
        for bar in bars:
            quantity = f"{bar['quantite']}-" if bar['quantite'] is not None else ""
            spacing = f" @{bar['espacement_mm']:g} mm" if bar['espacement_mm'] is not None else ""
            length = f" · L={bar['longueur_mm']:g} mm" if bar['longueur_mm'] is not None else ""
            values.append(f"{bar['role'] or '?'}: {quantity}{bar['diametre'] or '?'}{spacing}{length}")
        return " · ".join(values) or "—"

    table = pd.DataFrame([dict(Type=TYPE_LABELS[r['type_element']], Élément=r['element'],
                              Niveau=r['niveau'], Couche=r['layer'], Statut=labels[r['status']],
                              Plan=formatted(r['plan']), DA=formatted(r['atelier']),
                              Motif=r['reason']) for r in selected])
    if selected:
        colors = {"Armatures différentes": "#ffe0da", "Absent du plan": "#ffe7ba",
                  "Absent des DA": "#fff3b0", "À vérifier": "#e1edff"}
        styled = table.style.apply(
            lambda row: [
                f"background-color: {colors.get(row.Statut, '#e4f3e6')}; color: #17212b"
            ] * len(row), axis=1,
        )
        st.dataframe(styled, hide_index=True, width="stretch")
        index = st.selectbox("Élément à examiner", range(len(selected)),
            format_func=lambda i: f"{TYPE_LABELS[selected[i]['type_element']]} · {selected[i]['element']} · "
                                  f"{selected[i]['niveau']} · {selected[i]['layer']} · {labels[selected[i]['status']]}",
            key="comparison_evidence")
        with st.expander("Sources et annotations des deux lecteurs"):
            for column, side, title in zip(st.columns(2), ("plan", "atelier"), ("Plan L2C", "Dessins d'atelier")):
                column.write(title)
                column.json({"armatures": selected[index][side],
                             "sources": selected[index][f'{side}_sources'],
                             "non_appariées": selected[index][f'unmatched_{side}']})
    else:
        st.info("Aucun résultat pour ces filtres.")
    st.download_button("Télécharger la comparaison JSON", json.dumps(rows, ensure_ascii=False, indent=2),
                       file_name=f"{plan.project}_comparaison.json", mime="application/json")


@dataclass(frozen=True)
class Dataset:
    key: str              # "plan" | "atelier"
    unit: str             # column the per-unit views group by
    unit_label: str       # singular, for filters
    units_label: str      # plural, for counts and the tab name
    json_name: str
    done_label: str       # "Feuillets traités" / "Pages traitées" (gender agreement)
    noun: str             # what the hero number counts, in a sentence
    scope_md: str


PLAN = Dataset(
    key="plan", unit="feuillet", unit_label="Feuillet", units_label="Feuillets",
    json_name="elements_plan.json", done_label="Feuillets traités", noun="éléments d'armature extraits du plan",
    scope_md=(
        "- ✅ Extraction côté **plan**, tous les types : radiers (S-050), semelles (S-100), "
        "poutres (S-300), murs de refend (S-400), colonnes (S-500), dalles (S-600)\n"
        "- ✅ JSON conforme à l'annexe A + manifeste de run\n"
        "- ✅ Comparaison des extractions dans la section Comparaison\n"
        "- ⬜ Classement des non-conformités et rapport final\n"
        "- ⬜ Rapport PDF\n\n"
        "Les feuillets non traités sont listés explicitement plutôt que comptés à zéro."
    ),
)

ATELIER = Dataset(
    key="atelier", unit="fichier", unit_label="Fichier", units_label="Pages",
    json_name="elements_atelier.json", done_label="Pages traitées", noun="éléments d'armature extraits des dessins d'atelier",
    scope_md=(
        "Avancement détaillé : **DA_PLAN.md**.\n\n"
        "- ✅ Cinq lecteurs CLP : colonnes, dalles, semelles, poutres et radiers\n"
        "- ✅ Colonnes : Partie 3 complet + sous-sol Partie 1 page 5 ; radiers : fichier complet ; autres types : dernière page\n"
        "- ✅ Nettoyage des résultats, dédoublonnage et JSON annexe A\n"
        "- ⬜ Autres fichiers, projets et sélection directe des PDF dans l'interface\n\n"
        "Les lecteurs DA sont **aveugles** : ils ne voient jamais les valeurs du plan. "
        "Un fichier non lu est listé avec son motif."
    ),
)


# ----------------------------------------------------------------- frames
def bars(armature) -> str:
    out = []
    for a in armature:
        if a.quantite and a.diametre:
            out.append(f"{a.quantite}-{a.diametre}")
        elif a.espacement_mm and a.diametre:
            out.append(f"{a.diametre}@{a.espacement_mm:.0f}mm")
        elif a.diametre:
            out.append(a.diametre)
    return " · ".join(out)


def detail(d) -> str:
    extra = d.model_extra or {}
    bits = []
    if extra.get("integrity_type"):
        bits.append(f"intégrité {extra['integrity_type']}")
    if extra.get("layer") and extra["layer"] != "intégrité":
        bits.append(f"rang {extra['layer']}")
    if extra.get("role"):
        bits.append(extra["role"])
    if extra.get("grid_line"):
        bits.append(f"axe {extra['grid_line']}")
    if extra.get("position"):
        bits.append(f"axes {extra['position']}")
    if extra.get("direction"):
        bits.append(extra["direction"])
    return ", ".join(bits)


def record_bars(record) -> str:
    roles = (record.debug.model_extra or {}).get("roles")
    if roles and len(roles) == len(record.armature) and all(roles):
        return " · ".join(f"{role}: {bars([bar])}" for role, bar in zip(roles, record.armature))
    return bars(record.armature)


def frames(result) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(elements, units) tables - identical columns for both sides where meaningful."""
    rows = [
        {
            "fichier": r.fichier, "feuillet": r.feuillet, "page": r.page, "element": r.element,
            "type": r.type_element, "niveau": r.debug.niveau or "",
            "armature": record_bars(r),
            "repère": " · ".join(a.repere for a in r.armature if a.repere),
            "détail": detail(r.debug),
            "x": round(r.x, 1), "y": round(r.y, 1),
            "confiance": r.debug.confidence,
            "localisé": r.element != "UNKNOWN",
            "source": " · ".join(r.debug.raw),
        }
        for r in result.records
    ]
    units = [
        {"fichier": s.fichier or result.plan_file, "feuillet": s.feuillet, "page": s.page,
         "dossier": s.diagnostics.get("folder", ""),
         "type": s.type_element or "—", "niveau": s.niveau or "—",
         "lecture": TIER_LABELS.get(s.tier, "texte") if s.tier else "texte",
         "éléments": s.records, "localisés": s.located,
         "statut": s.status, "motif": s.reason or "",
         "avertissements": len(s.diagnostics.get("warnings", [])),
         "grille": s.diagnostics.get("source", s.diagnostics.get("letter_role", "")) or "",
         "échelle_pt_par_pouce": s.diagnostics.get("scale_pt_per_inch")}
        for s in result.sheets
    ]
    unit_columns = ['fichier', 'feuillet', 'page', 'dossier', 'type', 'niveau', 'lecture',
                    'éléments', 'localisés', 'statut', 'motif', 'avertissements', 'grille',
                    'échelle_pt_par_pouce']
    return pd.DataFrame(rows), pd.DataFrame(units, columns=unit_columns)


# ----------------------------------------------------------------- views
def kpi_row(spec: Dataset, result, df: pd.DataFrame) -> None:
    t = result.totals
    st.markdown(
        f"<div style='font-size:3rem;line-height:1.1;font-weight:600;color:{theme.INK}'>"
        f"{t['elements']:,}</div>"
        f"<div style='color:{theme.INK_2};margin-bottom:.75rem'>"
        f"{spec.noun} — {result.project}, {result.plan_file}</div>",
        unsafe_allow_html=True,
    )
    by_type = df.type.value_counts() if not df.empty else {}
    tc = st.columns(len(TYPE_LABELS))
    for col, (k, label) in zip(tc, TYPE_LABELS.items()):
        col.metric(label, "À venir" if k in result.meta.get("pending_types", [])
                   else f"{int(by_type.get(k, 0)):,}")
    beam_entries = sum(len(r.armature) for r in result.records if r.type_element == "poutre")
    st.caption(f"Poutres : {int(by_type.get('poutre', 0))} éléments · {beam_entries} entrées d'armature. "
               "Chaque poutre regroupe toutes ses annotations dans un même enregistrement.")
    columns = [r for r in result.records if r.type_element == 'colonne']
    if columns:
        st.caption(f"Colonnes : {len(columns)} enregistrements coordonnée/niveau sur "
                   f"{len({r.element for r in columns if r.element != 'UNKNOWN'})} coordonnées de grille.")
    c = st.columns(5)
    pct = 100 * t["located"] / t["elements"] if t["elements"] else 0
    c[0].metric("Localisés sur la grille", f"{t['located']:,}", f"{pct:.1f} %")
    c[1].metric(spec.done_label, t["sheets_extracted"], f"{t['sheets']} au total")
    c[2].metric("Confiance moyenne", f"{t['mean_confidence']:.3f}")
    c[3].metric("Système d'unités", result.unit_system)
    c[4].metric("Durée", f"{result.elapsed_s:.2f} s")


def _per_unit_chart(spec: Dataset, units: pd.DataFrame) -> None:
    ex = units[units.statut == "extracted"].copy()
    if ex.empty:
        st.info(f"Aucun {spec.unit_label.lower()} traité pour l'instant.")
        return
    ex = ex.groupby(spec.unit, sort=False)[["éléments", "localisés"]].sum().reset_index()
    ex["non localisés"] = ex["éléments"] - ex["localisés"]
    long = ex.melt(id_vars=spec.unit, value_vars=["localisés", "non localisés"],
                   var_name="état", value_name="n")
    chart = (
        alt.Chart(long)
        .mark_bar(cornerRadiusEnd=4, height=14)
        .encode(
            y=alt.Y(f"{spec.unit}:N", sort=list(ex[spec.unit]), title=None),
            x=alt.X("n:Q", title="éléments", stack=True),
            color=alt.Color(
                "état:N",
                scale=alt.Scale(domain=["localisés", "non localisés"],
                                range=[theme.MUTED, theme.CRITICAL]),
                legend=alt.Legend(title=None, orient="top"),
            ),
            tooltip=[spec.unit, "état", "n"],
        )
        .properties(height=max(150, 26 * len(ex)))
    )
    st.altair_chart(chart, width="stretch")


def _tier_chart(units: pd.DataFrame) -> None:
    """DA only: pages per folder, by how they can be read."""
    agg = units.groupby(["dossier", "lecture"]).size().reset_index(name="pages")
    order = [TIER_LABELS[k] for k in sorted(TIER_LABELS)]
    chart = (
        alt.Chart(agg)
        .mark_bar(cornerRadiusEnd=4, height=14)
        .encode(
            y=alt.Y("dossier:N", title=None),
            x=alt.X("pages:Q", title="pages", stack=True),
            color=alt.Color("lecture:N",
                            scale=alt.Scale(domain=order, range=theme.TIER_RANGE),
                            legend=alt.Legend(title=None, orient="top")),
            tooltip=["dossier", "lecture", "pages"],
        )
        .properties(height=max(120, 30 * agg.dossier.nunique()))
    )
    st.altair_chart(chart, width="stretch")


def overview(spec: Dataset, df: pd.DataFrame, units: pd.DataFrame) -> None:
    left, right = st.columns([3, 2])
    with left:
        st.caption(f"Éléments par {spec.unit_label.lower()} — en rouge ceux qui n'ont pas pu "
                   "être localisés")
        _per_unit_chart(spec, units)
    with right:
        if spec.key == "atelier":
            st.caption("Pages par dossier, selon leur mode de lecture")
            _tier_chart(units)
        st.caption("Distribution de la confiance d'extraction")
        if df.empty:
            st.info("Aucun élément.")
        else:
            hist = (
                alt.Chart(df)
                .mark_bar(cornerRadiusEnd=3)
                .encode(
                    x=alt.X("confiance:Q", bin=alt.Bin(maxbins=24), title="confiance"),
                    y=alt.Y("count():Q", title="éléments"),
                    color=alt.value(theme.ACCENT),
                    tooltip=["count()"],
                )
                .properties(height=240)
            )
            st.altair_chart(hist, width="stretch")


def elements(spec: Dataset, df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Aucun élément extrait.")
        return
    f = st.columns([1, 1, 1, 1, 2])
    kinds = f[0].multiselect("Type", sorted(df.type.unique()),
                             format_func=lambda k: TYPE_LABELS.get(k, k), key=f"{spec.key}_type")
    unit = f[1].multiselect(spec.unit_label, sorted(df[spec.unit].unique()), key=f"{spec.key}_unit")
    niveau = f[2].multiselect("Niveau", sorted(x for x in df.niveau.unique() if x),
                              key=f"{spec.key}_niv")
    only_unloc = f[3].checkbox("Non localisés seulement", key=f"{spec.key}_unloc")
    thr = f[4].slider("Confiance minimale", 0.0, 1.0, 0.0, 0.01, key=f"{spec.key}_thr")

    view = df
    if kinds:
        view = view[view.type.isin(kinds)]
    if unit:
        view = view[view[spec.unit].isin(unit)]
    if niveau:
        view = view[view.niveau.isin(niveau)]
    if only_unloc:
        view = view[~view["localisé"]]
    view = view[view.confiance >= thr].sort_values(["confiance", spec.unit, "element"])
    drop = [c for c in ("repère", "fichier") if c in view and not view[c].astype(bool).any()]
    if spec.key == "plan":
        drop.append("fichier")
    view = view.drop(columns=sorted(set(drop)))

    st.caption(f"{len(view):,} / {len(df):,} éléments — triés par confiance croissante "
               "(les cas les plus incertains en premier)")
    st.dataframe(
        view, width="stretch", hide_index=True, height=440,
        column_config={
            "confiance": st.column_config.ProgressColumn("confiance", min_value=0.0,
                                                         max_value=1.0, format="%.3f"),
            "localisé": st.column_config.CheckboxColumn("localisé"),
            "source": st.column_config.TextColumn("texte source", width="medium"),
        },
    )


def units_table(spec: Dataset, units: pd.DataFrame) -> None:
    if spec.key == "plan":
        st.caption("Chaque feuillet du jeu de plans, y compris ceux hors périmètre.")
        cols = ["feuillet", "page", "type", "niveau", "éléments", "localisés", "statut",
                "motif", "avertissements", "grille", "échelle_pt_par_pouce"]
    else:
        st.caption("Dernière page de chaque fichier configuré, avec son état de lecture.")
        cols = ["dossier", "fichier", "page", "type", "lecture", "feuillet", "éléments",
                "localisés", "statut", "motif", "avertissements"]
    show = units[[c for c in cols if c in units]].copy()
    show["statut"] = show.statut.map(
        lambda s: f"{theme.STATUS.get(s, ('', '', s))[1]} {theme.STATUS.get(s, ('', '', s))[2]}"
    )
    st.dataframe(show, width="stretch", hide_index=True, height=520)


def diagnostics(spec: Dataset, result, units: pd.DataFrame) -> None:
    st.caption("Ce que le système sait ne pas savoir.")
    warn = [(s.fichier or s.feuillet, s.feuillet, w) for s in result.sheets
            for w in s.diagnostics.get("warnings", [])]
    c = st.columns(3)
    c[0].metric("Avertissements", len(warn))
    if spec.key == "plan":
        c[1].metric("Feuillets hors périmètre", int((units.statut == "skipped").sum()))
        c[2].metric("Échelle (pt/pouce)",
                    f"{units['échelle_pt_par_pouce'].dropna().median():.3f}"
                    if units["échelle_pt_par_pouce"].notna().any() else "—")
    else:
        c[1].metric("Pages non lues", int((units.statut == "unread").sum()),
                    f"sur {len(units)}", delta_color="off")
        tiers = result.meta.get("tiers", {})
        c[2].metric("Pages lues par OCR", int((units.statut == "extracted").sum()),
                    f"{tiers.get(3, 0)} pages configurées", delta_color="off")
    if warn:
        cols = ["fichier", "feuillet", "avertissement"]
        frame = pd.DataFrame(warn, columns=cols)
        if spec.key == "plan":
            frame = frame.drop(columns="fichier")
        st.dataframe(frame, width="stretch", hide_index=True)
    st.markdown("**Détection des unités** — "
                f"`{result.unit_system}`, preuve : `{result.unit_evidence}`")
    if spec.key == "atelier":
        labels = result.meta.get("labels") or {}
        st.markdown("**Dialecte du fabricant** — étiquettes rencontrées : "
                    + (", ".join(f"`{k}` ×{v}" for k, v in labels.items()) or "aucune pour l'instant"))
        unread = units[units.statut == "unread"].groupby("motif").size().reset_index(name="pages")
        if not unread.empty:
            st.markdown("**Pages non lues, par motif**")
            st.dataframe(unread, width="stretch", hide_index=True)
    with st.expander("Périmètre de cette étape"):
        st.markdown(spec.scope_md)


def downloads(spec: Dataset, result, df: pd.DataFrame, units: pd.DataFrame) -> None:
    records = io_json.dump_records(result.records)
    payload = json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True)
    st.caption("JSON annexe A des éléments extraits, avec exports CSV et manifeste.")
    c = st.columns(3)
    stem = spec.json_name.removesuffix(".json")
    c[0].download_button(spec.json_name, payload,
                         file_name=f"{result.project}_{spec.json_name}",
                         mime="application/json", width="stretch", key=f"{spec.key}_dl_json")
    c[1].download_button("éléments (CSV)", df.to_csv(index=False).encode("utf-8"),
                         file_name=f"{result.project}_{stem}.csv",
                         mime="text/csv", width="stretch", key=f"{spec.key}_dl_csv")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(spec.json_name, payload)
        zf.writestr(f"{stem}.csv", df.to_csv(index=False))
        zf.writestr(f"{spec.units_label.lower()}.csv", units.to_csv(index=False))
        zf.writestr("run_manifest.json", json.dumps({
            "pipeline_version": result.meta.get("parser_version", io_json.PIPELINE_VERSION),
            "side": spec.key, "project": result.project, "source": result.plan_file,
            "unit_system": result.unit_system, "unit_evidence": result.unit_evidence,
            "totals": result.totals, "elapsed_s": result.elapsed_s, "meta": result.meta,
        }, ensure_ascii=False, indent=2, sort_keys=True, default=str))
    name = f"{result.project}_{spec.key}_l2c_review.zip"
    c[2].download_button(name, buf.getvalue(), file_name=name, mime="application/zip",
                         width="stretch", key=f"{spec.key}_dl_zip")

    with st.expander("Aperçu du JSON (3 premiers enregistrements)"):
        st.code(json.dumps(records[:3], ensure_ascii=False, indent=2), language="json")


def render(spec: Dataset, result) -> None:
    """The whole section: KPI row + the five sub-tabs."""
    df, units = frames(result)
    kpi_row(spec, result, df)
    st.divider()
    tabs = st.tabs(["Vue d'ensemble", "Éléments", spec.units_label, "Diagnostics",
                    "Téléchargements"])
    with tabs[0]:
        overview(spec, df, units)
    with tabs[1]:
        elements(spec, df)
    with tabs[2]:
        units_table(spec, units)
    with tabs[3]:
        diagnostics(spec, result, units)
    with tabs[4]:
        downloads(spec, result, df, units)
