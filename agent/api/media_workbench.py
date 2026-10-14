"""Import → timed transcript → reviewed translation → existing speech/export editor."""
import errno
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from agent.api.speech import context, ensure_idle
from agent.db import crud
from agent.media_limits import MAX_DURATION, MAX_SPEECH_CUES
from agent.models.speech import SpeechDocument
from agent.services import imported_video, media_jobs as jobs, transcription, subtitle_translation, speech
from agent.services import timeline
from agent.models.timeline import TimelineDocument
from agent.services.speech_store import atomic_json, load_document, read_json, timeline_key

router = APIRouter(tags=["media-workbench"])


class TranscribeRequest(BaseModel):
    revision: int = Field(ge=0)
    model: Literal["small", "base", "large-v3-turbo-q5_0"] = "small"
    language: str = "auto"
    start: float = Field(default=0, ge=0, lt=MAX_DURATION, allow_inf_nan=False)
    end: float | None = Field(default=None, gt=0, le=MAX_DURATION, allow_inf_nan=False)
    restart: bool = False


class TranslateRequest(BaseModel):
    revision: int = Field(ge=1)
    provider: Literal["configured", "codex", "claude", "agy"] = "configured"
    language: str = Field(default="vi", min_length=2, max_length=8)
    synopsis: str = Field(default="", max_length=4000)
    glossary: str = Field(default="", max_length=4000)
    restart: bool = False


class ShortenRequest(BaseModel):
    revision: int = Field(ge=1)
    cue_ids: list[uuid.UUID] = Field(min_length=1, max_length=MAX_SPEECH_CUES)
    provider: Literal["configured", "codex", "claude", "agy"] = "configured"
    synopsis: str = Field(default="", max_length=4000)
    glossary: str = Field(default="", max_length=4000)
    restart: bool = False


@router.get("/media-workbench/import-limits")
async def import_limits():
    """The dashboard uses the same limits as the upload and probe handlers."""
    return {"max_bytes": imported_video.MAX_BYTES, "max_duration_seconds": imported_video.MAX_DURATION,
            "upload_timeout_seconds": imported_video.UPLOAD_TIMEOUT_SECONDS}


def _ensure_upload_space(required_bytes: int):
    free = shutil.disk_usage(jobs.ROOT).free
    if free < required_bytes:
        raise HTTPException(507,
            f"Không đủ dung lượng trống để tải và xử lý video. Cần ít nhất {required_bytes / 1024**3:.1f} GB trống "
            f"(gồm dung lượng dự phòng), hiện còn {free / 1024**3:.1f} GB. Dọn ổ đĩa hoặc chọn video nhỏ hơn.")


@router.post("/projects/{pid}/import-video", status_code=202)
async def upload_video(pid: uuid.UUID, request: Request, filename: str = Query(max_length=255)):
    pid = str(pid)
    if not await crud.get_project(pid):
        raise HTTPException(404, "Không tìm thấy dự án.")
    name = Path(filename.replace("\\", "/")).name
    if Path(name).suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        raise HTTPException(415, "Chọn MP4, MOV, MKV, WebM hoặc M4V.")
    declared = request.headers.get("content-length", "")
    size_error = f"Video tải lên tối đa {imported_video.MAX_BYTES / 1024**3:g} GB."
    if declared.isdigit() and int(declared) > imported_video.MAX_BYTES:
        raise HTTPException(413, size_error)
    jobs.ROOT.mkdir(parents=True, exist_ok=True)
    # Keep room for the upload, a normalized copy, and filesystem headroom.
    _ensure_upload_space((int(declared) if declared.isdigit() else 0) * 2 + imported_video.MIN_FREE_BYTES)
    with tempfile.TemporaryDirectory(prefix="upload-", dir=jobs.ROOT) as temp:
        upload = Path(temp) / "upload"
        total = 0
        next_space_check = 0
        try:
            async with await anyio.open_file(upload, "wb") as file:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > imported_video.MAX_BYTES:
                        raise HTTPException(413, size_error)
                    if total >= next_space_check:
                        _ensure_upload_space(total + len(chunk) + imported_video.MIN_FREE_BYTES)
                        next_space_check = total + 64 * 1024 * 1024
                    await file.write(chunk)
        except OSError as exc:
            if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
                raise HTTPException(507, "Ổ đĩa không còn đủ chỗ để lưu video. Dọn ổ đĩa rồi tải lại.") from exc
            raise
        if not total:
            raise HTTPException(400, "File video trống.")
        try:
            info = await imported_video.probe(upload)
        except (ValueError, RuntimeError, TimeoutError) as exc:
            raise HTTPException(422, str(exc)) from exc
        videos = await crud.list_videos(pid)
        video = await crud.create_video(pid, Path(name).stem, description="Video nhập để tạo phụ đề và lồng tiếng",
                                      display_order=len(videos), orientation=info["orientation"])
        vid = video["id"]
        try:
            directory = jobs.directory(vid)
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / "upload"
            upload.replace(destination)
            info.update(filename=name, size_bytes=total, source_id=str(uuid.uuid4()), status="PREPARING")
            atomic_json(directory / "source.json", info)
            await crud.update_video(vid, status="PROCESSING", duration=info["duration"])
            job = imported_video.prepare(vid, destination, info)
        except BaseException:
            await crud.delete_video(vid)
            shutil.rmtree(jobs.directory(vid), ignore_errors=True)
            raise
        return {"video": await crud.get_video(vid), "job": job}


