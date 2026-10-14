import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

import numpy as np
import pytest
import soundfile as sf
from fastapi import HTTPException

from agent.api import speech_analysis as api, speech as speech_api
from agent.db import crud, schema
from agent.models.speech import Cue, SpeechDocument
from agent.services import media_jobs as jobs, speech, speech_analysis as analysis, speech_store as store, video_assembly
from agent.services.speech_analysis_core import assign_turns, compare_words, inspect_signal, recognized_cue


def test_signal_preserves_stereo_and_reports_real_locations(tmp_path):
    rate = 16000
    clean = .2 * np.sin(2 * np.pi * 300 * np.arange(rate * 2) / rate)
    wav = np.concatenate([clean, np.zeros(rate * 2), np.ones(rate)])
    path = tmp_path / 'signal.wav'
    sf.write(path, np.column_stack([wav, -wav]), rate, subtype='FLOAT')
    before = path.read_bytes()
    result = inspect_signal(path)
    silence = next(w for w in result['warnings'] if w['kind'] == 'silence')
    assert (silence['start'], silence['end']) == (2, 4)
    assert any(w['kind'] == 'clipping' and w['start'] == 4 for w in result['warnings'])
    assert path.read_bytes() == before
    limited = inspect_signal(path, max_seconds=1)
    assert limited['limited'] and limited['analyzed_seconds'] == 1
    assert not any(w['kind'] == 'silence' for w in limited['warnings'])


def test_signal_empty_invalid_and_quiet(tmp_path):
    path = tmp_path / 'test.wav'
    sf.write(path, np.array([], dtype='float32'), 16000)
    assert inspect_signal(path)['warnings'][0]['kind'] == 'empty'
    sf.write(path, np.concatenate([np.ones(16000) * .006, np.full(1600, np.nan)]), 16000, subtype='FLOAT')
    kinds = {w['kind'] for w in inspect_signal(path)['warnings']}
    assert {'invalid', 'quiet'} <= kinds


def test_words_unicode_punctuation_and_missing_speech():
    assert compare_words('XIN CHÀO, Phúc!', 'xin chào phúc')['difference'] == 0
    assert not compare_words('Xin chào bạn.', 'Xin chào bạn')['needs_review']
    assert compare_words('Xin chào bạn, hôm nay trời rất đẹp.', 'Xin chào')['needs_review']
    assert compare_words('Xin chào.', '')['needs_review']
    assert compare_words('ma', 'má')['needs_review']


def test_recognized_file_identity_ignores_inaccurate_token_times():
    item = dict(id='a', start=20, end=22, text='Xin chào')
    transcript = {'transcription': [{'text': ' Xin chào', 'tokens': [
        {'text': ' Xin', 'offsets': {'from': 0, 'to': 0}, 'p': .9},
        {'text': ' chào', 'offsets': {'from': 99000, 'to': 100000}, 'p': .9},
        {'text': '[_EOT_]', 'p': .01},
    ]}]}
    result = recognized_cue(transcript, item)
    assert result['recognized'] == 'Xin chào' and result['id'] == 'a'
    assert result['start'] == 20 and not result['needs_review']
    transcript['transcription'][0]['tokens'][0]['p'] = .1
    assert recognized_cue(transcript, item)['uncertain']
    assert recognized_cue({'transcription': []}, item)['needs_review']


async def test_whisper_missing_file_output_rejected(tmp_path, monkeypatch):
    async def partial(args, **options):
        store.atomic_json(tmp_path / 'recognized-0.json', {'transcription': [{'text': 'Xin chào'}]})
    monkeypatch.setattr(jobs, 'process', partial)
    items = [dict(id=str(i), path=str(tmp_path / f'{i}.wav'), duration=1, start=i, end=i+1, text='Xin chào') for i in range(2)]
    with pytest.raises(ValueError, match='không trả đủ'):
        await analysis.recognize(items, 'small', 'vi', tmp_path, lambda **changes: None)


