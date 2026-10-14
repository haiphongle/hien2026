"""Qwen3 language and preview routing without loading its large model."""
import asyncio
import json
import subprocess
import sys
import time
import wave
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from agent.api import vieneu as presets
from agent.services import qwen3, speech_voices, speech_store as store, speech_analysis, transcription
from agent.services.tts_runtime import ManagedTTSWorker
from agent.services.qwen3_worker import synthesize


def wav(path, seconds=3):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\x00\x08" * round(24000 * seconds))


def test_catalog_uses_only_supported_languages_and_distinct_cache_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(qwen3, "QWEN3_PREVIEWS_DIR", tmp_path)
    voices = qwen3.catalog()
    assert len(voices) == len({voice["id"] for voice in voices}) == 90
    assert "vi" not in qwen3.LANGUAGES
    assert len(qwen3.previews("ja")) == 9
    assert speech_voices.voice_languages("qwen3_ja_ono_anna") == ["ja"]
    assert qwen3.voice_identity("qwen3_en_ryan")["language_name"] == "English"
    assert qwen3.preview_path("qwen3_en_ryan") != qwen3.preview_path("qwen3_ja_ryan")
    with pytest.raises(ValueError):
        qwen3.get_preset("qwen3_vi_ryan")
    with pytest.raises(ValueError):
        qwen3.catalog("vi")


@pytest.mark.parametrize("marker,downloaded", [
    ({"model": qwen3.QWEN3_MODEL, "sdk": qwen3.SDK_VERSION, "device": "MPS"}, True),
    ({"model": "different/model", "sdk": qwen3.SDK_VERSION}, False),
    ([], False),
])
def test_status_checks_marker_without_reading_external_files_in_api(monkeypatch, tmp_path, marker, downloaded):
    worker = qwen3.Qwen3Worker()
    worker.cache_dir = tmp_path
    monkeypatch.setattr(qwen3.shutil, "which", lambda _: "/fake/python")
    monkeypatch.setattr(qwen3.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0, qwen3.SDK_VERSION + "\n" + json.dumps(marker)))
    assert worker.status()["model_downloaded"] == downloaded
    assert worker.status()["available"]


