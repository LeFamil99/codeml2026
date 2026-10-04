import json
import pickle
from l2c.beam_records import align_beam_records, align_result, BEAM_FORMAT
from l2c.model import Armature, Debug, ElementRecord
from l2c.pipeline import ProjectResult, SheetReport
from l2c.da import dashboard, jobs
from l2c import io_json


def callout(number, *, element='P100', source='plan', page=1, quantity=2):
    return ElementRecord(id=f'beam_{number}_{source}', source=source, fichier=f'{source}.pdf',
        feuillet='S-300', page=page, x=number * 10., y=number * 20.,
        type_element='poutre', element=element,
        armature=[Armature(quantite=quantity, diametre='25M')],
        debug=Debug(raw=[f'{quantity}-25M'], role='longitudinale',
                    beam_anchor=[50., 100.], axes=[dict(label='17', x=10., y=1.)],
                    position='17 → 16 → 15'))


def test_shared_unit_preserves_repeated_bars_and_each_annotation_position():
    rows = [callout(1), callout(2)]
    aligned = align_beam_records(rows)
    assert len(aligned) == 1 and len(aligned[0].armature) == 2
    assert aligned[0].debug.roles == ['longitudinale'] * 2
    assert [(a['x'], a['y']) for a in aligned[0].debug.annotations] == [(10., 20.), (20., 40.)]
    assert [a['source_id'] for a in aligned[0].debug.annotations] == [r.id for r in rows]
    assert (aligned[0].x, aligned[0].y) == (50., 100.)
    assert aligned[0].debug.position == '17 → 16 → 15'
    assert align_beam_records(aligned)[0] is aligned[0]
    assert len(rows[0].armature) == 1  # Original cached objects are not mutated.


def test_unlocated_callouts_and_different_pages_sources_remain_independent():
    rows = [callout(1), callout(2, source='atelier'), callout(3, page=2),
            callout(4, element='UNKNOWN'), callout(5, element='UNKNOWN')]
    assert len(align_beam_records(rows)) == 5


def test_exact_repeated_observation_is_removed_without_losing_conflicting_reads():
    first = callout(1)
    repeated = first.model_copy(update={'id': 'duplicate'})
    conflicting = callout(1, quantity=3)
    grouped = align_beam_records([first, repeated, conflicting])[0]
    assert [bar.quantite for bar in grouped.armature] == [2, 3]


def test_saved_project_upgrade_updates_counts_and_appendix_a_output(tmp_path):
    rows = [callout(1), callout(2)]
    result = ProjectResult('CLP', 'plan.pdf', 'imperial', {}, rows,
        [SheetReport('S-300', 1, 'poutre', None, 2, 2, 'extracted')], 0.)
    assert align_result(result)
    assert not align_result(result)
    assert result.sheets[0].records == result.sheets[0].located == 1
    assert result.sheets[0].diagnostics['reinforcement_entries'] == 2
    path = tmp_path / 'elements_plan.json'
    io_json.write_records(str(path), result.records)
    assert io_json.validate_file(str(path)) == (1, [])
    assert len(json.loads(path.read_text())[0]['armature']) == 2


def test_legacy_completed_job_and_checkpoint_upgrade_without_pdf_or_ocr(tmp_path):
    pdf = tmp_path / 'beam.pdf'
    pdf.write_bytes(b'not even a PDF; migration must not read it')
    cache = tmp_path / 'checkpoints'
    cache.mkdir()
    fingerprint, path = dashboard.file_checkpoint('poutre', pdf, cache)
    rows = [callout(1, source='atelier'), callout(2, source='atelier')]
    sheet = SheetReport('S-300', 1, 'poutre', None, 2, 2, 'extracted', fichier='atelier.pdf')
    jobs.write_pickle(path, {'fingerprint': fingerprint, 'records': rows, 'sheet': sheet})
    saved = dashboard.load_checkpoint(path, fingerprint)
    assert len(saved['records']) == saved['sheet'].records == 1
    assert saved['records'][0].debug.record_format == BEAM_FORMAT
    assert len(pickle.loads(path.read_bytes())['records']) == 1
    # Completed result on disk upgrades once and remains recoverable after reload.
    directory = tmp_path / 'job'
    directory.mkdir()
    jobs.write_json(directory / 'state.json', {'status': 'completed'})
    result = ProjectResult('CLP', 'atelier.pdf', 'imperial', {}, rows, [sheet], 0.)
    jobs.write_pickle(directory / 'result.pkl', result)
    job = jobs.Job(directory, jobs.ProcessReference(0, directory))
    assert len(job.result().records) == 1
    assert len(pickle.loads((directory / 'result.pkl').read_bytes()).records) == 1
    assert len(job.result().records[0].armature) == 2


def test_grouped_da_does_not_invent_individual_bar_positions():
    grouped = callout(1, source='atelier')
    grouped.armature.append(Armature(quantite=3, diametre='20M'))
    grouped.debug.roles = ['longitudinale', 'étriers']
    aligned = align_beam_records([grouped])[0]
    assert all(a['x'] is None and a['y'] is None for a in aligned.debug.annotations)
