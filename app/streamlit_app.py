"""L2C review dashboard.

Two sections with the same layout and the same components (app/views.py):
- Plan L2C: every element type of the plan -> JSON -> download;
- Dessins d'atelier: the shop drawings of the same project (progress: DA_PLAN.md).
The comparison section reviews loaded parser results; conformity reporting is pending.

Run:  streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import streamlit as st

import intake as _intake
import theme
import views
from l2c.da.dashboard import PARSER_VERSION, input_files, input_stamp_for
from l2c.da.jobs import get_manager
from l2c.pipeline import run_plan
from l2c.io_json import PIPELINE_VERSION

st.set_page_config(page_title="Révision L2C", layout="wide")
theme.apply()

def _store(upload, project: str) -> str:
    return _intake.store(upload.name, upload.getvalue(), project)


def intake():
    """The plan, the parsing method and the DA files that method reads, each from its own input."""
    from l2c.da.methods import DEFAULT_METHOD, METHODS
    st.sidebar.header("Fichiers")
    plan_upload = st.sidebar.file_uploader("Plan L2C", type="pdf", key="up_plan")
    if plan_upload is None:
        return None
    project = _intake.project_name(plan_upload.name)
    plan_path = _store(plan_upload, project)
    st.sidebar.subheader("Dessins d'atelier")
    method = st.sidebar.selectbox("Méthode d'analyse", list(METHODS), index=list(METHODS).index(DEFAULT_METHOD),
                                  format_func=lambda key: METHODS[key].label, key="da_method")
    da = {}
    for kind in METHODS[method].categories:
        uploads = st.sidebar.file_uploader(_intake.DA_LABELS[kind], type="pdf", accept_multiple_files=True,
                                           key=f"up_{method}_{kind}")
        da[kind] = [_store(upload, project) for upload in uploads or []]
    return project, plan_path, method, da


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
    st.info("Téléversez le plan L2C (PDF) dans le panneau de gauche pour commencer.")
    st.stop()

project, plan_path, method, da_sources = selection
project_dir = os.path.join(_intake.UPLOAD_ROOT, project)
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
    with st.spinner(f"Extraction du plan de {project}…"):
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
    stamp = (("method", method),) + input_stamp_for(da_sources)
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
    st.caption("Colonnes et radiers : fichier complet. Dalles, semelles et poutres : dernière page.")
    missing = [_intake.DA_LABELS[kind] for kind in da_sources if not da_sources[kind]]
    if missing:
        st.info("Aucun fichier pour : " + ", ".join(missing) + ".")
    with st.expander("Fichiers utilisés"):
        for kind, paths in da_sources.items():
            for path in paths:
                st.write(f"{_intake.DA_LABELS[kind]} · {os.path.basename(path)}")
    stamp = (("method", method),) + input_stamp_for(da_sources)
    manager = get_manager()
    job = manager.ensure(project_dir, stamp, PARSER_VERSION, da_sources, method=method)
    status = job.snapshot()["status"]
    label = "Reprendre la génération (conserver les fichiers sauvegardés)" if status == "failed" else "Vider le cache et régénérer"
    if st.button(label, key="regenerate_atelier", disabled=job.running):
        job = manager.ensure(project_dir, stamp, PARSER_VERSION, da_sources, force=True,
                             reparse=status == "completed", method=method)
    state = job.snapshot()
    if state["status"] == "completed":
        st.caption(f"{job.result().meta.get('checkpoint_hits', 0)} fichiers réutilisés depuis le cache.")
        views.render(views.ATELIER, job.result())
    elif state["status"] == "failed":
        st.error(f"La génération DA a échoué : {state['error']}")
        from l2c.da.dashboard import checkpoint_inventory
        saved = checkpoint_inventory(da_sources, manager.root / "file_results")
        st.info(f"{len(saved)}/{len(input_files(da_sources))} fichiers récupérables dans le cache. "
                "Reprendre réutilise les pages sauvegardées et les autres fichiers en cache.")
        st.caption(f"{len(state.get('saved_pages', []))} pages déjà sauvegardées sur disque.")
        if state.get("traceback"):
            with st.expander("Détails de l'erreur"):
                st.code(state["traceback"])
    else:
        da_progress(job)
