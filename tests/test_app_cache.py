"""Regenerate the selected section without invalidating the other section."""

import os

import pytest

pytest.importorskip("streamlit")
import streamlit as st
from streamlit.testing.v1 import AppTest

from l2c.model import Armature, Debug, ElementRecord
from l2c.pipeline import ProjectResult, SheetReport
from conftest import wait_for_da


def test_section_buttons_rerun_only_the_selected_parser(tmp_path, monkeypatch, background_jobs):
    import l2c.pipeline
    import l2c.da.dashboard
    calls = {"plan": 0, "atelier": 0}
    project = tmp_path / "CLP"
    project.mkdir()
    (project / "L2C_PLAN_STR_CLP.pdf").touch()
    (project / "DA").mkdir()
    def fake(side):
        def run(*args, **kwargs):
            calls[side] += 1
            if side == "atelier" and kwargs.get("progress"):
                for index in range(1, 5):
                    kwargs["progress"](index, 4, f"drawing-{index}.pdf")
            record = ElementRecord(id="test", source="plan" if side == "plan" else "atelier",
                                   fichier="source.pdf", feuillet="S-601", page=1, x=10, y=10,
                                   type_element="dalle", element="J-15",
                                   armature=[Armature(quantite=calls[side], diametre="15M")] * 2,
                                   debug=Debug(layer="intégrité", integrity_type="B", roles=["NUM", "ALP"]))
            return ProjectResult("CLP", "source.pdf", "imperial", {}, [record],
                                 [SheetReport("S-601", 1, "dalle", "NIVEAU 2", 1, 1, "extracted")], 0.1)
        return run
    monkeypatch.setattr(l2c.pipeline, "run_plan", fake("plan"))
    monkeypatch.setattr(l2c.da.dashboard, "run_da", fake("atelier"))
    st.cache_data.clear()
    try:
        app = os.path.join(os.path.dirname(__file__), "..", "app", "streamlit_app.py")
        at = AppTest.from_file(app, default_timeout=30).run()
        at.sidebar.text_input[0].set_value(str(project)).run()
        assert not at.exception
        tables = [df.value for df in at.dataframe if "armature" in df.value.columns]
        assert any("NUM:" in value and "ALP:" in value for df in tables for value in df.armature)
        assert any("intégrité B" in value for df in tables for value in df["détail"])
        baseline = calls["plan"]
        at.run()
        assert calls["plan"] == baseline
        at.button(key="regenerate_plan").click().run()
        assert not at.exception and calls["plan"] == baseline + 1
        at.segmented_control[0].set_value("Dessins d'atelier").run()
        wait_for_da(at)
        assert not at.exception and calls["atelier"] == 1
        assert any("Radiers" in info.value and "à venir" in info.value for info in at.info)
        assert any("CLP_POUTRES.pdf" in text.value for text in at.markdown)
        at.run()
        assert not at.exception and calls["atelier"] == 1
        at.segmented_control[0].set_value("Plan L2C").run()
        assert not at.exception and calls["plan"] == baseline + 1
        at.segmented_control[0].set_value("Dessins d'atelier").run()
        assert not at.exception and calls["atelier"] == 1
        at.button(key="regenerate_atelier").click().run()
        wait_for_da(at)
        assert not at.exception and calls["atelier"] == 2
        at.segmented_control[0].set_value("Plan L2C").run()
        assert not at.exception and calls["plan"] == baseline + 1
        at.segmented_control[0].set_value("Dessins d'atelier").run()
        assert not at.exception and calls["atelier"] == 2
    finally:
        st.cache_data.clear()
