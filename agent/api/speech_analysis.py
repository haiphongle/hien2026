"""Read-only analysis jobs; applying speaker suggestions uses the speech draft editor."""
import asyncio
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.api.speech import context, ensure_idle
from agent.services import media_jobs, speech_analysis, speech_store

router = APIRouter(tags=["speech-analysis"])


class AnalysisRequest(BaseModel):
    kind: Literal["signal", "words", "speakers"]
    target: Literal["speech", "export"] = "speech"
    revision: int = Field(ge=0)
    model: Literal["base", "small", "large-v3-turbo-q5_0"] = "small"
    language: str = Field(default="vi", max_length=8)
    num_speakers: int = Field(default=0, ge=0, le=10)


@router.get("/videos/{vid}/speech-analysis")
async def status(vid: UUID):
    vid = str(vid)
    _, scenes = await context(vid)
    doc = speech_store.load_document(vid)
    return {"capabilities": speech_analysis.capabilities(), "job": media_jobs.latest(vid),
            "has_export": speech_analysis.export_source(vid)[0].is_file(), "reports": await asyncio.to_thread(speech_analysis.reports, vid, doc, scenes)}


@router.post("/videos/{vid}/speech-analysis", status_code=202)
async def start(vid: UUID, body: AnalysisRequest):
    vid = str(vid)
    _, scenes = await context(vid)
    ensure_idle(vid)
    doc = speech_store.load_document(vid)
    if doc.revision != body.revision:
        raise HTTPException(409, "Kịch bản đã thay đổi. Lưu hoặc tải lại bản mới trước khi kiểm tra.")
    try:
        return speech_analysis.start(vid, doc.model_copy(deep=True), scenes, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/videos/{vid}/speech-analysis/cancel")
async def cancel(vid: UUID):
    await context(str(vid))
    job = media_jobs.latest(str(vid))
    if job and job["kind"] in speech_analysis.KINDS:
        return await media_jobs.cancel(str(vid))
    return job


@router.get("/videos/{vid}/speech-analysis/source/{token}")
async def preview(vid: UUID, token: UUID):
    await context(str(vid))
    directory = media_jobs.directory(str(vid))
    report = speech_store.read_json(directory / "analysis-speakers.json", {})
    path = directory / f"analysis-source-{token}.m4a"
    if report.get("source_audio_id") != str(token) or not path.is_file():
        raise HTTPException(404, "Bản nghe lại không còn khớp với báo cáo hiện tại. Tải lại kết quả.")
    return FileResponse(path, media_type="audio/mp4")
