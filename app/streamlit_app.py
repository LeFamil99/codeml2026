"""L2C review dashboard - thin vertical slice.

Scope: plan-side column extraction -> JSON -> download. Shop-drawing decoding,
matching and the conformity report are NOT in this slice and the UI says so
explicitly rather than showing empty panels.

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


def intake() -> list[tuple[str, str]]:
    """Return [(project_name, plan_path)] from a local folder or an uploaded ZIP."""
    st.sidebar.header("Données")
    mode = st.sidebar.radio("Source", ["Dossier local", "Archive ZIP"], label_visibility="collapsed")
    found: list[tuple[str, str]] = []

    if mode == "Dossier local":
        root = st.sidebar.text_input("Racine du corpus", DEFAULT_CORPUS)
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                d = os.path.join(root, name)
                if os.path.isdir(d) and (plan := find_plan(d)):
                    found.append((name, plan))
            if not found:
                st.sidebar.warning("Aucun fichier L2C_PLAN_STR_*.pdf trouvé.")
        else:
            st.sidebar.error("Dossier introuvable.")
    else:
        up = st.sidebar.file_uploader("Archive d'un projet (.zip)", type="zip")
        if up is not None:
            target = os.path.join(_session_dir(), up.name.replace(".zip", ""))
            if not os.path.isdir(target):
                with zipfile.ZipFile(io.BytesIO(up.getvalue())) as zf:
                    zf.extractall(target)
            for dirpath, _, files in os.walk(target):
                for f in files:
                    if f.startswith("L2C_PLAN_STR") and f.endswith(".pdf"):
                        found.append((f.replace("L2C_PLAN_STR_", "").replace(".pdf", ""),
                                      os.path.join(dirpath, f)))
    return found


# ----------------------------------------------------------------- run
@st.cache_data(show_spinner=False)
def _run(plan_path: str, mtime: float):
    result = run_plan(plan_path)
    rows = [
        {
            "feuillet": r.feuillet, "page": r.page, "element": r.element,
            "type": r.type_element, "niveau": r.debug.niveau or "",
            "barres": next((f"{a.quantite}-{a.diametre}" for a in r.armature
                            if a.quantite and a.diametre), ""),
            "étriers": next((f"{a.diametre}@{a.espacement_mm:.0f}mm" for a in r.armature
                             if a.espacement_mm), ""),
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
            st.altair_chart(chart, use_container_width=True)

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
            st.altair_chart(hist, use_container_width=True)


def elements_tab(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("Aucun élément extrait.")
        return
    f = st.columns([1, 1, 1, 2])
    sheet = f[0].multiselect("Feuillet", sorted(df.feuillet.unique()))
    niveau = f[1].multiselect("Niveau", sorted(x for x in df.niveau.unique() if x))
    only_unloc = f[2].checkbox("Non localisés seulement")
    thr = f[3].slider("Confiance minimale", 0.0, 1.0, 0.0, 0.01)

    view = df
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
        view, use_container_width=True, hide_index=True, height=440,
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
    st.dataframe(show, use_container_width=True, hide_index=True, height=520)


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
                     use_container_width=True, hide_index=True)
    st.markdown("**Détection des unités** — "
                f"`{result.unit_system}`, preuve : `{result.unit_evidence}`")
    with st.expander("Périmètre de cette étape"):
        st.markdown(
            "- ✅ Extraction côté **plan**, colonnes (série S-500)\n"
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
                         mime="application/json", use_container_width=True)
    c[1].download_button("éléments (CSV)", df.to_csv(index=False).encode("utf-8"),
                         file_name=f"{result.project}_elements.csv",
                         mime="text/csv", use_container_width=True)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("elements_plan.json", payload)
        zf.writestr("elements.csv", df.to_csv(index=False))
        zf.writestr("run_manifest.json", json.dumps({
            "pipeline_version": "0.1.0-slice1",
            "project": result.project, "plan_file": result.plan_file,
            "unit_system": result.unit_system, "unit_evidence": result.unit_evidence,
            "totals": result.totals, "elapsed_s": result.elapsed_s,
            "scope": "plan-side columns only",
        }, ensure_ascii=False, indent=2, sort_keys=True))
    c[2].download_button(f"{result.project}_l2c_review.zip", buf.getvalue(),
                         file_name=f"{result.project}_l2c_review.zip",
                         mime="application/zip", use_container_width=True)

    with st.expander("Aperçu du JSON (3 premiers enregistrements)"):
        st.code(json.dumps(records[:3], ensure_ascii=False, indent=2), language="json")


# ----------------------------------------------------------------- main
st.title("Révision des dessins d'atelier — L2C")
st.caption("Étape 1 : extraction côté plan et base JSON. "
           "La lecture des dessins d'atelier et la comparaison viennent ensuite.")

projects = intake()
if not projects:
    st.info("Choisissez un dossier de corpus ou téléversez l'archive d'un projet pour commencer.")
    st.stop()

names = [p[0] for p in projects]
chosen = st.sidebar.selectbox("Projet", names)
plan_path = dict(projects)[chosen]
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
