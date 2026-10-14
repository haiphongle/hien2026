import hashlib
import json
import wave
from pathlib import Path

import pytest
from fastapi import HTTPException

from agent.api import tts as api
from agent.models.tts import NarrateVideoRequest
from agent.services import tts, voice_samples


def write_wav(path, value=0, seconds=0.1):
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(value.to_bytes(2, 'little') * int(24000 * seconds))


async def test_bundled_library_needs_no_vieneu_install_or_ffprobe(monkeypatch):
    monkeypatch.setattr(api.subprocess, 'run', lambda *a, **kw: pytest.fail('Preview must not spawn a subprocess'))
    samples = await api.list_vieneu_voice_samples()
    assert len(samples) == 6
    for sample in samples:
        assert sample['duration'] > 1
        assert sample['sample_text']
        response = await api.get_vieneu_voice_sample_audio(sample['id'])
        assert response.media_type == 'audio/wav'
        assert Path(response.path).is_file()


@pytest.mark.parametrize('sample_id', ['../../secret', 'missing', 'vieneu_binh_north_male/../secret'])
async def test_unlisted_sample_never_serves_arbitrary_path(sample_id):
    with pytest.raises(HTTPException) as exc:
        await api.get_vieneu_voice_sample_audio(sample_id)
    assert exc.value.status_code == 404


async def test_broken_library_is_an_error_not_an_empty_success(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_samples, 'SAMPLES_DIR', tmp_path)
    with pytest.raises(HTTPException) as exc:
        await api.list_vieneu_voice_samples()
    assert exc.value.status_code == 503


def test_corrupt_sample_and_transcript_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_samples, 'SAMPLES_DIR', tmp_path)
    name = voice_samples.VOICE_SAMPLES[0][0]
    (tmp_path / f'{name}.wav').write_bytes(b'not audio')
    (tmp_path / f'{name}.txt').write_text('Hello')
    with pytest.raises(HTTPException) as exc:
        api._get_vieneu_voice_sample(name)
    assert exc.value.status_code == 422


def test_sample_selection_has_reference_and_no_conflicting_style():
    body = NarrateVideoRequest(project_id='p', voice_sample_id='vieneu_ly_north_female', instruct='male')
    instruct, audio, transcript = api._resolve_narration_voice(body, {})
    assert instruct is None
    assert Path(audio).name == 'vieneu_ly_north_female.wav'
    assert transcript
    with pytest.raises(HTTPException) as exc:
        api._resolve_narration_voice(body.model_copy(update={'template': 'another_voice'}), {})
    assert exc.value.status_code == 400


async def test_reference_replacement_and_model_change_invalidate_cache(tmp_path, monkeypatch):
    ref = tmp_path / 'ref.wav'
    write_wav(ref)
    out = tmp_path / 'output'
    out.mkdir()
    scene = {'id': '00000000-0000-0000-0000-000000000001', 'display_order': 0, 'narrator_text': 'Xin chào.'}
    calls = []

    def fake_batch(args, callback):
        calls.append(args)
        results = []
        for item in args['items']:
            write_wav(item['output'])
            Path(item['output'] + '.json').write_text(json.dumps({
                'signature': item['signature'], 'text_hash': item['text_hash'],
            }))
            results.append({'id': item['id'], 'ok': True, 'path': item['output'], 'duration': 0.1})
        return results

    monkeypatch.setattr(tts, '_run_batch_subprocess', fake_batch)
    async def generate():
        return await tts.generate_video_narration([scene], str(out), ref_audio=str(ref), ref_text='Mẫu.', instruct='ignored')

    assert (await generate())[0]['status'] == 'COMPLETED'
    assert (await generate())[0]['duration'] == 0.1
    assert len(calls) == 1
    scene['display_order'] = 4
    await generate()
    assert len(calls) == 1, 'Reordering must reuse the unchanged narration'
    write_wav(ref, value=7)
    await generate()
    assert len(calls) == 2, 'Replacing audio at the same path must regenerate'
    monkeypatch.setattr(tts, 'TTS_MODEL', 'different-model')
    await generate()
    assert len(calls) == 3


def test_malformed_sidecar_does_not_crash_preview(tmp_path):
    path = tmp_path / 'scene.wav'
    write_wav(path)
    meta = Path(str(path) + '.json')
    meta.write_text('[]')
    assert not tts.narration_matches_text(path, 'Hi')
    meta.write_text(json.dumps({'text_hash': hashlib.sha256(b'Hi').hexdigest()}))
    assert tts.narration_matches_text(path, 'Hi')


def test_regeneration_changes_audio_url(tmp_path):
    path = tmp_path / 'scene.wav'
    write_wav(path)
    first = api._scene_audio_url('v', 's', path)
    import os
    timestamp = path.stat().st_mtime_ns + 1_000_000
    os.utime(path, ns=(timestamp, timestamp))
    assert api._scene_audio_url('v', 's', path) != first


async def test_failed_voice_design_keeps_existing_template(tmp_path, monkeypatch):
    from agent.models.tts import VoiceTemplateRequest
    monkeypatch.setattr(api, 'TEMPLATES_DIR', tmp_path)
    monkeypatch.setattr(api, 'TEMPLATES_META', tmp_path / 'templates.json')
    original = tmp_path / 'saved.wav'
    write_wav(original)
    before = original.read_bytes()
    api._save_templates_meta({'saved': {'audio_path': str(original), 'text': 'Original'}})
    before_meta = api.TEMPLATES_META.read_bytes()
    async def fail(**kwargs):
        Path(kwargs['output_path']).write_bytes(b'partial')
        raise RuntimeError('inference failed')
    monkeypatch.setattr(api, 'generate_speech', fail)
    with pytest.raises(RuntimeError):
        await api._generate_and_save_voice_template(VoiceTemplateRequest(name='saved', text='Hello', instruct='male'))
    assert original.read_bytes() == before
    assert api.TEMPLATES_META.read_bytes() == before_meta
    assert not list(tmp_path.glob('.voice-*'))
