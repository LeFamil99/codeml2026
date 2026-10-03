"""L2C review dashboard.

Two sections with the same layout and the same components (app/views.py):
- Plan L2C: every element type of the plan -> JSON -> download;
- Dessins d'atelier: the shop drawings of the same project (progress: DA_PLAN.md).
Matching and the conformity report are later stages.

Run:  streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import streamlit as st

import views
from l2c.da.inventory import find_da_dir
from l2c.da.pipeline import run_da
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
@st.cache_data(show_spinner=False)
def _run_plan(plan_path: str, mtime: float):
    return run_plan(plan_path)


def _da_stamp(project_dir: str) -> float:
    """Newest mtime under DA/ - the cache key, so an edited DA file reruns."""
    root = find_da_dir(project_dir)
    if root is None:
        return 0.0
    return max((os.path.getmtime(os.path.join(dp, f))
                for dp, _, fs in os.walk(root) for f in fs), default=0.0)


@st.cache_data(show_spinner=False)
def _run_da(project_dir: str, stamp: float):
    return run_da(project_dir)


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

section = st.segmented_control(
    "Section", ["Plan L2C", "Dessins d'atelier"], default="Plan L2C",
    label_visibility="collapsed", key="section",
) or "Plan L2C"

if section == "Plan L2C":
    st.caption("Extraction côté plan (tous les types d'éléments) et base JSON.")
    if st.button("Vider le cache et régénérer", key="regenerate_plan"):
        _run_plan.clear(plan_path, os.path.getmtime(plan_path))
    with st.spinner(f"Extraction du plan de {chosen}…"):
        result = _run_plan(plan_path, os.path.getmtime(plan_path))
    views.render(views.PLAN, result)
else:
    st.caption("Lecture des dessins d'atelier du même projet — avancement détaillé dans "
               "DA_PLAN.md. Le lecteur ne voit jamais les valeurs du plan.")
    if find_da_dir(project_dir) is None:
        st.warning(f"Aucun dossier DA dans {project_dir}.")
        st.stop()
    if st.button("Vider le cache et régénérer", key="regenerate_atelier"):
        _run_da.clear(project_dir, _da_stamp(project_dir))
    with st.spinner(f"Lecture des dessins d'atelier de {chosen}…"):
        result = _run_da(project_dir, _da_stamp(project_dir))
    views.render(views.ATELIER, result)