def test_speakers_order_overlap_and_uncertain_assignment():
    cues = [dict(id=str(i), start=i * 3, end=i * 3 + 3, text='Lời nói') for i in range(4)]
    turns = [dict(start=0, end=2.5, speaker='9'), dict(start=.2, end=2, speaker='9'),
             dict(start=3, end=6, speaker='2'), dict(start=3, end=6, speaker='9'),
             dict(start=6, end=6.1, speaker='9')]
    result = assign_turns(cues, turns)
    assert result['speakers'] == ['Người nói 1', 'Người nói 2']
    assert result['cues'][0]['coverage'] == pytest.approx(2.5 / 3, abs=.001)
    assert not result['cues'][0]['needs_review']
    assert all(c['needs_review'] for c in result['cues'][1:])
    assert result['cues'][-1]['speaker'] == ''


@pytest.fixture
async def project(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path / 'db.sqlite')
    monkeypatch.setattr(jobs, 'ROOT', tmp_path / 'media')
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'speech')
    monkeypatch.setattr(video_assembly, 'OUTPUT_DIR', tmp_path / 'output')
    monkeypatch.setattr(analysis, 'TTS_SEMAPHORE', asyncio.Semaphore(1))
    await schema.init_db()
    p = await crud.create_project(name='Audio analysis test', material='realistic')
    video = await crud.create_video(p['id'], title='Dialogue')
    scene = await crud.create_scene(video['id'], 0, 'Scene')
    await crud.update_scene(scene['id'], duration=8)
    scenes = await crud.list_scenes(video['id'])
    cue = Cue(start=0, end=2, text='Xin chào bạn.', voice_id='fixture')
    doc = SpeechDocument(revision=1, timeline=store.timeline_key(scenes), cues=[cue])
    store.atomic_json(store.video_dir(video['id']) / 'script.json', doc.model_dump(mode='json'))
    wav = tmp_path / 'speech.wav'
    sf.write(wav, np.zeros(16000), 16000)
    monkeypatch.setattr(speech, 'cue_audio', lambda vid, c: (wav, {}, {}, 'sig'))
    monkeypatch.setattr(speech, 'cue_status', lambda vid, c: {'audio_url': '/audio', 'duration': 1})
    try:
        yield UUID(video['id']), doc, scenes, wav
    finally:
        await jobs.shutdown()
        await schema.close_db()


async def complete(vid):
    task = jobs._tasks.get(str(vid))
    if task:
        await asyncio.wait_for(asyncio.shield(task), 30)
    return jobs.latest(str(vid))


async def test_actual_signal_worker_report_survives_reload_and_detects_changes(project):
    vid, doc, scenes, wav = project
    original = store.load_document(str(vid)).model_dump()
    await api.start(vid, api.AnalysisRequest(kind='signal', revision=1))
    job = await complete(vid)
    assert job['status'] == 'COMPLETED', job
    first = (await api.status(vid))['reports']['signal_speech']
    assert first['items'][0]['warnings'][0]['kind'] == 'silence'
    assert not first['stale']
    assert store.load_document(str(vid)).model_dump() == original
    assert (await api.status(vid))['reports']['signal_speech'] == first
    # Regeneration at the same pathname invalidates the stored report.
    sf.write(wav, np.ones(32000) * .1, 16000)
    assert (await api.status(vid))['reports']['signal_speech']['stale']
    # Same audio, edited document also invalidates (including speaker-only edits).
    doc.cues[0].speaker = 'Phúc'
    store.atomic_json(store.video_dir(str(vid)) / 'script.json', doc.model_dump(mode='json'))
    assert analysis.fingerprint(str(vid), doc, scenes, 'signal_speech') != first['fingerprint']


