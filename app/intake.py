"""Uploaded inputs: each file is stored once under its content digest.

The same file always lands at the same path, so the DA cache (file name and content) is reused
across sessions. Nothing here depends on Streamlit.
"""

import hashlib
import os

UPLOAD_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".cache", "uploads")
DA_LABELS = {
    "colonne": "Colonnes",
    "dalle": "Dalles",
    "semelle": "Semelles",
    "poutre": "Poutres",
    "radier": "Radiers",
}


def project_name(plan_file_name: str) -> str:
    """Project label, the same rule as l2c.pipeline.run_plan: L2C_PLAN_STR_<project>.pdf."""
    return plan_file_name.replace("L2C_PLAN_STR_", "").replace(".pdf", "")


def store(name: str, data: bytes, project: str, root: str = UPLOAD_ROOT) -> str:
    folder = os.path.join(root, project, hashlib.sha256(data).hexdigest()[:16])
    path = os.path.join(folder, os.path.basename(name))
    if not os.path.isfile(path):
        os.makedirs(folder, exist_ok=True)
        with open(path + ".part", "wb") as out:
            out.write(data)
        os.replace(path + ".part", path)
    return path
