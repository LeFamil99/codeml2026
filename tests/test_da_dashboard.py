"""DA dashboard integration: dedicated adapters, scope, cache and last-page reads."""
from dataclasses import replace

import pymupdf
import pytest

from l2c import io_json
from l2c.da import dashboard
from l2c.da.imageread import TextLine
from l2c.da.parsers import colonne_clp, dalle_clp, poutre_clp, semelle_clp
from conftest import wait_for_da


def test_column_supplement_adds_only_last_sheet_and_current_revision_wins(
        tmp_path, connected_parsers):
    project = sources(tmp_path)
    path = project / "DA/Colonnes" / dashboard.COLUMN_SUPPLEMENT
    with pymupdf.open() as document:
        for _ in range(5):
            document.new_page()
        document.save(path)
    result = dashboard.run_da(str(project))
    assert [call[1] for call in connected_parsers if call[0] == "colonne"] == [1, 2, 5]
    assert len(dashboard.configured_inputs(str(project))["colonne"]) == 2
    # Fake readers repeat K-6: the earlier release must not restore it.
    assert len([r for r in result.records if r.type_element == "colonne"]) == 1
    report = next(s for s in result.sheets if s.fichier == dashboard.COLUMN_SUPPLEMENT)
    assert report.page == 5 and report.records == 0
    assert report.diagnostics["last_page_only"] is True
    assert report.diagnostics["superseded_by_current_schedule"]


def test_old_column_page_cells_upgrade_without_reparsing_other_types(
        tmp_path, monkeypatch, connected_parsers):
    project = sources(tmp_path)
    directory = tmp_path / "cache"
    current = dashboard.COLUMN_SCOPE
    monkeypatch.setattr(dashboard, "COLUMN_SCOPE", "all-pages-v1")
    dashboard.run_da(str(project), checkpoint_dir=directory)
    initial_calls = len(connected_parsers)
    monkeypatch.setattr(dashboard, "COLUMN_SCOPE", current)
    repaired = []
    def repair(page, cells):
        repaired.append(page.number+1)
        return cells
    monkeypatch.setattr(colonne_clp, "repair_cached_page", repair)
    result = dashboard.run_da(str(project), checkpoint_dir=directory)
    assert repaired == [1, 2]
    assert len(connected_parsers) == initial_calls
    assert result.meta["checkpoint_hits"] == 3
    assert result.meta["page_checkpoint_hits"] == 2
    assert dashboard.run_da(str(project), checkpoint_dir=directory).meta["checkpoint_hits"] == 4


def sources(tmp_path, all_slabs=False):
    project = tmp_path / "CLP"
    relatives = list(dashboard.CLP_FILES.values())
    if all_slabs:
        relatives += [f"DA/Dalles/{filename}" for filename in (
            "CLP_DALLE NIV 2.pdf", "CLP_DALLE NIV 4.pdf", "CLP_DALLE NIV 5.pdf",
            "CLP_DALLE NIV RDC.pdf", "CLP_DALLE TRÉFOND.pdf")]
    for relative in relatives:
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open() as doc:
            doc.new_page()
            last = doc.new_page(width=500, height=400)
            last.set_rotation(90)
            doc.save(path)
    return project


