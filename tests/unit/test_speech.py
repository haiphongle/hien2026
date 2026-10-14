import asyncio
import shutil
import subprocess
import threading
import wave
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from agent.api import speech as api
from agent.db import crud, schema
from agent.models.speech import Cue, GenerateSpeechRequest, ParseSpeechRequest, SpeechDocument
from agent.services import media_jobs, subtitle_translation, speech, speech_jobs, speech_store as store, speech_voices, vieneu, kokoro, qwen3


def wav(path, seconds=0.5):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b'\x00\x08' * int(48000 * seconds))


@pytest.fixture
async def project(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path / 'test.db')
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'speech')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path / 'media')
    monkeypatch.setattr(speech, 'TTS_SEMAPHORE', asyncio.Semaphore(1))
    await schema.init_db()
    try:
        p = await crud.create_project(name='Test speech', material='realistic')
        v = await crud.create_video(p['id'], title='Dialogue')
        s = await crud.create_scene(v['id'], 0, 'Scene')
        await crud.update_scene(s['id'], duration=8)
        yield p, v, s
    finally:
        await speech_jobs.drain_jobs()
        await schema.close_db()


def test_srt_roundtrip_unicode_gaps_and_speaker():
    cues = store.parse_srt('\ufeff1\r\n00:00:01,250 --> 00:00:03,000\r\nPHÚC: <i>Xin chào!</i>\r\n\r\n2\r\n00:00:04,000 --> 00:00:06,000\r\n[Linh] Em khoẻ.\nRất vui.')
    assert [(c.speaker, c.start, c.end) for c in cues] == [('PHÚC', 1.25, 3), ('Linh', 4, 6)]
    assert cues[1].text == 'Em khoẻ.\nRất vui.'
    exported = store.parse_srt(store.srt_text(cues))
    assert [(c.start, c.end, c.text) for c in exported] == [(c.start, c.end, c.text) for c in cues]


@pytest.mark.parametrize('text', [
    '1\n00:00:03,000 --> 00:00:01,000\nHi',
    '1\n00:60:01,000 --> 00:60:03,000\nHi',
    '1\n00:00:01,000 --> 00:00:03,000\nHi\n\n2\n00:00:02,000 --> 00:00:04,000\nThere',
    '1\n00:00:01,000 --> 00:00:03,000',
])
def test_reject_invalid_srt(text):
    with pytest.raises(ValueError):
        store.parse_srt(text)


def test_dialogue_five_speakers_and_scene_headers():
    cues = store.parse_dialogue('Cảnh 1\nPHÚC: Xin chào.\nLINH: Chào anh.\nCảnh 2\nA: Rất vui.\nB: Tôi cũng vậy.\nC: Hẹn gặp lại.', [{'duration': 8}, {'duration': 6}])
    assert len({c.speaker for c in cues}) == 5
    assert cues[0].start == 0 and cues[2].start == 8 and cues[-1].end == 14
    with pytest.raises(ValueError, match='Cảnh 1'):
        store.parse_dialogue('Phúc: Hi\nCảnh 2\nLinh: Hello', [{'duration': 8}, {'duration': 8}])


async def test_document_revision_defaults_and_timeline_guard(project):
    p, v, s = project
    doc = await api.parse_document(v['id'], ParseSpeechRequest(mode='dialogue', text='PHÚC: Xin chào.\nLINH: Chào anh.'))
    for cue, voice in zip(doc.cues, vieneu.catalog()[:2]):
        cue.voice_id = voice['id']
    saved = await api.save_document(v['id'], doc)
    stale = saved.model_copy(deep=True)
    saved.cues[0].text = 'Xin chào bạn.'
    await api.save_document(v['id'], saved)
    with pytest.raises(HTTPException) as exc:
        await api.save_document(v['id'], stale)
    assert exc.value.status_code == 409
    v2 = await crud.create_video(p['id'], title='Second video')
    await crud.create_scene(v2['id'], 0, 'Scene')
    other = await api.parse_document(v2['id'], ParseSpeechRequest(mode='dialogue', text='Phúc: Một câu mới.'))
    assert other.cues[0].voice_id == doc.cues[0].voice_id
    await crud.update_scene(s['id'], duration=9)
    with pytest.raises(HTTPException) as exc:
        await api.save_document(v['id'], saved)
    assert exc.value.status_code == 422
    assert (await api.save_document(v['id'], saved, acknowledge_timeline=True)).revision == 3


