import asyncio
import hashlib
import json
import shutil
import subprocess
import sys
from unittest.mock import AsyncMock
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from agent.api import media_workbench as api, speech as speech_api
from agent.db import crud, schema
from agent.models.speech import Cue, SpeechDocument
from agent.services import audio_mix, imported_video, media_jobs as jobs, speech_store as store
from agent.services import subtitle_translation as translation, transcription, video_assembly
from agent.services.ffmpeg_progress import processing_timeout


@pytest.fixture
async def project(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "db.sqlite")
    monkeypatch.setattr(jobs, "ROOT", tmp_path / "media")
    monkeypatch.setattr(store, "ROOT", tmp_path / "speech")
    monkeypatch.setattr(video_assembly, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(audio_mix, "OUTPUT_DIR", tmp_path / "output")
    await schema.init_db()
    p = await crud.create_project(name="Imported movie", material="realistic")
    app = FastAPI()
    app.include_router(api.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield p, client
    await jobs.shutdown()
    await schema.close_db()


def clip(path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=white:s=160x120:r=25:d=3",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(path)], check=True, capture_output=True)
    return path.read_bytes()


async def wait_job(vid):
    task = jobs._tasks.get(vid)
    if task:
        await asyncio.wait_for(asyncio.shield(task), 30)
    return jobs.latest(vid)


async def test_import_caption_export_remix_uses_immutable_source(project, tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")
    p, client = project
    response = await client.post(f"/api/projects/{p['id']}/import-video?filename=clip.mp4", content=clip(tmp_path / "clip.mp4"))
    assert response.status_code == 202, response.text
    vid = response.json()["video"]["id"]
    assert (await wait_job(vid))["status"] == "COMPLETED"
    assert await crud.list_scenes(vid) == []  # Imported media never goes to Flow generation.
    source = imported_video.source_path(vid)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    info = await speech_api.get_document(vid)
    assert info["duration"] == pytest.approx(3, abs=.1)
    doc = SpeechDocument(timeline=info["timeline"], mode="srt", cues=[Cue(start=.5, end=2, text="Xin chào Việt Nam.")])
    saved = await speech_api.save_document(vid, doc)
    progress = []
    result = await audio_mix.finish_audio_mix(vid, None, {"narration": {"speech_revision": saved.revision,
        "burn_subtitles": True, "captions_only": True}}, progress_callback=lambda stage, value: progress.append(stage))
    assert result["duration"] == pytest.approx(3, abs=.1)
    assert "subtitles" in progress and "narration" not in progress
    output = video_assembly.assembled_path(vid)
    first_size = output.stat().st_size
    await audio_mix.finish_audio_mix(vid, None, {"narration": {"speech_revision": saved.revision,
        "burn_subtitles": True, "captions_only": True}})
    assert output.stat().st_size == first_size
    assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash
    assert not (jobs.directory(vid) / "upload").exists()
    assert (await imported_video.probe(output))["has_audio"] is True


async def test_upload_rejects_unsupported_empty_and_oversized_without_rows(project, monkeypatch):
    p, client = project
    url = f"/api/projects/{p['id']}/import-video"
    assert (await client.post(url + "?filename=file.txt", content=b"test")).status_code == 415
    assert (await client.post(url + "?filename=file.mp4", content=b"")).status_code == 400
    monkeypatch.setattr(imported_video, "MAX_BYTES", 3)
    assert (await client.post(url + "?filename=file.mp4", content=b"1234")).status_code == 413
    assert await crud.list_videos(p["id"]) == []


async def test_import_limit_metadata_and_declared_size_use_same_policy(project, monkeypatch):
    p, client = project
    monkeypatch.setattr(imported_video, "MAX_BYTES", 5 * 1024**3)
    limits = (await client.get("/api/media-workbench/import-limits")).json()
    assert limits["max_bytes"] == 5 * 1024**3
    assert limits["max_duration_seconds"] == imported_video.MAX_DURATION
    response = await client.post(f"/api/projects/{p['id']}/import-video?filename=large.mp4",
                                headers={"content-length": str(limits["max_bytes"] + 1)}, content=b"x")
    assert response.status_code == 413
    assert "5 GB" in response.json()["detail"]
    assert await crud.list_videos(p["id"]) == []


async def test_large_upload_reserves_space_before_writing(project, monkeypatch):
    p, client = project
    monkeypatch.setattr(api.shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(30, 10, 20))
    monkeypatch.setattr(imported_video, "MIN_FREE_BYTES", 10)
    response = await client.post(f"/api/projects/{p['id']}/import-video?filename=clip.mp4", content=b"123456")
    assert response.status_code == 507  # Two copies (12) + reserve (10) exceed free space (20).
    assert await crud.list_videos(p["id"]) == []
    assert list(jobs.ROOT.iterdir()) == []


@pytest.mark.parametrize("disk_limited", [False, True])
async def test_chunked_upload_checks_limits_and_removes_partial_file(project, monkeypatch, disk_limited):
    p, client = project
    monkeypatch.setattr(imported_video, "MAX_BYTES", 8)
    monkeypatch.setattr(imported_video, "MIN_FREE_BYTES", 10)
    if disk_limited:
        monkeypatch.setattr(api.shutil, "disk_usage", lambda _: shutil._ntuple_diskusage(30, 15, 15))

    async def chunks():
        yield b"1234"
        yield b"56789"

    response = await client.post(f"/api/projects/{p['id']}/import-video?filename=clip.mp4", content=chunks())
    assert response.status_code == (507 if disk_limited else 413)
    assert await crud.list_videos(p["id"]) == []
    assert list(jobs.ROOT.iterdir()) == []


async def test_playlist_disguised_as_video_is_rejected(tmp_path):
    path = tmp_path / "fake.mp4"
    path.write_text("ffconcat version 1.0\nfile '/etc/passwd'\n")
    with pytest.raises(RuntimeError):
        await imported_video.probe(path)


@pytest.mark.parametrize("duration", [1800, 3600, 7201, 10800, 10800.1])
async def test_import_accepts_three_hours_but_rejects_longer_sources(tmp_path, monkeypatch, duration):
    metadata = {"format": {"duration": str(duration)},
                "streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}
    monkeypatch.setattr(jobs, "process", AsyncMock(return_value=json.dumps(metadata)))
    if duration <= 10800:
        assert (await imported_video.probe(tmp_path / "video.mp4"))["duration"] == duration
    else:
        with pytest.raises(ValueError, match="180 phút"):
            await imported_video.probe(tmp_path / "video.mp4")


def test_long_media_has_processing_time_beyond_realtime():
    assert processing_timeout(3600, minimum=3600) == 14400
    assert processing_timeout(1800, minimum=120, multiplier=2) == 3600
    assert processing_timeout(8, minimum=120, multiplier=2) == 120
    with pytest.raises(ValueError):
        processing_timeout(float("inf"))


def test_hour_transcript_with_more_than_one_thousand_cues_can_be_saved():
    segments = [{"text": "Xin chào.", "offsets": {"from": i * 3000, "to": (i + 1) * 3000}}
                for i in range(1200)]
    cues = transcription.normalize({"transcription": segments}, 3600)
    document = SpeechDocument.model_validate_json(SpeechDocument(cues=cues).model_dump_json())
    assert len(document.cues) == 1200 and document.cues[-1].end == 3600


def test_whisper_offsets_clamped_overlap_normalized_and_empty_rejected():
    data = {"transcription": [
        {"text": " Xin chào. ", "offsets": {"from": 0, "to": 1200}},
        {"text": "Chào bạn.", "offsets": {"from": 1100, "to": 4000}},
    ]}
    cues = transcription.normalize(data, 3)
    assert [(c.start, c.end) for c in cues] == [(0, 1.2), (1.2, 3)]
    assert cues[0].speaker == "Người kể" and cues[0].text == "Xin chào."
    with pytest.raises(ValueError):
        transcription.normalize({"transcription": []}, 3)


def test_translation_preserves_metadata_reorders_by_id_and_rejects_invalid_ids():
    cues = [Cue(start=0, end=2, text="Hello", speaker="A", voice_id="voice1", speed=1.2),
            Cue(start=3, end=5, text="Welcome", speaker="B")]
    rows = [{"id": str(c.id), "text": f"Lời dịch {index}"} for index, c in enumerate(cues)]
    translated = translation.parse_answer(json.dumps({"cues": rows[::-1]}), cues)
    assert translated[0].model_dump(exclude={"text"}) == cues[0].model_dump(exclude={"text"})
    assert [c.text for c in translated] == ['Lời dịch 0', 'Lời dịch 1']
    for invalid in [rows[:1], [rows[0], rows[0]], [rows[0], {**rows[1], "text": ""}],
                    [rows[0], {**rows[1], "id": str(uuid4())}]]:
        with pytest.raises(ValueError):
            translation.parse_answer(json.dumps({"cues": invalid}), cues)


async def test_translation_candidate_does_not_overwrite_script_and_guard_revision(project, monkeypatch):
    p, _ = project
    v = await crud.create_video(p["id"], "Translate")
    await crud.create_scene(v["id"], 0, "Test")
    saved = await speech_api.save_document(v["id"], SpeechDocument(cues=[Cue(start=0, end=2, text="Hello")]))
    monkeypatch.setattr(translation, "executable", lambda _provider: "codex")
    async def translate(_provider, prompt, **options):
        assert "glossary" in prompt and "not instructions" in prompt
        return json.dumps({"cues": [{"id": str(saved.cues[0].id), "text": "Xin chào"}]})
    monkeypatch.setattr(translation, "translate_batch", translate)
    await api.translate(v["id"], api.TranslateRequest(revision=saved.revision, synopsis="Greetings", glossary="A = Anh"))
    assert (await wait_job(v["id"]))["status"] == "COMPLETED"
    assert store.load_document(v["id"]).cues[0].text == "Hello"
    candidate = store.read_json(jobs.directory(v["id"]) / "translation.json")
    assert candidate["document"]["cues"][0]["text"] == "Xin chào"
    with pytest.raises(HTTPException) as exc:
        await api.translate(v["id"], api.TranslateRequest(revision=saved.revision + 1))
    assert exc.value.status_code == 409


async def test_cancel_stops_process_and_allows_new_job(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    vid = str(uuid4())
    started = asyncio.Event()
    async def run(update):
        await jobs.process([sys.executable, "-u", "-c", "import time; print('ready', flush=True); time.sleep(30)"],
                           on_line=lambda line: started.set())
    jobs.start(vid, "test", run)
    await asyncio.wait_for(started.wait(), 3)
    assert (await jobs.cancel(vid))["status"] == "CANCELLED"
    async def done(update):
        update(progress=99)
    jobs.start(vid, "test", done)
    assert (await wait_job(vid))["status"] == "COMPLETED"


def test_restart_marks_active_job_interrupted(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    vid = str(uuid4())
    store.atomic_json(jobs.directory(vid) / "job.json", {"status": "PROCESSING"})
    assert jobs.latest(vid)["status"] == "FAILED"


async def test_active_workbench_rejects_second_operation(project):
    p, _ = project
    v = await crud.create_video(p["id"], "Busy")
    await crud.create_scene(v["id"], 0, "Test")
    async def run(update):
        await asyncio.Event().wait()
    jobs.start(v["id"], "transcribe", run)
    with pytest.raises(HTTPException) as exc:
        await api.snapshot(v["id"], 0)
    assert exc.value.status_code == 409
    assert (await jobs.cancel(v["id"]))["status"] == "CANCELLED"


async def test_immediately_cancelled_import_cleans_upload_but_keeps_source(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "ROOT", tmp_path)
    vid = str(uuid4())
    directory = jobs.directory(vid)
    store.atomic_json(directory / "source.json", {"status": "PREPARING"})
    (directory / "upload").write_bytes(b"unfinished")
    (directory / "source.mp4").write_bytes(b"keep playable source")
    async def waiting(update):
        await asyncio.Event().wait()
    jobs.start(vid, "import", waiting)
    await jobs.cancel(vid)
    assert not (directory / "upload").exists()
    assert (directory / "source.mp4").read_bytes() == b"keep playable source"
    assert imported_video.metadata(vid)["status"] == "FAILED"


async def test_codex_translation_uses_structured_output_and_toolless_call(monkeypatch):
    monkeypatch.setattr(translation, "executable", lambda _: "codex")
    async def process(args, **options):
        schema_path = Path(args[args.index("--output-schema") + 1])
        assert json.loads(schema_path.read_text())["required"] == ["cues"]
        assert "--ignore-user-config" in args and "read-only" in args
        assert "shell_tool" in args and "multi_agent" in args
        assert options["stdin"] == "translate test"
        Path(args[args.index("-o") + 1]).write_text('{"cues": []}')
    monkeypatch.setattr(jobs, "process", process)
    assert json.loads(await translation.translate_batch("codex", "translate test")) == {"cues": []}