async def test_revision_lock_cancel_and_previous_report_retained(project, monkeypatch):
    vid, doc, _, _ = project
    await api.start(vid, api.AnalysisRequest(kind='signal', revision=1))
    await complete(vid)
    report = (await api.status(vid))['reports']['signal_speech']
    entered = asyncio.Event()
    async def waiting(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(analysis, 'worker', waiting)
    await api.start(vid, api.AnalysisRequest(kind='signal', revision=1))
    await entered.wait()
    with pytest.raises(HTTPException) as exc:
        await speech_api.save_document(str(vid), doc)
    assert exc.value.status_code == 409
    with pytest.raises(HTTPException):
        await api.start(vid, api.AnalysisRequest(kind='signal', revision=1))
    await api.cancel(vid)
    assert jobs.latest(str(vid))['status'] == 'CANCELLED'
    assert (await api.status(vid))['reports']['signal_speech'] == report
    assert not list(jobs.directory(str(vid)).glob('analysis-*/'))
    with pytest.raises(HTTPException) as exc:
        await api.start(vid, api.AnalysisRequest(kind='signal', revision=0))
    assert exc.value.status_code == 409


async def test_missing_model_and_export_errors_are_actionable(project, monkeypatch):
    vid, _, _, _ = project
    monkeypatch.setattr(analysis, 'capabilities', lambda: {'signal_ready': True, 'speakers_ready': False, 'models': []})
    for kind, target in [('speakers', 'speech'), ('words', 'speech'), ('signal', 'export')]:
        with pytest.raises(HTTPException) as exc:
            await api.start(vid, api.AnalysisRequest(kind=kind, target=target, revision=1))
        assert exc.value.status_code == 422
    assert not jobs.latest(str(vid))


async def test_changed_script_during_analysis_does_not_publish_old_results(project, monkeypatch):
    vid, doc, _, _ = project
    async def changed(*args, **kwargs):
        doc.revision += 1
        store.atomic_json(store.video_dir(str(vid)) / 'script.json', doc.model_dump(mode='json'))
        return []
    monkeypatch.setattr(analysis, 'worker', changed)
    await api.start(vid, api.AnalysisRequest(kind='signal', revision=1))
    job = await complete(vid)
    assert job['status'] == 'FAILED'
    assert 'đã đổi' in job['error']
    assert not (await api.status(vid))['reports']


async def test_source_preview_keeps_only_published_version(project, tmp_path, monkeypatch):
    vid, _, _, wav = project
    preview = await analysis.source_preview(wav, tmp_path, lambda **changes: None)
    first = {'fingerprint': 'one'}
    analysis.publish(str(vid), 'speakers', first, preview)
    response = await api.preview(vid, UUID(first['source_audio_id']))
    old_path = Path(response.path)
    assert old_path.is_file()
    preview = await analysis.source_preview(wav, tmp_path, lambda **changes: None)
    second = {'fingerprint': 'two'}
    analysis.publish(str(vid), 'speakers', second, preview)
    assert not old_path.exists()
    with pytest.raises(HTTPException):
        await api.preview(vid, UUID(first['source_audio_id']))
    response = await api.preview(vid, UUID(second['source_audio_id']))
    assert Path(response.path).is_file()
    # A failed report write must retain the playable previous report and clean its candidate.
    preview = await analysis.source_preview(wav, tmp_path, lambda **changes: None)
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(store, 'atomic_json', fail)
    with pytest.raises(OSError):
        analysis.publish(str(vid), 'speakers', {'fingerprint': 'three'}, preview)
    assert len(list(jobs.directory(str(vid)).glob('analysis-source-*.m4a'))) == 1
    assert (await api.preview(vid, UUID(second['source_audio_id']))).path == response.path


async def test_cancel_during_source_assembly_stops_child_and_keeps_previous_cut(project, tmp_path, monkeypatch):
    vid, _, scenes, _ = project
    original_process = jobs.process
    clip = tmp_path / 'clip.mp4'
    await original_process([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=blue:s=160x90:d=1:r=24',
        '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(clip),
    ])
    second = await crud.create_scene(str(vid), 1, 'Second')
    for scene in [scenes[0], second]:
        await crud.update_scene(scene['id'], horizontal_video_status='COMPLETED', horizontal_video_url=clip.as_uri(), duration=1)
    await crud.update_video(str(vid), orientation='HORIZONTAL')
    saved_cut = video_assembly.assembled_path(str(vid))
    saved_cut.parent.mkdir(parents=True)
    saved_cut.write_bytes(b'previous playable cut')
    entered, child = asyncio.Event(), []

    async def slow_normalization(args, **options):
        if args[0] == 'ffmpeg':
            def ready(line):
                child.append(int(line))
                entered.set()
            return await original_process([
                sys.executable, '-u', '-c', "import os,time; print(os.getpid(),flush=True); time.sleep(30)",
            ], on_line=ready)
        return await original_process(args, **options)

    monkeypatch.setattr(jobs, 'process', slow_normalization)
    work = tmp_path / 'review'
    work.mkdir()
    task = asyncio.create_task(analysis.source_audio(str(vid), await crud.list_scenes(str(vid)), work, lambda **changes: None))
    await asyncio.wait_for(entered.wait(), 5)
    assert video_assembly.video_export_lock.locked()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    with pytest.raises(ProcessLookupError):
        os.kill(child[0], 0)
    assert not video_assembly.video_export_lock.locked()
    assert saved_cut.read_bytes() == b'previous playable cut'
    assert not list(work.iterdir())


async def test_source_progress_does_not_reset_after_assembly(tmp_path, monkeypatch):
    async def render(vid, output, **options):
        options['progress_callback']('downloading', 1)
        options['progress_callback']('assembling', 1)
        return {'duration': 16}
    async def extract(args, **options):
        for timestamp in [0, 16_000_000]:
            options['on_line'](f'out_time_us={timestamp}')
    monkeypatch.setattr(analysis, 'render_video_to_file', render)
    monkeypatch.setattr(jobs, 'process', extract)
    monkeypatch.setattr(analysis.imported_video, 'metadata', lambda vid: None)
    changes = []
    await analysis.source_audio('11111111-1111-1111-1111-111111111111', [], tmp_path, lambda **change: changes.append(change))
    percentages = [c['progress'] for c in changes if 'progress' in c]
    assert percentages == sorted(percentages)
    assert percentages[-1] == 9
    with pytest.raises(ValueError, match='180 phút'):
        await analysis.extract_audio(tmp_path / 'long.mp4', tmp_path / 'audio.wav', lambda **c: None, 10801)


async def test_preview_cleanup_failure_does_not_fail_a_published_report(project, tmp_path, monkeypatch):
    vid, _, _, wav = project
    first = {'fingerprint': 'one'}
    analysis.publish(str(vid), 'speakers', first, await analysis.source_preview(wav, tmp_path, lambda **changes: None))
    old_path = jobs.directory(str(vid)) / f"analysis-source-{first['source_audio_id']}.m4a"
    next_preview = await analysis.source_preview(wav, tmp_path, lambda **changes: None)
    original_unlink = Path.unlink
    def blocked(path, *args, **kwargs):
        if path == old_path:
            raise PermissionError('cleanup denied')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', blocked)
    second = {'fingerprint': 'two'}
    analysis.publish(str(vid), 'speakers', second, next_preview)
    assert store.read_json(jobs.directory(str(vid)) / 'analysis-speakers.json')['fingerprint'] == 'two'
    assert Path((await api.preview(vid, UUID(second['source_audio_id']))).path).is_file()


def test_export_analysis_uses_latest_full_timeline_cut_and_invalidates_old_report(tmp_path, monkeypatch):
    from agent.services import editing
    from uuid import uuid4
    monkeypatch.setattr(editing, 'ROOT', tmp_path/'editing')
    vid, full_id, range_id = str(uuid4()), str(uuid4()), str(uuid4())
    original = tmp_path/'old.mp4'; original.write_bytes(b'old cut')
    monkeypatch.setattr(analysis, 'assembled_path', lambda _: original)
    full = editing.directory(vid)/'exports'/f'{full_id}.mp4'
    full.parent.mkdir(parents=True); full.write_bytes(b'new full timeline cut')
    partial = full.parent/f'{range_id}.mp4'; partial.write_bytes(b'range')
    doc = SpeechDocument()
    before = analysis.fingerprint(vid, doc, [], 'signal_export')
    editing.record_export(vid, {'export_id': full_id, 'url': f'/exports/{full_id}', 'operation': 'timeline', 'created_at': 1, 'range_start': 0, 'range_end': None})
    editing.record_export(vid, {'export_id': range_id, 'url': f'/exports/{range_id}', 'operation': 'timeline', 'created_at': 2, 'range_start': 1, 'range_end': 2})
    path, url = analysis.export_source(vid)
    assert path == full and full_id in url
    assert analysis.fingerprint(vid, doc, [], 'signal_export') != before
    full.unlink()
    assert analysis.export_source(vid)[0] == original