async def test_generation_cache_voice_speed_overflow_retry_and_export(project, monkeypatch):
    _, v, _ = project
    vid = v['id']
    doc = await api.parse_document(vid, ParseSpeechRequest(mode='srt', text='1\n00:00:01,000 --> 00:00:03,000\nPhúc: Xin chào.\n\n2\n00:00:04,000 --> 00:00:06,000\nLinh: Chào anh.'))
    for c, voice in zip(doc.cues, vieneu.catalog()[:2]):
        c.voice_id = voice['id']
    doc = await api.save_document(vid, doc)
    calls = []
    fail = [True]
    def render(args, progress):
        item = args['items'][0]
        calls.append((item['id'], args['identity']['preset_id'], args['speed']))
        if item['id'] == str(doc.cues[1].id) and fail[0]:
            return [{'id': item['id'], 'ok': False, 'error': 'test failure'}]
        wav(item['output'], 0.5 / args['speed'])
        store.atomic_json(Path(item['output'] + '.json'), {'signature': item['signature']})
        return [{'id': item['id'], 'ok': True}]
    monkeypatch.setattr(vieneu.worker, 'generate', render)
    await api.generate(vid, GenerateSpeechRequest(revision=doc.revision))
    await speech_jobs.drain_jobs()
    assert (await api.get_document(vid))['job']['status'] == 'COMPLETED_WITH_ERRORS'
    fail[0] = False
    await api.generate(vid, GenerateSpeechRequest(revision=doc.revision))
    await speech_jobs.drain_jobs()
    assert len(calls) == 3  # Successful first cue reused on retry.
    scenes = await crud.list_scenes(vid)
    tracks, captions, _ = speech.prepare_export(vid, doc.revision, scenes)
    assert [t[1:] for t in tracks] == [(1, 2), (4, 2)]
    assert [c['duration'] for c in captions] == [1, 2, 1, 2]
    assert captions[1]['narrator_text'] == 'Xin chào.'
    doc.cues[0].end = 1.1
    doc = await api.save_document(vid, doc)
    assert speech.cue_status(vid, doc.cues[0])['status'] == 'TOO_LONG'
    with pytest.raises(ValueError, match='vượt khung'):
        speech.prepare_export(vid, doc.revision, scenes)
    cached_path = speech.cue_audio(vid, doc.cues[0])[0]
    doc.cues[0].end = 1.65
    doc = await api.save_document(vid, doc)
    assert speech.cue_status(vid, doc.cues[0])['status'] == 'READY'
    assert speech.cue_audio(vid, doc.cues[0])[0] == cached_path
    tracks, _, _ = speech.prepare_export(vid, doc.revision, scenes)
    assert tracks[0][1:] == pytest.approx((1, .65))
    assert len(calls) == 3  # Timing repair reuses audio without another TTS call.
    doc.cues[0].speed = 1.2
    assert speech.cue_status(vid, doc.cues[0])['status'] == 'MISSING'
    doc.cues[0].speed = 1
    doc.cues[0].voice_id = vieneu.catalog()[2]['id']
    assert speech.cue_status(vid, doc.cues[0])['status'] == 'MISSING'


def test_interrupted_job_and_invalid_paths(tmp_path, monkeypatch):
    path = tmp_path / 'job.json'
    store.atomic_json(path, {'status': 'PROCESSING'})
    assert speech_jobs.get_job('unknown', path)['stage'] == 'interrupted'
    with pytest.raises(ValueError):
        speech_voices.voice_dir('custom_../../secrets')
    with pytest.raises(ValueError):
        store.video_dir('../secret')


async def test_cancel_finishes_current_cue_releases_worker_and_retry_reuses_cache(project, monkeypatch):
    _, video, _ = project
    vid = video['id']
    doc = await api.parse_document(vid, ParseSpeechRequest(mode='dialogue', text='Phúc: Một.\nPhúc: Hai.\nPhúc: Ba.'))
    for cue in doc.cues:
        cue.voice_id = vieneu.catalog()[0]['id']
    doc = await api.save_document(vid, doc)
    entered, release = threading.Event(), threading.Event()
    calls = []
    def generate(args, progress):
        item = args['items'][0]
        calls.append(item['id'])
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
            progress({'stage': 'generating'})  # Must not erase the stop request.
        wav(item['output'])
        store.atomic_json(Path(item['output'] + '.json'), {'signature': item['signature']})
        return [{'id': item['id'], 'ok': True}]
    monkeypatch.setattr(vieneu.worker, 'generate', generate)
    await api.generate(vid, GenerateSpeechRequest(revision=doc.revision))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        cancelled = await api.cancel_generation(vid)
        assert cancelled['cancel_requested'] and cancelled['status'] == 'PROCESSING'
        with pytest.raises(HTTPException) as exc:
            await api.generate(vid, GenerateSpeechRequest(revision=doc.revision))
        assert exc.value.status_code == 409
    finally:
        release.set()
    await speech_jobs.drain_jobs()
    job = (await api.get_document(vid))['job']
    assert job['status'] == 'CANCELLED' and job['completed'] == 1
    assert calls == [str(doc.cues[0].id)]
    await api.generate(vid, GenerateSpeechRequest(revision=doc.revision))
    await speech_jobs.drain_jobs()
    assert calls == [str(c.id) for c in doc.cues]
    assert (await api.get_document(vid))['job']['status'] == 'COMPLETED'
    assert not speech.TTS_SEMAPHORE.locked()


async def test_cancel_while_waiting_for_model_does_not_start_inference(project, monkeypatch):
    _, video, _ = project
    vid = video['id']
    doc = await api.parse_document(vid, ParseSpeechRequest(mode='dialogue', text='Phúc: Xin chào.'))
    doc.cues[0].voice_id = vieneu.catalog()[0]['id']
    doc = await api.save_document(vid, doc)
    def forbidden(*args):
        pytest.fail('Cancelled waiting job must not start the model')
    monkeypatch.setattr(vieneu.worker, 'generate', forbidden)
    await speech.TTS_SEMAPHORE.acquire()
    try:
        await api.generate(vid, GenerateSpeechRequest(revision=doc.revision))
        await asyncio.sleep(0)
        assert (await api.cancel_generation(vid))['cancel_requested']
    finally:
        speech.TTS_SEMAPHORE.release()
    await speech_jobs.drain_jobs()
    job = (await api.get_document(vid))['job']
    assert job['status'] == 'CANCELLED' and job['completed'] == 0


