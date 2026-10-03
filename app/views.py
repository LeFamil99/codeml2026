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
        "- ⬜ Appariement plan ↔ atelier et classement des non-conformités\n"
        "- ⬜ Rapport PDF\n\n"
        "Les feuillets non traités sont listés explicitement plutôt que comptés à zéro."
    ),
)

ATELIER = Dataset(
    key="atelier", unit="fichier", unit_label="Fichier", units_label="Pages",
    json_name="elements_atelier.json", done_label="Pages traitées", noun="éléments d'armature extraits des dessins d'atelier",
    scope_md=(
        "Avancement détaillé : **DA_PLAN.md**.\n\n"
        "- ✅ Inventaire des fichiers DA, type d'élément et niveau de lecture par page\n"
        "- ✅ Lecture des pages avec couche texte (CLP : colonnes, dalles, semelles, radier, "
        "poutres ; LIGREP : colonnes)\n"
        "- ⬜ Décodeur de glyphes vectoriels (340 pages sans texte)\n"
        "- ⬜ Repérage sur la grille des pages décodées\n"
        "- ⬜ Dialectes des autres fabricants\n\n"
        "Le lecteur DA est **aveugle** : il ne voit jamais les valeurs du plan. "
        "Une page non lue est listée avec son motif, jamais comptée comme conforme."
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
    if extra.get("layer"):
        bits.append(f"rang {extra['layer']}")
    if extra.get("role"):
        bits.append(extra["role"])
    if extra.get("grid_line"):
        bits.append(f"axe {extra['grid_line']}")
    if extra.get("direction"):
        bits.append(extra["direction"])
    return ", ".join(bits)


def frames(result) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(elements, units) tables - identical columns for both sides where meaningful."""
    rows = [
        {
            "fichier": r.fichier, "feuillet": r.feuillet, "page": r.page, "element": r.element,
            "type": r.type_element, "niveau": r.debug.niveau or "",
            "armature": bars(r.armature),
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
    return pd.DataFrame(rows), pd.DataFrame(units)


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
        col.metric(label, f"{int(by_type.get(k, 0)):,}")
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
        st.caption("Chaque page de chaque dessin d'atelier, avec son mode de lecture.")
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
        c[2].metric("Pages avec couche texte", tiers.get(1, 0),
                    f"{tiers.get(2, 0)} en glyphes vectoriels", delta_color="off")
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
    st.caption("Les artefacts sont identiques à ceux produits par `l2c run`.")
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
            "pipeline_version": io_json.PIPELINE_VERSION,
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