@router.get("/videos/{vid}/workbench")
async def get_workbench(vid: uuid.UUID):
    vid = str(vid)
    if not await crud.get_video(vid):
        raise HTTPException(404, "Không tìm thấy video.")
    job = jobs.latest(vid)
    saved = timeline.load(vid)
    edit = TimelineDocument.model_validate(saved['document']) if saved and saved['document']['clips'] else None
    info = imported_video.metadata(vid)
    return {"source": imported_video.metadata(vid), "job": job,
            "audio_source": {'kind': 'timeline', 'duration': timeline.duration(edit), 'has_audio': bool(timeline.sound_clips(edit)),
                             'edit_signature': timeline.timing_key(edit)} if edit else
                            {'kind': 'source', 'duration': info['duration'], 'has_audio': info['has_audio']} if info else None,
            "whisper_available": bool(transcription.binary()), "models": transcription.models(),
            "languages": transcription.LANGUAGES, "providers": subtitle_translation.providers(),
            "transcript": read_json(jobs.directory(vid) / "transcript.json"),
            "translation": read_json(jobs.directory(vid) / "translation.json"),
            "shortening": read_json(jobs.directory(vid) / "shortening.json"),
            "transcription_progress": transcription.checkpoint_status(vid),
            "translation_progress": await anyio.to_thread.run_sync(subtitle_translation.checkpoint_status, vid),
            "shortening_progress": await anyio.to_thread.run_sync(subtitle_translation.checkpoint_status, vid, "shortening")}


async def snapshot(vid, revision):
    _, scenes = await context(vid)
    ensure_idle(vid)
    job = jobs.latest(vid)
    if job and job["status"] in jobs.ACTIVE:
        raise HTTPException(409, "Video đang có tác vụ chạy. Đợi xong hoặc bấm Dừng.")
    current = load_document(vid)
    if current.revision != revision:
        raise HTTPException(409, "Kịch bản đã thay đổi. Tải lại để dùng bản mới nhất.")
    return current, scenes


@router.post("/videos/{vid}/workbench/transcribe", status_code=202)
async def transcribe(vid: uuid.UUID, body: TranscribeRequest):
    vid = str(vid)
    doc, scenes = await snapshot(vid, body.revision)
    doc = SpeechDocument(revision=doc.revision, timeline=timeline_key(scenes), mode="srt")
    try:
        return transcription.start(vid, body.model, body.language, doc, scenes,
                                   start_time=body.start, end_time=body.end, restart=body.restart)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/videos/{vid}/workbench/translate", status_code=202)
async def translate(vid: uuid.UUID, body: TranslateRequest):
    vid = str(vid)
    doc, scenes = await snapshot(vid, body.revision)
    if not doc.cues or doc.timeline != timeline_key(scenes):
        raise HTTPException(409, "Lưu phụ đề đúng với video hiện tại trước khi dịch.")
    if body.language not in transcription.LANGUAGES or body.language == "auto":
        raise HTTPException(422, "Chọn ngôn ngữ đích.")
    try:
        speech.validate_document(doc, scenes, vid)
        return subtitle_translation.start(vid, doc, body.provider, body.language, body.synopsis, body.glossary, restart=body.restart)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/videos/{vid}/workbench/shorten", status_code=202)
async def shorten(vid: uuid.UUID, body: ShortenRequest):
    vid = str(vid)
    doc, scenes = await snapshot(vid, body.revision)
    if not doc.cues or doc.timeline != timeline_key(scenes):
        raise HTTPException(409, "Lưu phụ đề đúng với video hiện tại trước khi rút gọn.")
    selected = {str(cid) for cid in body.cue_ids}
    if len(selected) != len(body.cue_ids) or not selected <= {str(c.id) for c in doc.cues}:
        raise HTTPException(422, "Danh sách câu không hợp lệ hoặc bị trùng.")

    def budgets():
        result = {}
        for cue in doc.cues:
            if str(cue.id) not in selected:
                continue
            slot = cue.end - cue.start
            duration = speech.cue_status(vid, cue)["duration"]
            measured = len(cue.text) * slot / duration * .95 if duration else 1500
            result[str(cue.id)] = max(1, int(min(84, slot * 22, measured) + 1e-9))
        return result

    try:
        speech.validate_document(doc, scenes, vid)
        selection = await anyio.to_thread.run_sync(budgets)
        # Audio probing yields to other requests; recheck before reserving the job.
        current, scenes = await snapshot(vid, body.revision)
        if current != doc or current.timeline != timeline_key(scenes):
            raise HTTPException(409, "Kịch bản hoặc mốc video đã đổi. Tải lại trước khi rút gọn.")
        speech.validate_document(current, scenes, vid)
        return subtitle_translation.start(vid, doc, body.provider, "original", body.synopsis,
                                          body.glossary, restart=body.restart, selection=selection)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/videos/{vid}/workbench/cancel")
async def cancel(vid: uuid.UUID):
    if not await crud.get_video(str(vid)):
        raise HTTPException(404, "Không tìm thấy video.")
    return await jobs.cancel(str(vid))
