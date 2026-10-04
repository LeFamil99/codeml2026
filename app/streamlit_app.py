"""L2C review dashboard.

Two sections with the same layout and the same components (app/views.py):
- Plan L2C: every element type of the plan -> JSON -> download;
- Dessins d'atelier: the shop drawings of the same project (progress: DA_PLAN.md).
The comparison section reviews loaded parser results; conformity reporting is pending.

Run:  streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import streamlit as st

import views
from l2c.da.dashboard import PARSER_VERSION, configured_inputs, input_files, input_stamp
from l2c.da.jobs import get_manager
from l2c.pipeline import find_plan, run_plan
from l2c.io_json import PIPELINE_VERSION

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
@st.cache_data(show_spinner=False)
def _run_plan(plan_path: str, mtime: float, parser_version: str):
    return run_plan(plan_path)


@st.fragment(run_every="1s")
def da_progress(job):
    state = job.snapshot()
    if state["status"] in ("completed", "failed"):
        st.rerun()
    current, total = state.get("current", 0), state.get("total", 0)
    st.info(f"Fichier {current}/{total} : {state['filename']} — lecture en cours…"
            if current else "Démarrage de la génération DA…")
    completed = len(state['saved_files']) if 'saved_files' in state else max(0, current - 1)
    st.progress(completed / max(1, total))
    st.caption(f"{len(state.get('saved_files', []))}/{total} fichiers sauvegardés sur disque · "
               f"{state.get('checkpoint_hits', 0)} réutilisés depuis le cache.")
    if state.get('current_page'):
        st.caption(f"Page {state['current_page']}/{state['file_pages']} du fichier · "
                   f"{len(state.get('saved_pages', []))} pages sauvegardées sur disque.")
    st.caption(f"En cours depuis {int(time.time() - state['started'])} s. "
               "Vous pouvez naviguer : la génération continue en arrière-plan.")


# ----------------------------------------------------------------- main
st.title("Révision des dessins d'atelier — L2C")

selection = intake()
if selection is None:
    st.info("Indiquez le dossier des projets (ou téléversez l'archive d'un projet) pour commencer.")
    st.stop()

chosen, plan_path = selection
project_dir = os.path.dirname(plan_path)
st.sidebar.caption(f"`{os.path.basename(plan_path)}`")
if st.sidebar.button("Vider le cache"):
    st.cache_data.clear()
    get_manager().clear_completed()

section = st.segmented_control(
    "Section", ["Plan L2C", "Dessins d'atelier", "Comparaison"], default="Plan L2C",
    label_visibility="collapsed", key="section",
) or "Plan L2C"

if section == "Plan L2C":
    st.caption("Extraction côté plan (tous les types d'éléments) et base JSON.")
    if st.button("Vider le cache et régénérer", key="regenerate_plan"):
        _run_plan.clear(plan_path, os.path.getmtime(plan_path), PIPELINE_VERSION)
    with st.spinner(f"Extraction du plan de {chosen}…"):
        result = _run_plan(plan_path, os.path.getmtime(plan_path), PIPELINE_VERSION)
    from l2c.record_formats import align_result
    align_result(result)
    st.session_state["loaded_plan"] = ((plan_path, os.path.getmtime(plan_path), PIPELINE_VERSION), result)
    views.render(views.PLAN, result)
elif section == "Comparaison":
    signature = (plan_path, os.path.getmtime(plan_path), PIPELINE_VERSION)
    loaded = st.session_state.get("loaded_plan")
    if not loaded or loaded[0] != signature:
        st.info("Chargez le Plan L2C pour ce projet avant de comparer.")
        st.stop()
    try:
        stamp = input_stamp(project_dir)
    except ValueError as error:
        st.info(str(error))
        st.stop()
    job = get_manager().lookup(project_dir, stamp, PARSER_VERSION)
    status = job.snapshot()["status"] if job else None
    if status != "completed":
        st.info("La comparaison sera disponible lorsque la génération des dessins d'atelier sera terminée. "
                "Cette section ne démarre aucune génération.")
        if status in ("queued", "running"):
            da_progress(job)
        st.stop()
    views.render_comparison(loaded[1], job.result())
else:
    st.caption("Colonnes CLP : Partie 3 complet et supplément sous-sol (Partie 1, page 5). "
               "Radiers et dalles (BAS, HAUT, intégrité) : fichier complet. Semelles et poutres : dernière page.")
    try:
        sources = configured_inputs(project_dir)
    except ValueError as error:
        st.info(str(error))
        st.stop()
    with st.expander("Fichiers utilisés"):
        for _, path in input_files(sources):
            st.write(path.name)
    stamp = input_stamp(project_dir)
    manager = get_manager()
    job = manager.ensure(project_dir, stamp, PARSER_VERSION, sources)
    status = job.snapshot()["status"]
    label = "Reprendre la génération (conserver les fichiers sauvegardés)" if status == "failed" else "Vider le cache et régénérer"
    if st.button(label, key="regenerate_atelier", disabled=job.running):
        job = manager.ensure(project_dir, stamp, PARSER_VERSION, sources, force=True,
                             reparse=status == "completed")
    state = job.snapshot()
    if state["status"] == "completed":
        st.caption(f"{job.result().meta.get('checkpoint_hits', 0)} fichiers réutilisés depuis le cache.")
        views.render(views.ATELIER, job.result())
    elif state["status"] == "failed":
        st.error(f"La génération DA a échoué : {state['error']}")
        from l2c.da.dashboard import checkpoint_inventory
        saved = checkpoint_inventory(sources, manager.root / "file_results")
        st.info(f"{len(saved)}/{len(input_files(sources))} fichiers récupérables dans le cache. "
                "Reprendre réutilise les pages de colonnes sauvegardées et les autres fichiers en cache.")
        st.caption(f"{len(state.get('saved_pages', []))} pages déjà sauvegardées sur disque.")
        if state.get("traceback"):
            with st.expander("Détails de l'erreur"):
                st.code(state["traceback"])
    else:
        da_progress(job)
