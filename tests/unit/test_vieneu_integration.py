"""Contract tests for engine selection, cache isolation and recoverable previews."""
import asyncio
import json
import wave
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.api import tts as api, vieneu as native_api
from agent.models.tts import NarrateVideoRequest, NarrationJobRequest
from agent.services import tts, vieneu


def wav(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(b"\0\0" * 4800)


def test_catalog_has_regions_and_does_not_load_engine(monkeypatch):
    monkeypatch.setattr(vieneu.worker, "generate", lambda *a: pytest.fail("Catalog must not load a model"))
    voices = vieneu.previews()
    assert len(voices) == len({v["id"] for v in voices}) == 25
    assert {v["region"] for v in voices} == {"Bắc", "Trung", "Nam"}
    assert not any("voice" in v or "speaker_emb" in v for v in voices)


def test_missing_source_uses_bundled_catalog_without_changing_cache_keys(tmp_path, monkeypatch):
    bundled = vieneu._BUNDLED_CATALOG.read_bytes()
    source = tmp_path / "src/vieneu/assets/voices_v3_turbo.json"
    source.parent.mkdir(parents=True)
    source.write_bytes(bundled)
    monkeypatch.setattr(vieneu, "VIENEU_DIR", tmp_path)
    expected = [(v["id"], vieneu.voice_identity(v["id"]), vieneu.preview_path(v["id"]).name)
                for v in vieneu.catalog()]
    source.unlink()
    actual = [(v["id"], vieneu.voice_identity(v["id"]), vieneu.preview_path(v["id"]).name)
              for v in vieneu.catalog()]
    assert len(actual) == 25 and actual == expected


def test_native_selection_never_inherits_project_reference():
    preset = vieneu.catalog()[0]["id"]
    body = NarrateVideoRequest(project_id="p", engine="vieneu", preset_id=preset)
    assert api._resolve_narration_voice(body, {"narrator_ref_audio": "/missing.wav", "narrator_voice": "male"}) == (None, None, None)
    for update in ({"template": "saved"}, {"voice_sample_id": "vieneu_ly_north_female"}, {"preset_id": None}, {"engine": "omnivoice"}):
        with pytest.raises(HTTPException) as exc:
            api._resolve_narration_voice(body.model_copy(update=update), {})
        assert exc.value.status_code == 400


async def test_preview_rejects_paths_and_corrupt_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(vieneu, "VIENEU_PREVIEWS_DIR", tmp_path)
    for preset in ("../../secret", "v3_0000000000000000"):
        with pytest.raises(HTTPException) as exc:
            await native_api.preview_audio(preset)
        assert exc.value.status_code == 404
    preset = vieneu.catalog()[0]["id"]
    vieneu.preview_path(preset).write_bytes(b"broken")
    with pytest.raises(HTTPException):
        await native_api.preview_audio(preset)


async def test_scene_cache_separates_engines_presets_text_and_speed(tmp_path, monkeypatch):
    calls = []

    def render(args, progress):
        calls.append(args)
        results = []
        for item in args["items"]:
            wav(item["output"])
            Path(item["output"] + ".json").write_text(json.dumps({"signature": item["signature"], "text_hash": item["text_hash"]}))
            results.append({"id": item["id"], "ok": True, "path": item["output"], "duration": 0.1})
        return results

    monkeypatch.setattr(vieneu.worker, "generate", render)
    monkeypatch.setattr(tts, "_run_batch_subprocess", render)
    scene = {"id": "00000000-0000-0000-0000-000000000001", "display_order": 0, "narrator_text": "Xin chào."}
    preset, other = [v["id"] for v in vieneu.catalog()[:2]]

    async def generate(**kwargs):
        return await tts.generate_video_narration([scene], str(tmp_path), **kwargs)

    await generate(engine="vieneu", preset_id=preset)
    first = len(calls)
    scene["display_order"] = 5
    await generate(engine="vieneu", preset_id=preset)
    assert len(calls) == first, "Reordering must reuse narration"
    await generate(engine="vieneu", preset_id=other)
    await generate(engine="vieneu", preset_id=other, speed=1.2)
    scene["narrator_text"] = "Chào buổi sáng."
    await generate(engine="vieneu", preset_id=other, speed=1.2)
    await generate(engine="omnivoice")
    assert len(calls) == first + 4


async def test_loading_progress_does_not_mark_scene_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "get_video", AsyncMock(return_value={"project_id": "p"}))
    monkeypatch.setattr(api, "get_project", AsyncMock(return_value={"name": "test"}))
    scene = {"id": "s", "display_order": 0, "narrator_text": "Xin chào."}
    monkeypatch.setattr(api, "list_scenes", AsyncMock(return_value=[scene]))
    monkeypatch.setattr(api, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(api, "_TTS_JOBS", {})
    monkeypatch.setattr(api, "_TTS_SEMAPHORE", asyncio.Semaphore(1))
    monkeypatch.setattr(vieneu.worker, "status", lambda: {"available": True})
    observed = []

    async def generate(**kwargs):
        kwargs["progress_callback"]({"stage": "loading_model"})
        kwargs["progress_callback"]({"stage": "generating", "id": "s"})
        await asyncio.sleep(0)
        job = next(iter(api._TTS_JOBS.values()))
        observed.append((job["completed"], list(job["scenes"]), job["current_scene_id"]))
        return [{"scene_id": "s", "display_order": 0, "status": "FAILED", "error": "example failure"}]

    monkeypatch.setattr(api, "generate_video_narration", generate)
    created = await api.create_narration_job("v", NarrationJobRequest(project_id="p", engine="vieneu", preset_id=vieneu.catalog()[0]["id"]))
    await asyncio.gather(*api._TTS_JOB_TASKS)
    assert observed == [(0, [], "s")]
    job = await api.get_narration_job(created.job_id)
    assert job["status"] == "COMPLETED_WITH_ERRORS"
    assert job["voice_name"] == vieneu.catalog()[0]["name"]


async def test_preview_preparation_resumes_and_deduplicates(tmp_path, monkeypatch):
    monkeypatch.setattr(vieneu, "VIENEU_PREVIEWS_DIR", tmp_path)
    monkeypatch.setattr(native_api, "_tasks", {})
    monkeypatch.setattr(native_api, "_jobs", {})
    monkeypatch.setattr(native_api, "TTS_SEMAPHORE", asyncio.Semaphore(1))
    monkeypatch.setattr(vieneu.worker, "status", lambda: {"available": True})
    library = vieneu.catalog()[:3]
    wav(vieneu.preview_path(library[0]["id"]))
    monkeypatch.setattr(native_api, "voices", AsyncMock(return_value=vieneu.previews()[:3]))
    calls = []

    def render(args, callback):
        item = args["items"][0]
        calls.append(item["id"])
        wav(item["output"])
        return [{"ok": True}]

    monkeypatch.setattr(vieneu.worker, "generate", render)
    one, two = await asyncio.gather(native_api.prepare_previews(), native_api.prepare_previews())
    assert one is two
    await native_api._tasks["vieneu"]
    assert calls == [v["id"] for v in library[1:]]
    assert (await native_api.preview_job())["completed"] == 3
    assert (await native_api.preview_job())["status"] == "COMPLETED"
