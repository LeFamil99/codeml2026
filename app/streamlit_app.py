"""L2C review dashboard - plan side, whole plan set.

Scope: every element type on the plan (columns, footings, radier, walls, beams, slabs)
-> JSON -> download. Shop-drawing decoding, matching and the conformity report are
later stages and the UI says so explicitly rather than showing empty panels.

Run:  streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import altair as alt
import pandas as pd
import streamlit as st

import theme
from l2c import io_json
from l2c.pipeline import find_plan, run_plan

st.set_page_config(page_title="Révision L2C", page_icon="📐", layout="wide")

DEFAULT_CORPUS = os.path.expanduser("~/Downloads/l2c-participants")


# ----------------------------------------------------------------- intake
def _session_dir() -> str:
    if "session_dir" not in st.session_state:
        st.session_state.session_dir = tempfile.mkdtemp(prefix="l2c_")
    return st.session_state.session_dir


def _projects_under(path: str) -> list[str]:
    """Sub-folders of `path` that are themselves projects."""
    out = []
    for name in sorted(os.listdir(path)):
        d = os.path.join(path, name)
        if os.path.isdir(d) and find_plan(d):
            out.append(name)
    return out


def _from_folder() -> tuple[str, str] | None:
    path = st.sidebar.text_input(
        "Dossier des projets",
        DEFAULT_CORPUS,
        help="Le dossier qui contient les projets (ou directement le dossier d'un projet)",
    ).strip().rstrip("/")
    if not path:
        return None
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        st.sidebar.error("Dossier introuvable.")
        return None

    # the folder is itself a project
    if plan := find_plan(path):
        st.sidebar.success(f"Projet : **{os.path.basename(path)}**")
        return os.path.basename(path), plan

    # a folder of projects: pick ONE from the dropdown
    children = _projects_under(path)
    if not children:
        st.sidebar.error("Aucun L2C_PLAN_STR_*.pdf dans ce dossier ni dans ses sous-dossiers.")
        return None
    chosen = st.sidebar.selectbox("Projet", children)
    return chosen, find_plan(os.path.join(path, chosen))


def _from_zip() -> tuple[str, str] | None:
    up = st.sidebar.file_uploader("Archive d'un projet (.zip)", type="zip")
    if up is None:
        return None
    target = os.path.join(_session_dir(), up.name[:-4])
    if not os.path.isdir(target):
        with st.spinner("Extraction de l'archive…"):
            with zipfile.ZipFile(io.BytesIO(up.getvalue())) as zf:
                zf.extractall(target)
    plans = [
        (f.replace("L2C_PLAN_STR_", "")[:-4], os.path.join(dp, f))
        for dp, _, files in os.walk(target)
        for f in files
        if f.startswith("L2C_PLAN_STR") and f.endswith(".pdf")
    ]
    if not plans:
        st.sidebar.error("Aucun L2C_PLAN_STR_*.pdf dans l'archive.")
        return None
    if len(plans) == 1:
        st.sidebar.success(f"Projet : **{plans[0][0]}**")
        return plans[0]
    st.sidebar.info(f"{len(plans)} projets dans l'archive — choisissez-en un.")
    name = st.sidebar.selectbox("Projet", [p[0] for p in plans])
    return name, dict(plans)[name]


def intake() -> tuple[str, str] | None:
    """Pick exactly ONE project to analyse."""
    st.sidebar.header("Projet à analyser")
    mode = st.sidebar.radio("Source", ["Dossier", "Archive ZIP"],
                            label_visibility="collapsed", horizontal=True)
    return _from_folder() if mode == "Dossier" else _from_zip()


# ----------------------------------------------------------------- run
TYPE_LABELS = {"colonne": "Colonnes", "semelle": "Semelles", "radier": "Radiers",
               "mur_refend": "Murs de refend", "poutre": "Poutres", "dalle": "Dalles"}


def _bars(armature) -> str:
    out = []
    for a in armature:
        if a.quantite and a.diametre:
            out.append(f"{a.quantite}-{a.diametre}")
        elif a.espacement_mm and a.diametre:
            out.append(f"{a.diametre}@{a.espacement_mm:.0f}mm")
        elif a.diametre:
            out.append(a.diametre)
    return " · ".join(out)


def _detail(d) -> str:
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


@st.cache_data(show_spinner=False)
def _run(plan_path: str, mtime: float):
    result = run_plan(plan_path)
    rows = [
        {
            "feuillet": r.feuillet, "page": r.page, "element": r.element,
            "type": r.type_element, "niveau": r.debug.niveau or "",
            "armature": _bars(r.armature),
            "détail": _detail(r.debug),
            "x": round(r.x, 1), "y": round(r.y, 1),
            "confiance": r.debug.confidence,
            "localisé": r.element != "UNKNOWN",
            "source": " · ".join(r.debug.raw),
        }
        for r in result.records
    ]
    sheets = [
        {"feuillet": s.feuillet, "page": s.page, "type": s.type_element or "—",
         "niveau": s.niveau or "—", "éléments": s.records, "localisés": s.located,
         "statut": s.status, "motif": s.reason or "",
         "avertissements": len(s.diagnostics.get("warnings", [])),
         "grille": s.diagnostics.get("source", s.diagnostics.get("letter_role", "")),
         "échelle_pt_par_pouce": s.diagnostics.get("scale_pt_per_inch")}
        for s in result.sheets
    ]
    return result, pd.DataFrame(rows), pd.DataFrame(sheets)


# ----------------------------------------------------------------- views
def kpi_row(result, df: pd.DataFrame) -> None:
    t = result.totals
    st.markdown(
        f"<div style='font-size:3rem;line-height:1.1;font-weight:600;color:{theme.INK}'>"
        f"{t['elements']:,}</div>"
        f"<div style='color:{theme.INK_2};margin-bottom:.75rem'>"
        f"éléments d'armature extraits — {result.project}, {result.plan_file}</div>",
        unsafe_allow_html=True,
    )
    by_type = df.type.value_counts() if not df.empty else {}
    tc = st.columns(len(TYPE_LABELS))
    for col, (k, label) in zip(tc, TYPE_LABELS.items()):
        col.metric(label, f"{int(by_type.get(k, 0)):,}")
    c = st.columns(5)
    pct = 100 * t["located"] / t["elements"] if t["elements"] else 0
    c[0].metric("Localisés sur la grille", f"{t['located']:,}", f"{pct:.1f} %")
    c[1].metric("Feuillets traités", t["sheets_extracted"], f"{t['sheets']} au total")
    c[2].metric("Confiance moyenne", f"{t['mean_confidence']:.3f}")
    c[3].metric("Système d'unités", result.unit_system)
    c[4].metric("Durée", f"{result.elapsed_s:.2f} s")


def overview_charts(df: pd.DataFrame, sheets: pd.DataFrame) -> None:
    left, right = st.columns([3, 2])

    with left:
        st.caption("Éléments par feuillet — en rouge ceux qui n'ont pas pu être localisés")
        ex = sheets[sheets.statut == "extracted"].copy()
        if ex.empty:
            st.info("Aucun feuillet traité.")
        else:
            ex["non localisés"] = ex["éléments"] - ex["localisés"]
            long = ex.melt(id_vars="feuillet", value_vars=["localisés", "non localisés"],
                           var_name="état", value_name="n")
            chart = (
                alt.Chart(long)
                .mark_bar(cornerRadiusEnd=4, height=14)
                .encode(
                    y=alt.Y("feuillet:N", sort=list(ex.feuillet), title=None),
                    x=alt.X("n:Q", title="éléments", stack=True),
                    color=alt.Color(
                        "état:N",
                        scale=alt.Scale(domain=["localisés", "non localisés"],
                                        range=[theme.MUTED, theme.CRITICAL]),
                        legend=alt.Legend(title=None, orient="top"),
                    ),
                    tooltip=["feuillet", "état", "n"],
                )
                .properties(height=max(150, 26 * len(ex)))
            )
            st.altair_chart(chart, width="stretch")

    with right:
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


def elements_tab(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Aucun élément extrait.")
        return
    f = st.columns([1, 1, 1, 1, 2])
    kinds = f[0].multiselect("Type", sorted(df.type.unique()),
                             format_func=lambda k: TYPE_LABELS.get(k, k))
    sheet = f[1].multiselect("Feuillet", sorted(df.feuillet.unique()))
    niveau = f[2].multiselect("Niveau", sorted(x for x in df.niveau.unique() if x))
    only_unloc = f[3].checkbox("Non localisés seulement")
    thr = f[4].slider("Confiance minimale", 0.0, 1.0, 0.0, 0.01)

    view = df
    if kinds:
        view = view[view.type.isin(kinds)]
    if sheet:
        view = view[view.feuillet.isin(sheet)]
    if niveau:
        view = view[view.niveau.isin(niveau)]
    if only_unloc:
        view = view[~view["localisé"]]
    view = view[view.confiance >= thr].sort_values(["confiance", "feuillet", "element"])

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


def sheets_tab(sheets: pd.DataFrame) -> None:
    st.caption("Chaque feuillet du jeu de plans, y compris ceux hors périmètre de cette étape.")
    show = sheets.copy()
    show["statut"] = show.statut.map(
        lambda s: f"{theme.STATUS.get(s, ('', '', s))[1]} {theme.STATUS.get(s, ('', '', s))[2]}"
    )
    st.dataframe(show, width="stretch", hide_index=True, height=520)


def diagnostics_tab(result, sheets: pd.DataFrame) -> None:
    st.caption("Ce que le système sait ne pas savoir.")
    warn = [(s.feuillet, w) for s in result.sheets
            for w in s.diagnostics.get("warnings", [])]
    c = st.columns(3)
    c[0].metric("Avertissements", len(warn))
    c[1].metric("Feuillets hors périmètre", int((sheets.statut == "skipped").sum()))
    c[2].metric("Échelle (pt/pouce)",
                f"{sheets['échelle_pt_par_pouce'].dropna().median():.3f}"
                if sheets["échelle_pt_par_pouce"].notna().any() else "—")
    if warn:
        st.dataframe(pd.DataFrame(warn, columns=["feuillet", "avertissement"]),
                     width="stretch", hide_index=True)
    st.markdown("**Détection des unités** — "
                f"`{result.unit_system}`, preuve : `{result.unit_evidence}`")
    with st.expander("Périmètre de cette étape"):
        st.markdown(
            "- ✅ Extraction côté **plan**, tous les types : radiers (S-050), semelles (S-100), "
            "poutres (S-300), murs de refend (S-400), colonnes (S-500), dalles (S-600)\n"
            "- ✅ JSON conforme à l'annexe A + manifeste de run\n"
            "- ⬜ Lecture des **dessins d'atelier** (décodeur de glyphes)\n"
            "- ⬜ Appariement plan ↔ atelier et classement des non-conformités\n"
            "- ⬜ Rapport PDF\n\n"
            "Les feuillets non traités sont listés explicitement plutôt que comptés à zéro."
        )


def downloads_tab(result, df: pd.DataFrame) -> None:
    records = io_json.dump_records(result.records)
    payload = json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True)
    st.caption("Les artefacts sont identiques à ceux produits par `l2c run`.")
    c = st.columns(3)
    c[0].download_button("elements_plan.json", payload,
                         file_name=f"{result.project}_elements_plan.json",
                         mime="application/json", width="stretch")
    c[1].download_button("éléments (CSV)", df.to_csv(index=False).encode("utf-8"),
                         file_name=f"{result.project}_elements.csv",
                         mime="text/csv", width="stretch")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("elements_plan.json", payload)
        zf.writestr("elements.csv", df.to_csv(index=False))
        zf.writestr("run_manifest.json", json.dumps({
            "pipeline_version": "0.2.0-plan",
            "project": result.project, "plan_file": result.plan_file,
            "unit_system": result.unit_system, "unit_evidence": result.unit_evidence,
            "totals": result.totals, "elapsed_s": result.elapsed_s,
            "scope": "plan side, all element types",
        }, ensure_ascii=False, indent=2, sort_keys=True))
    c[2].download_button(f"{result.project}_l2c_review.zip", buf.getvalue(),
                         file_name=f"{result.project}_l2c_review.zip",
                         mime="application/zip", width="stretch")

    with st.expander("Aperçu du JSON (3 premiers enregistrements)"):
        st.code(json.dumps(records[:3], ensure_ascii=False, indent=2), language="json")


# ----------------------------------------------------------------- main
st.title("Révision des dessins d'atelier — L2C")
st.caption("Étape 1 : extraction côté plan (tous les types d'éléments) et base JSON. "
           "La lecture des dessins d'atelier et la comparaison viennent ensuite.")

selection = intake()
if selection is None:
    st.info("Indiquez le dossier d'un projet (ou téléversez son archive) pour commencer.")
    st.stop()

chosen, plan_path = selection
st.sidebar.caption(f"`{os.path.basename(plan_path)}`")
if st.sidebar.button("Vider le cache"):
    st.cache_data.clear()

with st.spinner(f"Extraction de {chosen}…"):
    result, df, sheets = _run(plan_path, os.path.getmtime(plan_path))

kpi_row(result, df)
st.divider()
tabs = st.tabs(["Vue d'ensemble", "Éléments", "Feuillets", "Diagnostics", "Téléchargements"])
with tabs[0]:
    overview_charts(df, sheets)
with tabs[1]:
    elements_tab(df)
with tabs[2]:
    sheets_tab(sheets)
with tabs[3]:
    diagnostics_tab(result, sheets)
with tabs[4]:
    downloads_tab(result, df)