def test_permission_probe_timeout_does_not_hang_api(monkeypatch, tmp_path):
    worker = qwen3.Qwen3Worker()
    worker.cache_dir = tmp_path
    monkeypatch.setattr(qwen3.shutil, "which", lambda _: "/fake/python")
    def blocked(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    monkeypatch.setattr(qwen3.subprocess, "run", blocked)
    assert not worker.status()["available"]


def test_start_checks_model_identity_even_without_a_prior_status_request(monkeypatch):
    worker = qwen3.Qwen3Worker()
    monkeypatch.setattr(worker, "status", lambda: {"model_downloaded": False})
    starts = []
    monkeypatch.setattr(ManagedTTSWorker, "_start", lambda self, allow_download=False: starts.append(allow_download))
    worker._start()
    assert starts == [True]


def test_synthesis_bounds_audio_without_mutating_callers_items(monkeypatch):
    worker = qwen3.Qwen3Worker()
    requests = []
    def capture(self, args, callback):
        requests.append(args)
        return []
    monkeypatch.setattr(ManagedTTSWorker, "generate", capture)
    args = {"speed": 1.2, "identity": qwen3.voice_identity("qwen3_en_ryan"),
            "items": [{"id": "a", "text": "Hello.", "output": "demo.wav", "signature": "original"}]}
    worker.generate(args)
    request = requests[0]
    assert request["items"][0] == {**args["items"][0], "max_audio_seconds": 15}
    assert "max_audio_seconds" not in args["items"][0]
    assert request["identity"] == args["identity"] and request["speed"] == 1.2


async def test_abnormal_cached_demo_is_flagged_and_not_served(monkeypatch, tmp_path):
    monkeypatch.setattr(qwen3, "QWEN3_PREVIEWS_DIR", tmp_path)
    wav(qwen3.preview_path("qwen3_en_ono_anna"), seconds=20.8)
    wav(qwen3.preview_path("qwen3_en_serena"), seconds=12)
    voices = {voice["id"]: voice for voice in qwen3.previews("en")}
    assert voices["qwen3_en_ono_anna"]["warning"] and not voices["qwen3_en_ono_anna"]["audio_url"]
    assert voices["qwen3_en_serena"]["audio_url"] and not voices["qwen3_en_serena"]["warning"]
    app = FastAPI()
    app.include_router(presets.router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/api/tts/qwen3/voices/qwen3_en_ono_anna/audio")).status_code == 404
        assert (await client.get("/api/tts/qwen3/voices/qwen3_en_serena/audio")).status_code == 200


async def test_unsupported_preview_language_is_rejected():
    app = FastAPI()
    app.include_router(presets.router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for method, route in (("GET", "voices"), ("GET", "previews/job"), ("POST", "previews")):
            response = await client.request(method, f"/api/tts/qwen3/{route}?language=vi")
            assert response.status_code == 422


async def test_preview_job_generates_only_selected_language(monkeypatch, tmp_path):
    monkeypatch.setattr(qwen3, "QWEN3_PREVIEWS_DIR", tmp_path)
    monkeypatch.setattr(qwen3.worker, "status", lambda: {"available": True})
    monkeypatch.setattr(presets, "_tasks", {})
    monkeypatch.setattr(presets, "_jobs", {})
    monkeypatch.setattr(presets, "TTS_SEMAPHORE", asyncio.Semaphore(1))
    generated = []

    def render(args, callback):
        item = args["items"][0]
        generated.append((item["id"], args["identity"]["language_name"], item["text"]))
        wav(item["output"])
        return [{"id": item["id"], "ok": True}]

    monkeypatch.setattr(speech_voices, "generate", render)
    app = FastAPI()
    app.include_router(presets.router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/tts/qwen3/voices?language=ja")
        assert response.status_code == 200 and len(response.json()) == 9
        assert (await client.post("/api/tts/qwen3/previews?language=ja")).status_code == 202
    await presets._tasks["qwen3:ja"]
    assert len(generated) == 9
    assert all(language == "Japanese" and voice.startswith("qwen3_ja_") for voice, language, _ in generated)
    assert (await presets.preview_job("qwen3", "en"))["status"] == "IDLE"
    assert all(voice["audio_url"] for voice in qwen3.previews("ja"))


def test_qwen_synthesis_timeout_is_bounded_without_changing_other_engines():
    worker = qwen3.Qwen3Worker()
    assert worker._item_timeout({"text": "Hello."}) == 300
    assert worker._item_timeout({"text": "a" * 100}) == 800
    assert worker._item_timeout({"text": "a" * 1500}) == 1800
    assert ManagedTTSWorker("kokoro", "python", Path("."), "model", Path("worker.py"))._item_timeout({}) is None


def test_hung_synthesis_is_stopped_after_model_loading(monkeypatch, tmp_path):
    worker = qwen3.Qwen3Worker()
    worker.cache_dir = tmp_path
    monkeypatch.setattr(worker, "_item_timeout", lambda _: .05)

    def start(**kwargs):
        worker._process = subprocess.Popen([
            sys.executable, "-u", "-c",
            'import json, sys, time; sys.stdin.readline(); '
            'print(json.dumps({"event":"progress","stage":"generating","id":"demo"}), flush=True); '
            'time.sleep(10)',
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)

    monkeypatch.setattr(worker, "_start", start)
    started = time.monotonic()
    with pytest.raises(TimeoutError):
        worker.generate({"items": [{"id": "demo", "text": "Hello.", "output": str(tmp_path / "demo.wav")}]})
    assert time.monotonic() - started < 3
    assert worker._process is None and worker.error
    assert not (tmp_path / "demo.wav").exists()


async def test_preview_timeout_keeps_cached_demos_and_continues_next_voice(monkeypatch, tmp_path):
    monkeypatch.setattr(qwen3, "QWEN3_PREVIEWS_DIR", tmp_path)
    monkeypatch.setattr(qwen3.worker, "status", lambda: {"available": True})
    monkeypatch.setattr(presets, "_tasks", {})
    monkeypatch.setattr(presets, "_jobs", {})
    monkeypatch.setattr(presets, "TTS_SEMAPHORE", asyncio.Semaphore(1))
    for voice in qwen3.catalog("en")[:7]:
        wav(qwen3.preview_path(voice["id"]))
    calls = []
    fail = [True]

    def render(args, callback):
        item = args["items"][0]
        calls.append(item["id"])
        if item["id"] == "qwen3_en_ono_anna" and fail[0]:
            raise TimeoutError("Quá thời gian tạo tiếng.")
        wav(item["output"])
        return [{"ok": True}]

    monkeypatch.setattr(speech_voices, "generate", render)
    await presets.prepare_previews("qwen3", "en")
    await presets._tasks["qwen3:en"]
    job = await presets.preview_job("qwen3", "en")
    assert job["status"] == "COMPLETED_WITH_ERRORS" and job["completed"] == 8
    assert job["processed"] == 9
    assert job["errors"] == ["Ono Anna: Quá thời gian tạo tiếng."]
    assert calls == ["qwen3_en_ono_anna", "qwen3_en_sohee"]
    fail[0] = False
    await presets.prepare_previews("qwen3", "en")
    await presets._tasks["qwen3:en"]
    assert calls[-1] == "qwen3_en_ono_anna" and len(calls) == 3
    assert all(voice["audio_url"] for voice in qwen3.previews("en"))


@pytest.mark.parametrize("durations", [(3,), (16, 3), (16, 16)])
def test_long_audio_retry_is_bounded_and_preserves_language(durations):
    import numpy as np

    calls, events = [], []
    class Model:
        def generate_custom_voice(self, **kwargs):
            calls.append(kwargs)
            return [np.full(durations[len(calls) - 1] * 10, .1)], 10

    item = {"id": "dylan", "text": "Bonjour.", "max_audio_seconds": 15}
    if durations[-1] > 15:
        with pytest.raises(ValueError, match=r"2 lần.*16.0s.*15.0s"):
            synthesize(Model(), item, "French", "Dylan", lambda **event: events.append(event))
    else:
        audio, rate, attempts = synthesize(Model(), item, "French", "Dylan", lambda **event: events.append(event))
        assert len(audio) / rate == 3 and attempts == len(durations)
    assert len(calls) == len(durations)
    assert all(call["language"] == "French" and call["speaker"] == "Dylan" and call["text"] == "Bonjour." for call in calls)
    assert all(15 * 12.5 < call["max_new_tokens"] < 2048 for call in calls)
    assert len(events) == len(durations) - 1
    if len(durations) == 2:
        assert calls[1]["temperature"] < .9 and events[0]["stage"] == "retrying"


@pytest.mark.parametrize("audio", [[], [float("nan")]])
def test_invalid_audio_is_not_saved_or_retried(audio):
    class Model:
        def generate_custom_voice(self, **kwargs):
            return [audio], 24000
    with pytest.raises(ValueError, match="âm thanh trống hoặc không hợp lệ"):
        synthesize(Model(), {"id": "x", "text": "Hello.", "max_audio_seconds": 15}, "English", "Ryan", lambda **event: None)


def test_token_limit_cannot_turn_truncated_long_speech_into_success():
    import numpy as np
    class Model:
        def generate_custom_voice(self, **kwargs):
            return [np.full(2047 * 1920, .1)], 24000
    with pytest.raises(ValueError, match="2 lần.*giới hạn 162.7s"):
        synthesize(Model(), {"id": "x", "text": "Hello. " * 100, "max_audio_seconds": 245}, "English", "Ryan", lambda **event: None)


async def test_single_demo_validates_language_and_reuses_saved_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(qwen3, "QWEN3_PREVIEWS_DIR", tmp_path)
    monkeypatch.setattr(qwen3.worker, "status", lambda: {"available": True})
    monkeypatch.setattr(presets, "_tasks", {})
    monkeypatch.setattr(presets, "_jobs", {})
    monkeypatch.setattr(presets, "TTS_SEMAPHORE", asyncio.Semaphore(1))
    calls = []
    def render(args, callback):
        item = args["items"][0]
        calls.append(item["id"])
        wav(item["output"])
        return [{"ok": True}]
    monkeypatch.setattr(speech_voices, "generate", render)
    app = FastAPI()
    app.include_router(presets.router, prefix="/api")
    route = "/api/tts/qwen3/previews?language=fr&preset_id=qwen3_fr_dylan"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        for voice in ("qwen3_en_dylan", "qwen3_fr_missing"):
            assert (await client.post(f"/api/tts/qwen3/previews?language=fr&preset_id={voice}")).status_code == 422
        responses = await asyncio.gather(client.post(route), client.post(route))
        assert all(response.status_code == 202 for response in responses)
        await presets._tasks["qwen3:fr"]
        job = await presets.preview_job("qwen3", "fr")
        assert job["completed"] == job["processed"] == job["total"] == 1
        assert job["status"] == "COMPLETED" and calls == ["qwen3_fr_dylan"]
        assert (await client.post(route)).status_code == 202
        await presets._tasks["qwen3:fr"]
        assert calls == ["qwen3_fr_dylan"]
        assert (await client.get("/api/tts/qwen3/voices/qwen3_fr_dylan/audio")).status_code == 200


def test_silent_synthesis_retries_once_and_is_never_accepted():
    import numpy as np
    calls, events = [], []
    class Model:
        def generate_custom_voice(self, **kwargs):
            calls.append(kwargs)
            return [np.zeros(24000)], 24000
    with pytest.raises(ValueError, match='2 lần.*không có tiếng rõ'):
        synthesize(Model(), {'id': 'x', 'text': 'Hello.', 'max_audio_seconds': 15},
                   'English', 'Ryan', lambda **event: events.append(event))
    assert len(calls) == 2 and events[0]['stage'] == 'retrying'


@pytest.mark.parametrize('heard,valid', [('Bonjour, bienvenue dans cette petite histoire.', True), ('Ce petit histoire', False)])
def test_suspicious_short_speech_uses_existing_asr_and_persists_quality(monkeypatch, tmp_path, heard, valid):
    from agent.services.speech_analysis_core import compare_words
    path = tmp_path / 'short.wav'
    text = qwen3.sample_text('qwen3_fr_eric')
    model_dir = tmp_path / 'models'
    model_dir.mkdir()
    (model_dir / 'ggml-small.bin').touch()
    monkeypatch.setattr(transcription, 'MODEL_DIR', model_dir)
    monkeypatch.setattr(transcription, 'binary', lambda: 'fixture-whisper')
    calls = []
    async def recognize(items, model, language, work, update, **options):
        calls.append((items, model, language, options))
        assert items[0]['text'] == text and Path(items[0]['path']).is_file()
        return [{'recognized': heard, **compare_words(text, heard)}]
    monkeypatch.setattr(speech_analysis, 'recognize', recognize)
    def render(self, args, callback):
        item = args['items'][0]
        wav(item['output'], 1.12)
        store.atomic_json(Path(item['output'] + '.json'), {'signature': 'keep-signature'})
        return [{'id': item['id'], 'ok': True}]
    monkeypatch.setattr(ManagedTTSWorker, 'generate', render)
    events = []
    result = qwen3.Qwen3Worker().generate({'identity': qwen3.voice_identity('qwen3_fr_eric'), 'speed': 1,
        'items': [{'id': 'demo', 'text': text, 'output': str(path)}]}, events.append)[0]
    assert result['ok'] == valid
    assert len(calls) == 1 and calls[0][1:3] == ('small', 'fr')
    assert calls[0][3] == {'cpu': True, 'minimum_timeout': 180}
    assert events == [{'stage': 'checking_audio'}]
    metadata = store.read_json(path.with_suffix('.wav.json'))
    assert metadata['signature'] == 'keep-signature' and metadata['quality']['recognized'] == heard
    assert bool(qwen3.audio_warning(path, text, 'fr')) == (not valid)
    assert len(calls) == 1  # Reading the cached quality cannot run ASR again.
    wav(path, 1.12)
    assert qwen3.audio_warning(path, text, 'fr')  # Old quality cannot validate a replaced WAV.


def test_regular_audio_does_not_load_asr_and_missing_asr_blocks_only_suspect(monkeypatch, tmp_path):
    path = tmp_path / 'audio.wav'
    text = qwen3.sample_text('qwen3_fr_eric')
    monkeypatch.setattr(transcription, 'MODEL_DIR', tmp_path / 'missing-models')
    monkeypatch.setattr(speech_analysis, 'recognize', lambda *a, **k: pytest.fail('No ASR for normal output'))
    wav(path, 3)
    assert qwen3.check_audio(path, text, 'fr', 1) is None
    wav(path, 1.12)
    assert 'Cần Whisper' in qwen3.check_audio(path, text, 'fr', 1)


async def test_short_legacy_demo_cannot_be_counted_ready_or_served(monkeypatch, tmp_path):
    monkeypatch.setattr(qwen3, 'QWEN3_PREVIEWS_DIR', tmp_path)
    path = qwen3.preview_path('qwen3_fr_eric')
    wav(path, 1.12)
    sample = next(v for v in qwen3.previews('fr') if v['id'] == 'qwen3_fr_eric')
    assert sample['warning'] and not sample['audio_url']
    app = FastAPI()
    app.include_router(presets.router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get('/api/tts/qwen3/voices/qwen3_fr_eric/audio')).status_code == 404


def test_silent_and_asr_failure_are_cached_as_invalid_without_destroying_audio(monkeypatch, tmp_path):
    path = tmp_path / 'silent.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b'\x00\x00' * 24000)
    assert 'không có tiếng rõ' in qwen3.check_audio(path, 'Hello there.', 'en', 1)
    assert path.is_file() and qwen3.audio_warning(path, 'Hello there.', 'en')
    wav(path, .2)
    (tmp_path / 'ggml-small.bin').touch()
    monkeypatch.setattr(transcription, 'MODEL_DIR', tmp_path)
    monkeypatch.setattr(transcription, 'binary', lambda: 'fixture-whisper')
    async def timeout(*args, **kwargs):
        raise TimeoutError('ASR timed out')
    monkeypatch.setattr(speech_analysis, 'recognize', timeout)
    assert qwen3.check_audio(path, 'Hello there.', 'en', 1) == 'ASR timed out'
    assert qwen3.audio_warning(path, 'Hello there.', 'en') == 'ASR timed out'


@pytest.mark.parametrize('data', ['{', 'null', '[]', '{"quality":[]}'])
def test_bad_quality_metadata_does_not_break_voice_library(monkeypatch, tmp_path, data):
    monkeypatch.setattr(qwen3, 'QWEN3_PREVIEWS_DIR', tmp_path)
    path = qwen3.preview_path('qwen3_fr_eric')
    wav(path, 1.12)
    path.with_suffix('.wav.json').write_text(data)
    sample = next(v for v in qwen3.previews('fr') if v['id'] == 'qwen3_fr_eric')
    assert sample['warning'] and not sample['audio_url']