@pytest.fixture
def connected_parsers(monkeypatch):
    """Replace expensive OCR only; use each parser's real sanitation/converter."""
    called = []
    def note(kind, page, filename):
        called.append((kind, page.number + 1, filename))
        assert not page.rotation
    def columns(page, *, max_strips, check):
        note("colonne", page, "")
        assert max_strips == 0 and check is False
        cell = colonne_clp.Cell(page.number + 1, "K-6", "NIVEAU 2", 10, 10, 30, 30,
            "text", lines=['VERT: 4 25M', 'ETRI: 10M @6"'], confidence=.9,
            summary="4-25M · 10M@152mm", bars=[
                dict(label="VERT", quantite=4, diametre="25M"),
                dict(label="ÉTRI", diametre="10M", espacement_mm=152.4)])
        return [cell, replace(cell, confidence=.8), replace(cell, coordinate=None),
                replace(cell, summary=None)]
    def slabs(page, filename, *, check):
        note("dalle", page, filename)
        assert check is False
        support = dalle_clp.Support(page.number + 1, "main", "J-15", (10, 10, 20, 20),
            level="NIVEAU 3", layer="intégrité", status="read", confidence=.9)
        support.bars = dalle_clp.parse_lines([
            TextLine(10, 10, 100, 20, "NUM. 3 15M 11-03", .99),
            TextLine(10, 30, 100, 40, "ALP. 3 15M 11-03", .99)])
        return dalle_clp.PageResult(filename, page.number + 1, [],
            [support, replace(support), replace(support, coordinate=None)], [], 0.)
    def footings(page, filename, *, check):
        note("semelle", page, filename)
        assert check is False
        row = dict(coordinate="L-13", view="main", type="B", status="read", confidence=.9,
            reinforcement=[dict(role=role, formatted="11-25M", quantite=11, diametre="25M")
                           for role in ("LONG", "TRAN")],
            anchor=[100, 100], marker_bbox=[100, 100, 110, 110],
            definition_bbox=[200, 200, 210, 210])
        return dict(fichier=filename, page=page.number + 1, views=[],
                    footings=[row, dict(row), dict(row, coordinate=None)])
    def beams(page, filename):
        note("poutre", page, filename)
        lines = [TextLine(100, 250, 220, 256, 'P100 - 24" x 38 1/2"', .99),
                 TextLine(90, 140, 150, 146, "2 25M 11-00", .99),
                 TextLine(130, 175, 190, 181, "2 20M 8-09", .99)]
        bubbles = [dalle_clp.Bubble(x, 90, 20, label, .99)
                   for x, label in [(50, "17"), (130, "16"), (210, "15")]]
        return poutre_clp.assemble(lines, bubbles, filename, page.number + 1, 500, 400)
    for module, reader in [(colonne_clp, columns), (dalle_clp, slabs),
                           (semelle_clp, footings), (poutre_clp, beams)]:
        monkeypatch.setattr(module, "parse_page", reader)
    return called


def test_adapters_read_all_column_pages_and_other_last_pages(
        tmp_path, monkeypatch, connected_parsers):
    project = sources(tmp_path)
    # No native-text path or old generic reader may participate in this runner.
    def forbidden(*args, **kwargs):
        raise AssertionError("dashboard must use dedicated blind parsers")
    for name in ("get_text", "get_drawings", "get_texttrace"):
        monkeypatch.setattr(pymupdf.Page, name, forbidden)
    import l2c.da.pipeline
    monkeypatch.setattr(l2c.da.pipeline, "run_da", forbidden)
    progress = []
    result = dashboard.run_da(str(project), progress=lambda *args: progress.append(args))
    assert len(connected_parsers) == 5 and [call[1] for call in connected_parsers if call[0] == 'colonne'] == [1, 2]
    assert len(result.sheets) == 5 and all(s.page == 2 for s in result.sheets if s.type_element != 'colonne')
    assert all(s.status == "extracted" and s.tier == 3 for s in result.sheets)
    assert len(result.records) == 4
    assert {r.type_element for r in result.records} == set(dashboard.CLP_FILES)
    assert all(r.element != "UNKNOWN" and r.armature and r.debug.decode_path == "ocr"
               for r in result.records)
    footing = next(r for r in result.records if r.type_element == "semelle")
    assert [(b.quantite, b.diametre) for b in footing.armature] == [(11, "25M")] * 2
    assert footing.debug.roles == ["LONG", "TRAN"]
    beam = next(r for r in result.records if r.type_element == "poutre")
    assert beam.debug.position == "17 → 16 → 15"
    column = next(r for r in result.records if r.type_element == "colonne")
    assert column.debug.niveau == "NIVEAU 2" and column.armature[1].espacement_mm == 152.4
    assert result.sheets[0].diagnostics["deduplication"]["removed_rows"] == 1
    assert len(result.sheets[0].diagnostics["cells"]) == 4
    assert result.meta["pending_types"] == ["radier"]
    assert progress[-1][0:2] == (4, 4)
    output = tmp_path / "elements_atelier.json"
    io_json.write_records(str(output), result.records)
    assert io_json.validate_file(str(output)) == (4, [])