def test_custom_voice_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    vid = 'custom_' + str(uuid4())
    directory = speech_voices.voice_dir(vid)
    store.atomic_json(directory / 'profile.json', {'id': vid, 'name': 'Phúc', 'status': 'READY'})
    store.atomic_json(directory / 'voice.json', {'speaker_emb': [1, 2], 'codes': [[1, 2]]})
    wav(directory / 'demo.wav')
    first = speech_voices.resolve_voice(vid)
    assert first == speech_voices.resolve_voice(vid)
    assert speech_voices.custom_voices()[0]['name'] == 'Phúc'
    assert first[1]['voice_id'] == vid


def test_srt_render_keeps_gaps_and_exact_caption_windows(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required')
    from agent.services.post_process import add_scene_narrations, add_scene_subtitles, _media_duration
    source, mixed, result = (tmp_path / n for n in ('source.mp4', 'mixed.mp4', 'result.mp4'))
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=160x284:r=25:d=4', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True, capture_output=True)
    wav(tmp_path / 'cue.wav', 0.5)
    progress = []
    assert add_scene_narrations(str(source), [(str(tmp_path / 'cue.wav'), 1, 1)], str(mixed), progress_callback=progress.append)
    assert progress[-1] == 1
    assert add_scene_subtitles(str(mixed), [{'duration': 1}, {'duration': 1, 'narrator_text': 'Xin chào Việt Nam.'}], str(result))
    assert _media_duration(str(result)) == pytest.approx(4, abs=.05)
    for timestamp, expected in [(0.5, False), (1.2, True), (1.8, True), (2.3, False)]:
        pixels = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(timestamp), '-i', str(result), '-frames:v', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1'], check=True, capture_output=True).stdout
        assert (sum(p < 150 for p in pixels) > 50) == expected


async def test_full_speech_export_with_real_ffmpeg(project, tmp_path, monkeypatch):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required')
    from agent.services import audio_mix, video_assembly, post_process
    from agent.utils.slugify import slugify
    p, v, scene = project
    monkeypatch.setattr(audio_mix, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(video_assembly, 'OUTPUT_DIR', tmp_path)
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=160x284:r=25:d=8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True, capture_output=True)
    await crud.update_scene(scene['id'], vertical_video_url=source.as_uri(), vertical_video_status='COMPLETED')
    scenes = await crud.list_scenes(v['id'])
    cue = Cue(start=1, end=3, text='Xin chào Việt Nam.', voice_id=vieneu.catalog()[0]['id'])
    doc = SpeechDocument(revision=1, timeline=store.timeline_key(scenes), mode='srt', cues=[cue])
    store.atomic_json(store.video_dir(v['id']) / 'script.json', doc.model_dump(mode='json'))
    audio, _, _, signature = speech.cue_audio(v['id'], cue)
    wav(audio, 0.5)
    store.atomic_json(Path(str(audio) + '.json'), {'signature': signature})
    project_dir = tmp_path / slugify(p['name'])
    wav(project_dir / 'music' / 'saved.wav', 2)
    store.atomic_json(project_dir / 'audio_mix' / f"{v['id']}.json", {'music': {'filename': 'saved.wav', 'volume': 0.1}})
    events = []
    result = await audio_mix.finish_audio_mix(v['id'], 'VERTICAL', {'narration': {'speech_revision': 1, 'burn_subtitles': True}}, lambda stage, fraction: events.append((stage, fraction)))
    output = video_assembly.assembled_path(v['id'])
    assert result['ok'] and output.is_file()
    assert result['audio_mix']['narration']['speech_revision'] == 1
    assert post_process._media_duration(str(output)) == pytest.approx(8, abs=.05)
    assert ('narration', 1) in events and ('music', 1) in events and ('subtitles', 1) in events


async def automatic_script(video, cues):
    scenes = await crud.list_scenes(video['id'])
    doc = SpeechDocument(revision=1, mode='srt', timeline=store.timeline_key(scenes), cues=cues)
    for cue in doc.cues:
        cue.voice_id = vieneu.catalog()[0]['id']
    store.atomic_json(store.video_dir(video['id']) / 'script.json', doc.model_dump(mode='json'))
    return doc


def automatic_mocks(monkeypatch, duration):
    calls = []
    def render(args, progress):
        item = args['items'][0]
        calls.append((item['id'], item['text'], args['speed']))
        wav(item['output'], duration(item['text']) / args['speed'])
        store.atomic_json(Path(item['output'] + '.json'), {'signature': item['signature']})
        return [{'id': item['id'], 'ok': True}]
    monkeypatch.setattr(vieneu.worker, 'generate', render)
    monkeypatch.setattr(subtitle_translation, 'executable', lambda _: 'fixture-ai')
    monkeypatch.setattr(subtitle_translation, '_semaphore', asyncio.Semaphore(1))
    return calls


async def test_automatic_shortens_5_seconds_into_3_seconds_and_undo_reuses_audio(project, monkeypatch):
    import json
    _, video, _ = project
    vid = video['id']
    original = await automatic_script(video, [Cue(start=0, end=3.36, text='x' * 89), Cue(start=3.36, end=6, text='Giữ nguyên.')])
    calls = automatic_mocks(monkeypatch, lambda text: 5.03 if text.startswith('x') else .8)
    batches = []
    async def shorten(_role, prompt, **options):
        rows = json.loads(prompt.rsplit('\n', 1)[-1])['cues']
        batches.append(rows)
        return json.dumps({'cues': [{'id': c['id'], 'text': 'Lời rút gọn.'} for c in rows]})
    monkeypatch.setattr(subtitle_translation, 'translate_batch', shorten)
    await api.generate(vid, GenerateSpeechRequest(revision=1, automatic=True))
    await speech_jobs.drain_jobs()
    result = await api.get_document(vid)
    assert result['job']['status'] == 'COMPLETED' and result['job']['progress'] == 100
    assert [a['status'] for a in result['audio']] == ['READY', 'READY']
    assert len(calls) == 3 and len(batches) == 1
    assert batches[0][0]['target_characters'] < 89
    saved = store.load_document(vid)
    assert saved.cues[0].text == 'Lời rút gọn.' and saved.cues[0].start == 0 and saved.cues[0].end == 3.36
    assert saved.cues[1] == original.cues[1]
    assert result['automatic']['original'] == original.model_dump(mode='json')
    # Reopening reconstructs the review from disk. Undo only changes the script,
    # leaves timeline/source untouched, and reuses the original audio signature.
    before = SpeechDocument.model_validate((await api.get_document(vid))['automatic']['original'])
    before.revision = saved.revision
    await api.save_document(vid, before)
    assert (await api.get_document(vid))['automatic'] is None
    assert speech.cue_status(vid, before.cues[0])['audio_url']
    assert len(calls) == 3


async def test_automatic_gap_and_speed_fit_do_not_move_next_cue_or_call_ai(project, monkeypatch):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required')
    _, video, _ = project
    doc = await automatic_script(video, [Cue(start=0, end=1, text='Gap'), Cue(start=3, end=4, text='Speed'), Cue(start=4, end=6, text='Keep')])
    calls = automatic_mocks(monkeypatch, lambda text: {'Gap': 1.5, 'Speed': 1.1, 'Keep': .5}[text])
    async def forbidden(*args, **kwargs):
        pytest.fail('Gap and modest speed repair must not need AI')
    monkeypatch.setattr(subtitle_translation, 'translate_batch', forbidden)
    await api.generate(video['id'], GenerateSpeechRequest(revision=1, automatic=True))
    await speech_jobs.drain_jobs()
    after = store.load_document(video['id'])
    assert after.cues[0].end == 1.5 and after.cues[0].speed == 1
    assert after.cues[1].start == 3 and 1 < after.cues[1].speed <= 1.25
    assert after.cues[2] == doc.cues[2]
    assert len(calls) == 3  # Speed repair reuses the existing speech, without inference.
    assert all(item['status'] == 'READY' for item in (await api.get_document(video['id']))['audio'])


@pytest.mark.parametrize('voice_id', ['v3_binh', 'qwen3_fr_eric'])
def test_retime_reuses_speech_and_valid_quality_without_loading_models(tmp_path, monkeypatch, voice_id):
    import hashlib
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required')
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    if voice_id == 'v3_binh':
        voice_id = vieneu.catalog()[0]['id']
    vid = str(uuid4())
    cue = Cue(start=0, end=1, text=qwen3.sample_text('qwen3_fr_eric'), voice_id=voice_id, speed=1.1)
    source, _, identity, signature = speech.cue_audio(vid, cue)
    wav(source, 1.12)
    original_audio = source.read_bytes()
    original_stamp = source.stat()
    quality = {'version': 1, 'stamp': [original_stamp.st_size, original_stamp.st_mtime_ns],
               'text_hash': hashlib.sha256(cue.text.encode()).hexdigest(), 'language': 'fr',
               'speed': cue.speed, 'error': None, 'recognized': cue.text, 'difference': 0}
    store.atomic_json(source.with_suffix('.wav.json'), {'signature': signature, 'quality': quality})
    after = cue.model_copy(update={'speed': 1.25})
    def forbidden(*args, **kwargs):
        pytest.fail('Changing only speed must not launch TTS or Whisper')
    monkeypatch.setattr(speech_voices, 'generate', forbidden)
    monkeypatch.setattr(qwen3, 'check_audio', forbidden)
    assert speech.retime_cue_audio(vid, cue, after)
    target, _, _, new_signature = speech.cue_audio(vid, after)
    status = speech.cue_status(vid, after)
    assert status['audio_url'] and status['duration'] == pytest.approx(1.12 * 1.1 / 1.25, abs=.04)
    assert source.read_bytes() == original_audio  # Undo still has the original speech.
    metadata = store.read_json(target.with_suffix('.wav.json'))
    assert metadata['signature'] == new_signature
    if identity['engine'] == 'qwen3':
        verdict = qwen3.cached_quality(target, after.text, 'fr', after.speed)
        assert verdict['recognized'] == cue.text and verdict['difference'] == 0 and verdict['error'] is None
    timestamp = target.stat().st_mtime_ns
    assert speech.retime_cue_audio(vid, cue, after) and target.stat().st_mtime_ns == timestamp


@pytest.mark.parametrize('change', ['text', 'voice', 'kokoro', 'invalid_quality'])
def test_retime_does_not_reuse_changed_input_or_rejected_speech(tmp_path, monkeypatch, change):
    import hashlib
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    vid = str(uuid4())
    voice = 'kokoro_af_heart' if change == 'kokoro' else 'qwen3_fr_eric'
    cue = Cue(start=0, end=2, text='Bonjour, bienvenue.', voice_id=voice)
    source, _, _, signature = speech.cue_audio(vid, cue)
    wav(source, 1)
    stamp = source.stat()
    store.atomic_json(source.with_suffix('.wav.json'), {'signature': signature, 'quality': {
        'version': 1, 'stamp': [stamp.st_size, stamp.st_mtime_ns],
        'text_hash': hashlib.sha256(cue.text.encode()).hexdigest(), 'language': 'fr',
        'speed': 1, 'error': 'Lời đọc thiếu câu' if change == 'invalid_quality' else None}})
    changes = {'speed': 1.2}
    if change == 'text':
        changes['text'] = 'Une autre phrase.'
    if change == 'voice':
        changes['voice_id'] = 'qwen3_fr_dylan'
    after = cue.model_copy(update=changes)
    def forbidden(*args, **kwargs):
        pytest.fail('Incompatible or rejected source must not be processed')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    assert not speech.retime_cue_audio(vid, cue, after)
    assert source.is_file() and not speech.cue_audio(vid, after)[0].exists()


async def test_automatic_failed_retime_falls_back_to_generation_without_losing_original(project, monkeypatch):
    _, video, _ = project
    doc = await automatic_script(video, [Cue(start=0, end=1, text='Speed'), Cue(start=1, end=3, text='Keep')])
    calls = automatic_mocks(monkeypatch, lambda text: 1.1 if text == 'Speed' else 2)
    source = speech.cue_audio(video['id'], doc.cues[0])[0]
    def failed(*args):
        raise subprocess.TimeoutExpired('ffmpeg', 90)
    monkeypatch.setattr(speech, 'retime_cue_audio', failed)
    await api.generate(video['id'], GenerateSpeechRequest(revision=1, automatic=True))
    await speech_jobs.drain_jobs()
    state = await api.get_document(video['id'])
    assert state['job']['status'] == 'COMPLETED' and len(calls) == 3
    assert all(item['status'] == 'READY' for item in state['audio'])
    assert speech.cue_status(video['id'], doc.cues[0])['audio_url'] and source.is_file()


def test_retime_publication_failure_keeps_original_and_removes_partial_output(tmp_path, monkeypatch):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required')
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    vid = str(uuid4())
    cue = Cue(start=0, end=2, text='Giữ nguyên giọng.', voice_id=vieneu.catalog()[0]['id'])
    source, _, _, signature = speech.cue_audio(vid, cue)
    wav(source, 1)
    store.atomic_json(source.with_suffix('.wav.json'), {'signature': signature})
    original = source.read_bytes()
    after = cue.model_copy(update={'speed': 1.2})
    def failed(*args):
        raise OSError('Disk write failed')
    monkeypatch.setattr(store, 'atomic_json', failed)
    with pytest.raises(OSError, match='Disk write failed'):
        speech.retime_cue_audio(vid, cue, after)
    target = speech.cue_audio(vid, after)[0]
    assert source.read_bytes() == original and speech.cue_status(vid, cue)['audio_url']
    assert not target.exists() and not target.with_suffix('.wav.json').exists()
    assert not list(source.parent.glob('.*.fit.wav'))


@pytest.mark.parametrize('metadata', ['null', '[]', '{'])
def test_invalid_audio_metadata_cannot_be_retimed(tmp_path, monkeypatch, metadata):
    monkeypatch.setattr(store, 'ROOT', tmp_path)
    vid = str(uuid4())
    cue = Cue(start=0, end=2, text='Xin chào.', voice_id=vieneu.catalog()[0]['id'])
    source = speech.cue_audio(vid, cue)[0]
    wav(source, 1)
    source.with_suffix('.wav.json').write_text(metadata)
    assert not speech.cue_status(vid, cue)['audio_url']
    assert not speech.retime_cue_audio(vid, cue, cue.model_copy(update={'speed': 1.2}))


async def test_automatic_borrows_neighbor_time_for_short_cue_without_ai(project, monkeypatch):
    _, video, _ = project
    original = await automatic_script(video, [
        Cue(start=0, end=2.6, text='First'),
        Cue(start=2.6, end=3.2, text='Short'),
        Cue(start=3.2, end=6, text='Last'),
    ])
    calls = automatic_mocks(monkeypatch, lambda text: {'First': 2.1, 'Short': 1.3, 'Last': 1.8}[text])
    async def forbidden(*args, **kwargs):
        pytest.fail('Nearby spare time is enough; AI must not shorten the script')
    monkeypatch.setattr(subtitle_translation, 'translate_batch', forbidden)
    await api.generate(video['id'], GenerateSpeechRequest(revision=1, automatic=True))
    await speech_jobs.drain_jobs()
    state = await api.get_document(video['id'])
    saved = store.load_document(video['id'])
    assert state['job']['status'] == 'COMPLETED'
    assert [item['status'] for item in state['audio']] == ['READY'] * 3
    assert [cue.text for cue in saved.cues] == [cue.text for cue in original.cues]
    assert [cue.id for cue in saved.cues] == [cue.id for cue in original.cues]
    assert saved.cues[0].start == 0 and saved.cues[-1].end == 6
    assert saved.cues[1].end - saved.cues[1].start >= 1.3
    assert all(abs(after.end - before.end) <= .75 for before, after in zip(original.cues, saved.cues))
    assert len(calls) == 3


async def test_automatic_stops_after_two_rounds_without_truncating_or_dropping_cues(project, monkeypatch):
    import json
    _, video, _ = project
    doc = await automatic_script(video, [Cue(start=0, end=1, text='Lời gốc'), Cue(start=1, end=2, text='Giữ')])
    calls = automatic_mocks(monkeypatch, lambda text: .5 if text == 'Giữ' else 5)
    rounds = []
    async def still_long(_role, prompt, **kwargs):
        rows = json.loads(prompt.rsplit('\n', 1)[-1])['cues']
        rounds.append(rows)
        return json.dumps({'cues': [{'id': c['id'], 'text': f'Vẫn dài {len(rounds)}'} for c in rows]})
    monkeypatch.setattr(subtitle_translation, 'translate_batch', still_long)
    await api.generate(video['id'], GenerateSpeechRequest(revision=1, automatic=True))
    await speech_jobs.drain_jobs()
    state = await api.get_document(video['id'])
    assert len(rounds) == 2 and len(calls) == 4
    assert state['job']['status'] == 'COMPLETED_WITH_ERRORS' and state['automatic']['round'] == 2
    after = store.load_document(video['id'])
    assert [c.id for c in after.cues] == [c.id for c in doc.cues]
    assert after.cues[1] == doc.cues[1]
    assert state['audio'][0]['duration'] == 5 and state['audio'][0]['status'] == 'TOO_LONG'
    with pytest.raises(ValueError, match='vượt khung'):
        speech.prepare_export(video['id'], after.revision, await crud.list_scenes(video['id']))


async def test_automatic_cancel_ai_resume_checkpoint_and_block_concurrent_jobs(project, monkeypatch):
    import json
    _, video, _ = project
    vid = video['id']
    await automatic_script(video, [Cue(start=0, end=1, text='Lời dài'), Cue(start=1, end=3, text='Giữ')])
    calls = automatic_mocks(monkeypatch, lambda text: 5 if text == 'Lời dài' else .5)
    entered, release = asyncio.Event(), asyncio.Event()
    requests = []
    async def slow_ai(_role, prompt, **kwargs):
        rows = json.loads(prompt.rsplit('\n', 1)[-1])['cues']
        requests.append(rows)
        entered.set()
        await release.wait()
        return json.dumps({'cues': [{'id': c['id'], 'text': 'Ngắn'} for c in rows]})
    monkeypatch.setattr(subtitle_translation, 'translate_batch', slow_ai)
    await api.generate(vid, GenerateSpeechRequest(revision=1, automatic=True))
    await asyncio.wait_for(entered.wait(), 5)
    with pytest.raises(HTTPException) as error:
        await api.generate(vid, GenerateSpeechRequest(revision=1))
    assert error.value.status_code == 409
    await api.cancel_generation(vid)
    release.set()
    await speech_jobs.drain_jobs()
    state = await api.get_document(vid)
    assert state['job']['status'] == 'CANCELLED'
    assert state['document']['cues'][0]['text'] == 'Lời dài'
    await api.generate(vid, GenerateSpeechRequest(revision=state['document']['revision'], automatic=True))
    await speech_jobs.drain_jobs()
    assert len(requests) == 1 and len(calls) == 3  # AI checkpoint and both existing WAVs reused.
    assert (await api.get_document(vid))['job']['status'] == 'COMPLETED'


async def test_automatic_discards_ai_result_if_source_timing_changes(project, monkeypatch):
    import json
    _, video, scene = project
    await automatic_script(video, [Cue(start=0, end=1, text='Lời dài'), Cue(start=1, end=3, text='Giữ')])
    automatic_mocks(monkeypatch, lambda text: 5 if text == 'Lời dài' else .5)
    async def change_source(_role, prompt, **kwargs):
        await crud.update_scene(scene['id'], duration=9)
        rows = json.loads(prompt.rsplit('\n', 1)[-1])['cues']
        return json.dumps({'cues': [{'id': c['id'], 'text': 'Ngắn'} for c in rows]})
    monkeypatch.setattr(subtitle_translation, 'translate_batch', change_source)
    await api.generate(video['id'], GenerateSpeechRequest(revision=1, automatic=True))
    await speech_jobs.drain_jobs()
    state = await api.get_document(video['id'])
    assert state['job']['status'] == 'FAILED' and 'mốc video đã đổi' in state['job']['error']
    assert state['document']['cues'][0]['text'] == 'Lời dài'


async def test_automatic_rejects_unsupported_language_before_starting_worker(project):
    _, video, _ = project
    await automatic_script(video, [Cue(start=0, end=3, text='Test')])
    with pytest.raises(HTTPException) as error:
        await api.generate(video['id'], GenerateSpeechRequest(revision=1, automatic=True, language='en'))
    assert error.value.status_code == 422 and 'tiếng Anh' in error.value.detail
    assert not (store.video_dir(video['id']) / 'automatic.json').exists()


async def test_english_automatic_generation_reuses_audio_and_exports_same_timing(project, monkeypatch):
    _, video, _ = project
    vid = video['id']
    doc = await api.parse_document(vid, ParseSpeechRequest(mode='srt', text='1\n00:00:01,000 --> 00:00:03,000\nHello there.\n\n2\n00:00:04,000 --> 00:00:06,000\nWelcome back.'))
    for cue in doc.cues:
        cue.voice_id = 'kokoro_af_heart'
    doc = await api.save_document(vid, doc)
    calls = []
    def render(args, progress):
        item = args['items'][0]
        calls.append((item['id'], args['voice']))
        wav(item['output'], .5)
        store.atomic_json(Path(item['output'] + '.json'), {'signature': item['signature']})
        return [{'id': item['id'], 'ok': True}]
    monkeypatch.setattr(kokoro.worker, 'generate', render)
    monkeypatch.setattr(vieneu.worker, 'generate', lambda *args: pytest.fail('English must use Kokoro'))
    await api.generate(vid, GenerateSpeechRequest(revision=doc.revision, automatic=True, language='en'))
    await speech_jobs.drain_jobs()
    assert (await api.get_document(vid))['job']['status'] == 'COMPLETED'
    tracks, captions, _ = speech.prepare_export(vid, doc.revision, await crud.list_scenes(vid))
    assert [t[1:] for t in tracks] == [(1, 2), (4, 2)]
    assert captions[1]['narrator_text'] == 'Hello there.'
    await api.generate(vid, GenerateSpeechRequest(revision=doc.revision, automatic=True, language='en'))
    await speech_jobs.drain_jobs()
    assert len(calls) == 2
    other = doc.cues[0].model_copy(update={'voice_id': vieneu.catalog()[0]['id']})
    assert speech.cue_audio(vid, other)[0] != speech.cue_audio(vid, doc.cues[0])[0]


async def test_automatic_preserves_failure_reason_and_retries_only_on_next_run(project, monkeypatch):
    _, video, _ = project
    await automatic_script(video, [Cue(start=0, end=3, text='Lời đọc')])
    attempts = []
    def failed(args, progress):
        attempts.append(args['items'][0]['id'])
        return [{'id': attempts[-1], 'ok': False, 'error': 'Model thiếu dữ liệu'}]
    monkeypatch.setattr(vieneu.worker, 'generate', failed)
    for _ in range(2):
        await api.generate(video['id'], GenerateSpeechRequest(revision=1, automatic=True))
        await speech_jobs.drain_jobs()
        state = await api.get_document(video['id'])
        assert state['job']['status'] == 'COMPLETED_WITH_ERRORS'
        assert state['job']['results'][attempts[-1]]['error'] == 'Model thiếu dữ liệu'
    assert len(attempts) == 2


@pytest.mark.parametrize('value', ['{', 'null', '{"revision":1,"round":"bad"}', '{"revision":1,"round":0,"original":{}}'])
async def test_bad_automatic_review_does_not_break_existing_speech_api(project, value):
    _, video, _ = project
    await automatic_script(video, [Cue(start=0, end=3, text='Lời đọc')])
    (store.video_dir(video['id']) / 'automatic.json').write_text(value)
    assert (await api.get_document(video['id']))['automatic'] is None


@pytest.mark.parametrize('automatic', [False, True])
@pytest.mark.parametrize('error_type', [TimeoutError, RuntimeError, OSError])
async def test_worker_failure_continues_next_cue_and_retry_only_missing(project, monkeypatch, automatic, error_type):
    _, video, _ = project
    vid = video['id']
    doc = await automatic_script(video, [Cue(start=0, end=3, text='First'), Cue(start=3, end=6, text='Second')])
    fail, calls = [True], []
    def render(args, progress):
        item = args['items'][0]
        calls.append(item['text'])
        if item['text'] == 'First' and fail[0]:
            raise error_type('Model interrupted')
        wav(item['output'], .5)
        store.atomic_json(Path(item['output'] + '.json'), {'signature': item['signature']})
        return [{'id': item['id'], 'ok': True}]
    monkeypatch.setattr(vieneu.worker, 'generate', render)
    await api.generate(vid, GenerateSpeechRequest(revision=1, automatic=automatic))
    await speech_jobs.drain_jobs()
    state = await api.get_document(vid)
    assert state['job']['status'] == 'COMPLETED_WITH_ERRORS'
    assert state['job']['results'][str(doc.cues[0].id)]['error'] == 'Model interrupted'
    assert state['audio'][1]['status'] == 'READY' and calls == ['First', 'Second']
    fail[0] = False
    await api.generate(vid, GenerateSpeechRequest(revision=1, automatic=automatic))
    await speech_jobs.drain_jobs()
    assert (await api.get_document(vid))['job']['status'] == 'COMPLETED'
    assert calls == ['First', 'Second', 'First']


async def test_last_ai_round_gets_final_speed_fit_preserving_cue_ids(project, monkeypatch):
    import json
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required')
    _, video, _ = project
    doc = await automatic_script(video, [Cue(start=0, end=3.36, text='Original'), Cue(start=3.36, end=6, text='Keep')])
    # Neighbor has no spare time, so the last AI result needs a modest speed fit.
    calls = automatic_mocks(monkeypatch, lambda text: {'Original': 5.03, 'AI 1': 4.5, 'AI 2': 3.6, 'Keep': 2.64}[text])
    rounds = []
    async def shorten(_role, prompt, **kwargs):
        rows = json.loads(prompt.rsplit('\n', 1)[-1])['cues']
        rounds.append(rows)
        return json.dumps({'cues': [{'id': c['id'], 'text': f'AI {len(rounds)}'} for c in rows]})
    monkeypatch.setattr(subtitle_translation, 'translate_batch', shorten)
    await api.generate(video['id'], GenerateSpeechRequest(revision=1, automatic=True))
    await speech_jobs.drain_jobs()
    after = store.load_document(video['id'])
    state = await api.get_document(video['id'])
    assert len(rounds) == 2 and len(calls) == 4
    assert state['job']['status'] == 'COMPLETED' and state['job']['progress'] == 100
    assert 1 < after.cues[0].speed <= 1.25 and after.cues[0].text == 'AI 2'
    assert after.cues[1] == doc.cues[1]
    assert [(c.id, c.start, c.end) for c in after.cues] == [(c.id, c.start, c.end) for c in doc.cues]
    assert all(item['status'] == 'READY' for item in state['audio'])
    assert speech.prepare_export(video['id'], after.revision, await crud.list_scenes(video['id']))[0]


async def test_mixed_engines_are_grouped_without_reordering_script(project, monkeypatch):
    _, video, _ = project
    vid = video['id']
    doc = await automatic_script(video, [Cue(start=i, end=i + 1, text=f'Cue {i}') for i in range(4)])
    for cue, voice in zip(doc.cues, ['qwen3_en_ryan', 'kokoro_af_heart', 'qwen3_en_aiden', kokoro.catalog()[1]['id']]):
        cue.voice_id = voice
    store.atomic_json(store.video_dir(vid) / 'script.json', doc.model_dump(mode='json'))
    calls = []
    def render(args, progress):
        item = args['items'][0]
        calls.append((args['identity']['engine'], item['id']))
        wav(item['output'], .5)
        store.atomic_json(Path(item['output'] + '.json'), {'signature': item['signature']})
        return [{'id': item['id'], 'ok': True}]
    monkeypatch.setattr(speech_voices, 'generate', render)
    monkeypatch.setattr(qwen3, 'audio_warning', lambda *a, **k: None)
    await api.generate(vid, GenerateSpeechRequest(revision=1, automatic=True, language='en'))
    await speech_jobs.drain_jobs()
    assert [engine for engine, _ in calls] == ['kokoro', 'kokoro', 'qwen3', 'qwen3']
    assert [cid for _, cid in calls] == [str(doc.cues[i].id) for i in (1, 3, 0, 2)]
    assert store.load_document(vid) == doc
    assert [item['id'] for item in (await api.get_document(vid))['audio']] == [str(c.id) for c in doc.cues]


async def test_automatic_ai_timeout_preserves_all_wavs_and_resume_only_creates_shortened_cue(project, monkeypatch):
    import json
    _, video, _ = project
    vid = video['id']
    doc = await automatic_script(video, [Cue(start=0, end=1, text='Long'), Cue(start=1, end=3, text='Keep')])
    calls = automatic_mocks(monkeypatch, lambda text: 5 if text == 'Long' else .5)
    async def timeout(*args, **kwargs):
        raise TimeoutError('codex timed out')
    monkeypatch.setattr(subtitle_translation, 'translate_batch', timeout)
    await api.generate(vid, GenerateSpeechRequest(revision=1, automatic=True))
    await speech_jobs.drain_jobs()
    state = await api.get_document(vid)
    assert state['job']['status'] == 'FAILED' and state['job']['current'] is None
    assert 'AI quá thời gian ở câu 1' in state['job']['error']
    assert len(calls) == 2 and all(item['audio_url'] for item in state['audio'])
    assert store.load_document(vid) == doc
    old_audio = [speech.cue_audio(vid, cue)[0] for cue in doc.cues]
    async def correct(_role, prompt, **kwargs):
        rows = json.loads(prompt.rsplit('\n', 1)[-1])['cues']
        return json.dumps({'cues': [{'id': row['id'], 'text': 'Short'} for row in rows]})
    monkeypatch.setattr(subtitle_translation, 'translate_batch', correct)
    await api.generate(vid, GenerateSpeechRequest(revision=1, automatic=True))
    await speech_jobs.drain_jobs()
    after = store.load_document(vid)
    assert (await api.get_document(vid))['job']['status'] == 'COMPLETED'
    assert len(calls) == 3 and calls[-1][1] == 'Short'
    assert after.cues[1] == doc.cues[1] and all(path.is_file() for path in old_audio)
