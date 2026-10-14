"""English presets share the existing jobs, cache and scene narration pipeline."""
import asyncio
import json
import wave
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from agent.api import tts as narration, vieneu as presets
from agent.models.tts import NarrateVideoRequest, NarrationJobRequest
from agent.services import kokoro, qwen3, speech_voices, tts, vieneu


def test_dispatcher_releases_other_model_before_switching(monkeypatch):
    events = []
    for name, service in [('kokoro', kokoro), ('vieneu', vieneu), ('qwen3', qwen3)]:
        monkeypatch.setattr(service.worker, 'close', lambda name=name: events.append(('close', name)))
        monkeypatch.setattr(service.worker, 'generate', lambda args, callback, name=name: events.append(('generate', name)))
    speech_voices.generate({'identity': {'engine': 'kokoro'}})
    speech_voices.generate({'identity': {'engine': 'vieneu'}})
    assert events == [('close', 'vieneu'), ('close', 'qwen3'), ('generate', 'kokoro'),
                      ('close', 'kokoro'), ('close', 'qwen3'), ('generate', 'vieneu')]


def wav(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x08" * 12000)


def test_catalog_languages_and_cache_identity_without_loading_models(monkeypatch, tmp_path):
    monkeypatch.setattr(kokoro.worker, "generate", lambda *a: pytest.fail("Catalog must not load the model"))
    monkeypatch.setattr(kokoro, "KOKORO_PREVIEWS_DIR", tmp_path)
    voices = kokoro.previews()
    assert len(voices) == len({v['id'] for v in voices}) == 6
    assert {v['region'] for v in voices} == {'Mỹ', 'Anh'}
    assert all(v['languages'] == ['en'] and v['audio_url'] is None for v in voices)
    assert speech_voices.voice_languages(voices[0]['id']) == ['en']
    assert speech_voices.voice_languages(vieneu.catalog()[0]['id']) == ['vi']
    before = kokoro.preview_path(voices[0]['id'])
    monkeypatch.setattr(kokoro, 'SAMPLE_TEXT', 'A new demo text.')
    assert kokoro.preview_path(voices[0]['id']) != before


@pytest.mark.parametrize('voice_id', ['kokoro_../../secret', 'kokoro_af_unknown', 'v3_87e36448ae68274c', None])
async def test_invalid_preset_never_reads_arbitrary_audio(voice_id):
    with pytest.raises(HTTPException) as error:
        await presets.preview_audio(voice_id, 'kokoro')
    assert error.value.status_code == 404


async def test_routes_keep_vieneu_and_add_english_with_engine_validation():
    app = FastAPI()
    app.include_router(presets.router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert len((await client.get('/api/tts/vieneu/voices')).json()) == 25
        assert len((await client.get('/api/tts/kokoro/voices')).json()) == 6
        assert len((await client.get('/api/tts/voices')).json()) == 121
        assert (await client.get('/api/tts/unknown/voices')).status_code == 422


async def test_previews_resume_share_active_job_and_keep_other_engine_separate(monkeypatch, tmp_path):
    monkeypatch.setattr(kokoro, 'KOKORO_PREVIEWS_DIR', tmp_path)
    monkeypatch.setattr(presets, '_tasks', {})
    monkeypatch.setattr(presets, '_jobs', {})
    monkeypatch.setattr(presets, 'TTS_SEMAPHORE', asyncio.Semaphore(1))
    monkeypatch.setattr(kokoro.worker, 'status', lambda: {'available': True})
    calls = []
    fail = [True]
    first = kokoro.catalog()[0]['id']
    wav(kokoro.preview_path(first))

    def render(args, callback):
        item = args['items'][0]
        calls.append(item['id'])
        if item['id'] == 'kokoro_af_bella' and fail[0]:
            return [{'ok': False, 'error': 'test failure'}]
        wav(item['output'])
        return [{'ok': True}]

    monkeypatch.setattr(kokoro.worker, 'generate', render)
    one, two = await asyncio.gather(presets.prepare_previews('kokoro'), presets.prepare_previews('kokoro'))
    assert one is two
    await presets._tasks['kokoro']
    assert (await presets.preview_job('kokoro'))['status'] == 'COMPLETED_WITH_ERRORS'
    assert (await presets.preview_job('vieneu'))['status'] == 'IDLE'
    fail[0] = False
    await presets.prepare_previews('kokoro')
    await presets._tasks['kokoro']
    assert len(calls) == 6 and calls[-1] == 'kokoro_af_bella'
    assert all(v['audio_url'] for v in kokoro.previews())


def test_scene_voice_selection_and_engine_mismatch():
    voice = kokoro.catalog()[0]['id']
    body = NarrateVideoRequest(project_id='p', engine='kokoro', preset_id=voice)
    assert narration._resolve_narration_voice(body, {'narrator_ref_audio': '/missing.wav'}) == (None, None, None)
    for changes in ({'engine': 'vieneu'}, {'template': 'saved'}, {'preset_id': vieneu.catalog()[0]['id']}):
        with pytest.raises(HTTPException):
            narration._resolve_narration_voice(body.model_copy(update=changes), {})


async def test_scene_narration_uses_kokoro_cache_and_progress(monkeypatch, tmp_path):
    calls = []
    events = []
    def render(args, progress):
        calls.append(args['voice'])
        results = []
        for item in args['items']:
            wav(item['output'])
            Path(item['output'] + '.json').write_text(json.dumps({'signature': item['signature'], 'text_hash': item['text_hash'], 'voice': args['identity']}))
            results.append({'id': item['id'], 'ok': True, 'path': item['output'], 'duration': .5})
            if progress:
                progress({'stage': 'generating', 'id': item['id'], 'ok': True})
        return results
    monkeypatch.setattr(kokoro.worker, 'generate', render)
    scene = {'id': '00000000-0000-0000-0000-000000000001', 'display_order': 0, 'narrator_text': 'Hello there.'}
    options = dict(scenes=[scene], output_dir=str(tmp_path), engine='kokoro', preset_id='kokoro_af_heart', progress_callback=events.append)
    results = await tts.generate_video_narration(**options)
    assert results[0]['status'] == 'COMPLETED' and calls == ['af_heart'] and events
    scene['display_order'] = 3
    await tts.generate_video_narration(**options)
    assert len(calls) == 1
    await tts.generate_video_narration(**{**options, 'preset_id': 'kokoro_bm_george'})
    assert calls == ['af_heart', 'bm_george']


async def test_scene_job_checks_kokoro_runtime_and_reports_voice_name(monkeypatch):
    monkeypatch.setattr(narration, 'get_video', AsyncMock(return_value={'project_id': 'p'}))
    monkeypatch.setattr(narration, 'get_project', AsyncMock(return_value={'name': 'test'}))
    monkeypatch.setattr(narration, 'list_scenes', AsyncMock(return_value=[{'id': 's', 'narrator_text': 'Hello.'}]))
    monkeypatch.setattr(narration, '_TTS_JOBS', {})
    monkeypatch.setattr(narration, 'generate_video_narration', AsyncMock(return_value=[]))
    monkeypatch.setattr(kokoro.worker, 'status', lambda: {'available': False, 'reason': 'Unavailable'})
    body = NarrationJobRequest(project_id='p', engine='kokoro', preset_id='kokoro_af_heart')
    with pytest.raises(HTTPException) as error:
        await narration.create_narration_job('v', body)
    assert error.value.status_code == 503 and not narration._TTS_JOBS
    monkeypatch.setattr(kokoro.worker, 'status', lambda: {'available': True})
    created = await narration.create_narration_job('v', body)
    await asyncio.gather(*narration._TTS_JOB_TASKS)
    assert narration._TTS_JOBS[created.job_id]['voice_name'] == 'Heart'