def test_missing_sources_are_reported_and_other_projects_do_not_use_clp_files(tmp_path):
    result = dashboard.run_da(str(tmp_path / "CLP"))
    assert not result.records and len(result.sheets) == 4
    assert all(s.status == "unread" and "introuvable" in s.reason for s in result.sheets)
    with pytest.raises(ValueError, match="CLP uniquement"):
        dashboard.run_da(str(tmp_path / "WP2"))
    with pytest.raises(ValueError, match="radier"):
        dashboard.run_da(str(tmp_path / "CLP"), inputs={"radier": "unused.pdf"})


def test_input_stamp_tracks_selected_files_including_removal(tmp_path):
    project = sources(tmp_path)
    original = dashboard.input_stamp(str(project))
    (project / "DA/unselected.pdf").touch()
    assert dashboard.input_stamp(str(project)) == original
    path = dashboard.configured_inputs(str(project))["poutre"][0]
    path.write_bytes(path.read_bytes() + b"\n")
    changed = dashboard.input_stamp(str(project))
    assert changed != original
    path.unlink()
    assert dashboard.input_stamp(str(project)) != changed


def test_all_slab_pdfs_use_last_pages_and_keep_each_file_in_the_output(
        tmp_path, connected_parsers):
    project = sources(tmp_path)
    directory = project / "DA/Dalles"
    extra = ["CLP_DALLE NIV 2.pdf", "CLP_DALLE NIV 4.pdf", "CLP_DALLE NIV 5.PDF",
             "CLP_DALLE NIV RDC.pdf", "CLP_DALLE TRÉFOND.pdf"]
    for index, filename in enumerate(extra, 3):
        with pymupdf.open() as doc:
            for _ in range(index):
                doc.new_page(width=500, height=400)
            doc.save(directory / filename)
    (directory / "notes.txt").touch()
    (directory / "nested.pdf").mkdir()
    result = dashboard.run_da(str(project))
    slabs = [r for r in result.records if r.type_element == "dalle"]
    expected = {"CLP_DALLE NIV 3.pdf": 2,
                **{filename: index for index, filename in enumerate(extra, 3)}}
    assert {r.fichier: r.page for r in slabs} == expected
    assert len(result.records) == 9 and result.meta["files"] == 9
    assert len({r.id for r in result.records}) == 9
    assert len([call for call in connected_parsers if call[0] == "dalle"]) == 6
    assert {s.fichier: s.page for s in result.sheets if s.type_element == "dalle"} == expected
    # Future uploads can provide a list; one path repeated must run only once.
    paths = dashboard.configured_inputs(str(project))["dalle"]
    uploaded = dashboard.run_da(str(project), inputs={"dalle": [*paths, paths[0]]})
    assert len(uploaded.records) == 6 and uploaded.meta["files"] == 6
    output = tmp_path / "all_slabs.json"
    io_json.write_records(str(output), uploaded.records)
    assert io_json.validate_file(str(output)) == (6, [])


def test_slab_folder_membership_invalidates_cache(tmp_path):
    project = sources(tmp_path)
    initial = dashboard.input_stamp(str(project))
    added = project / "DA/Dalles/new_slab.PDF"
    added.touch()
    assert dashboard.input_stamp(str(project)) != initial
    added.unlink()
    assert dashboard.input_stamp(str(project)) == initial


def test_interrupted_run_resumes_completed_files_and_invalidates_changed_inputs(
        tmp_path, monkeypatch, connected_parsers):
    project = sources(tmp_path)
    checkpoints = tmp_path / "checkpoints"
    original = dashboard.PARSERS["dalle"]
    def fail_after_columns(*args, **kwargs):
        raise RuntimeError("interrupted during second file")
    monkeypatch.setitem(dashboard.PARSERS, "dalle", fail_after_columns)
    with pytest.raises(RuntimeError, match="interrupted"):
        dashboard.run_da(str(project), checkpoint_dir=checkpoints)
    assert len(list(checkpoints.glob("*.pkl"))) == 1
    monkeypatch.setitem(dashboard.PARSERS, "dalle", original)
    resumed = dashboard.run_da(str(project), checkpoint_dir=checkpoints)
    assert len(resumed.records) == 4 and resumed.meta["checkpoint_hits"] == 1
    assert sum(call[0] == "colonne" for call in connected_parsers) == 2
    saved = [r.to_schema() for r in resumed.records]
    cached = dashboard.run_da(str(project), checkpoint_dir=checkpoints)
    assert cached.meta["checkpoint_hits"] == 4
    assert [r.to_schema() for r in cached.records] == saved
    assert len(connected_parsers) == 5
    slab = dashboard.configured_inputs(str(project))["dalle"][0]
    slab.write_bytes(slab.read_bytes() + b"\n")
    changed = dashboard.run_da(str(project), checkpoint_dir=checkpoints)
    assert changed.meta["checkpoint_hits"] == 3 and len(connected_parsers) == 6
    fresh = dashboard.run_da(str(project), checkpoint_dir=checkpoints, reuse_checkpoints=False)
    assert fresh.meta["checkpoint_hits"] == 0 and len(connected_parsers) == 11


def test_column_adapter_keeps_levels_conflicts_and_zero_confidence():
    cell = colonne_clp.Cell(2, "K-6", "NIVEAU 2", 0, 0, 10, 10, "text",
        confidence=0., summary="4-25M", bars=[dict(label="VERT", quantite=4, diametre="25M")])
    records = colonne_clp.records([cell, replace(cell), replace(cell, level="NIVEAU 3"),
        replace(cell, summary="5-25M", bars=[dict(label="VERT", quantite=5, diametre="25M")])],
        "columns.pdf")
    assert len(records) == 3 and records[0].debug.confidence == 0.
    assert records[0].debug.duplicate_conflict is True
    assert {r.debug.niveau for r in records} == {"NIVEAU 2", "NIVEAU 3"}


def test_column_scope_only_invalidates_columns_and_keeps_other_file_caches(
        tmp_path, monkeypatch, connected_parsers):
    project = sources(tmp_path)
    cache = tmp_path / 'cache'
    first = dashboard.run_da(str(project), checkpoint_dir=cache)
    stamp = dashboard.input_stamp(str(project))
    inputs = dashboard.configured_inputs(str(project))
    other_keys = {kind: dashboard.file_checkpoint(kind, paths[0], cache)[0]
                  for kind, paths in inputs.items() if kind != 'colonne'}
    monkeypatch.setattr(dashboard, 'COLUMN_SCOPE', 'all-pages-next')
    assert dashboard.input_stamp(str(project)) != stamp
    for kind, fingerprint in other_keys.items():
        assert dashboard.file_checkpoint(kind, inputs[kind][0], cache)[0] == fingerprint
    second = dashboard.run_da(str(project), checkpoint_dir=cache)
    assert second.meta['checkpoint_hits'] == 3
    assert len(connected_parsers) == 7  # Only the two column pages reread.
    assert first.meta['page_policy']['colonne'] == 'all'
    assert len(second.sheets) == 5 and not second.meta['last_page_only']


def test_interrupted_column_pdf_resumes_saved_page_and_deduplicates_across_pages(
        tmp_path, monkeypatch, connected_parsers):
    project = sources(tmp_path)
    cache = tmp_path / 'cache'
    original = colonne_clp.parse_page
    events = []
    def interrupted(page, **kwargs):
        if page.number == 1:
            raise RuntimeError('crash in column page two')
        return original(page, **kwargs)
    monkeypatch.setattr(colonne_clp, 'parse_page', interrupted)
    with pytest.raises(RuntimeError, match='page two'):
        dashboard.run_da(str(project), checkpoint_dir=cache,
                         page_progress=lambda *args: events.append(args))
    assert len(list((cache/'pages').glob('*.pkl'))) == 1
    assert not list(cache.glob('*.pkl'))
    assert any(event[1] == 1 and event[3] for event in events)
    monkeypatch.setattr(colonne_clp, 'parse_page', original)
    resumed = dashboard.run_da(str(project), checkpoint_dir=cache)
    assert resumed.meta['page_checkpoint_hits'] == 1
    assert len(connected_parsers) == 5
    columns = [r for r in resumed.records if r.type_element == 'colonne']
    assert len(columns) == 1  # Same coordinate/storey/spec on both synthetic pages.
    reports = [s for s in resumed.sheets if s.type_element == 'colonne']
    assert [s.page for s in reports] == [1, 2]
    assert reports[0].diagnostics['file_deduplication']['removed_rows'] == 3
    assert sum(s.records for s in reports) == 1
    assert resumed.meta['inputs'][0]['pages'] == [1, 2]


def test_corrupt_checkpoint_is_reparsed_and_version_change_invalidates_cache(
        tmp_path, monkeypatch, connected_parsers):
    project = sources(tmp_path)
    cache = tmp_path / "cache"
    dashboard.run_da(str(project), checkpoint_dir=cache)
    inputs = dashboard.configured_inputs(str(project))
    assert len(dashboard.checkpoint_inventory(inputs, cache)) == 4
    fingerprint, checkpoint = dashboard.file_checkpoint("dalle", inputs["dalle"][0], cache)
    checkpoint.write_bytes(b"incomplete write")
    assert len(dashboard.checkpoint_inventory(inputs, cache)) == 3
    repaired = dashboard.run_da(str(project), checkpoint_dir=cache)
    assert repaired.meta["checkpoint_hits"] == 3 and len(connected_parsers) == 6
    monkeypatch.setattr(dashboard, "PARSER_VERSION", "next-version")
    assert dashboard.checkpoint_inventory(inputs, cache) == []
    changed = dashboard.run_da(str(project), checkpoint_dir=cache)
    assert changed.meta["checkpoint_hits"] == 0 and len(connected_parsers) == 11


def test_da_ui_uses_the_connected_parsers_and_downloads_their_records(
        tmp_path, monkeypatch, connected_parsers, background_jobs):
    import os
    st = pytest.importorskip("streamlit")
    from streamlit.testing.v1 import AppTest
    import l2c.pipeline
    downloaded = []
    original_dump = io_json.dump_records
    def capture_download(records):
        payload = original_dump(records)
        downloaded.append(payload)
        return payload
    monkeypatch.setattr(io_json, "dump_records", capture_download)
    project = sources(tmp_path, all_slabs=True)
    plan = project / "L2C_PLAN_STR_CLP.pdf"
    plan.touch()
    monkeypatch.setattr(l2c.pipeline, "run_plan", lambda *a, **k: l2c.pipeline.ProjectResult(
        "CLP", plan.name, "imperial", {}, [],
        [l2c.pipeline.SheetReport("S-001", 1, None, None, 0, 0, "skipped")], 0.))
    st.cache_data.clear()
    try:
        app = os.path.join(os.path.dirname(__file__), "..", "app", "streamlit_app.py")
        at = AppTest.from_file(app, default_timeout=30).run()
        at.sidebar.text_input[0].set_value(str(project)).run()
        at.segmented_control[0].set_value("Dessins d'atelier").run()
        wait_for_da(at)
        assert not at.exception
        assert len(connected_parsers) == 10
        metrics = {m.label: m.value for m in at.metric}
        assert metrics["Radiers"] == "À venir" and metrics["Pages traitées"] == "10"
        assert metrics["Pages lues par OCR"] == "10"
        for filename in (p.name for p in dashboard.configured_inputs(str(project))["dalle"]):
            assert any(filename in text.value for text in at.markdown)
        tables = [df.value for df in at.dataframe if "armature" in df.value]
        assert any("LONG: 11-25M · TRAN: 11-25M" in value
                   for table in tables for value in table.armature)
        at.run()
        assert not at.exception and len(connected_parsers) == 10
        at.segmented_control[0].set_value("Plan L2C").run()
        assert not at.exception
        at.segmented_control[0].set_value("Dessins d'atelier").run()
        assert not at.exception and len(connected_parsers) == 10
        at.button(key="regenerate_atelier").click().run()
        wait_for_da(at)
        assert not at.exception and len(connected_parsers) == 20
        # Capture the serialization actually invoked by the UI download view.
        assert len(downloaded[-1]) == 9
        assert {r["type_element"] for r in downloaded[-1]} == set(dashboard.CLP_FILES)
    finally:
        st.cache_data.clear()
