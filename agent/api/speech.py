"""Persistent character voices, dialogue editor and timed SRT narration."""
import asyncio
import shutil
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from agent.db import crud
from agent.models.speech import GenerateSpeechRequest, ParseSpeechRequest, SpeechDocument
from agent.services import speech, speech_jobs, speech_store as store, speech_voices
from agent.services.audio_mix_jobs import ACTIVE as MIX_ACTIVE, latest_job
from agent.services.imported_video import timeline
from agent.services import timeline as edit_timeline
from agent.models.timeline import TimelineDocument

router = APIRouter(tags=["speech"])


async def context(vid: str):
    try:
        store.identifier(vid)
    except ValueError as exc:
        raise HTTPException(422, "ID video không hợp lệ.") from exc
    video = await crud.get_video(vid)
    if not video:
        raise HTTPException(404, "Không tìm thấy video.")
    try:
        return video, await timeline(vid)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


async def voice_defaults(project_id: str):
    defaults = store.read_json(store.ROOT / "projects" / f"{store.identifier(project_id)}.json", {})
    characters = await crud.get_project_characters(project_id)
    for character in characters:
        value = store.read_json(store.ROOT / "characters" / f"{store.identifier(character['id'])}.json", {})
        if value.get("voice_id"):
            defaults[store.name_key(character["name"])] = value["voice_id"]
    return defaults, characters


def ensure_idle(vid: str):
    from agent.services import media_jobs
    media = media_jobs.latest(vid)
    if media and media["status"] in media_jobs.ACTIVE:
        raise HTTPException(409, "Video đang nhận dạng, dịch hoặc kiểm tra âm thanh. Đợi xong hoặc bấm Dừng.")
    job = speech_jobs.get_job(vid, store.video_dir(vid) / "job.json")
    mix = latest_job(vid)
    if (job and job["status"] in speech_jobs.ACTIVE) or (mix and mix["status"] in MIX_ACTIVE):
        raise HTTPException(409, "Video đang tạo hoặc ghép âm thanh. Đợi hoàn tất rồi sửa kịch bản.")


@router.get("/videos/{vid}/speech")
async def get_document(vid: str):
    video, scenes = await context(vid)
    doc = store.load_document(vid)
    saved = edit_timeline.load(vid)
    edit = TimelineDocument.model_validate(saved['document']) if saved else None
    _, characters = await voice_defaults(video["project_id"])
    return {"document": doc.model_dump(mode="json"), "timeline": store.timeline_key(scenes),
            "duration": edit_timeline.duration(edit) if edit and doc.edit_signature else sum(float(s.get("duration") or 8) for s in scenes),
            "edit_signature": edit_timeline.timing_key(edit) if edit else '',
            "characters": [{"id": c["id"], "name": c["name"]} for c in characters],
            "audio": [speech.cue_status(vid, cue) for cue in doc.cues],
            "automatic": speech.automatic_preview(vid, doc),
            "job": speech_jobs.get_job(vid, store.video_dir(vid) / "job.json")}


@router.post("/videos/{vid}/speech/parse")
async def parse_document(vid: str, body: ParseSpeechRequest):
    video, scenes = await context(vid)
    try:
        cues = store.parse_srt(body.text) if body.mode == "srt" else store.parse_dialogue(body.text, scenes)
        defaults, _ = await voice_defaults(video["project_id"])
        for cue in cues:
            cue.voice_id = defaults.get(store.name_key(cue.speaker), "")
        return SpeechDocument(revision=store.load_document(vid).revision, mode=body.mode,
                              timeline=store.timeline_key(scenes), cues=cues)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/videos/{vid}/speech")
async def save_document(vid: str, body: SpeechDocument, acknowledge_timeline: bool = False):
    video, scenes = await context(vid)
    defaults, characters = await voice_defaults(video["project_id"])
    ensure_idle(vid)
    current = store.load_document(vid)
    if body.revision != current.revision:
        raise HTTPException(409, "Kịch bản đã được sửa ở cửa sổ khác. Tải lại trước khi lưu.")
    if acknowledge_timeline or not body.timeline:
        body.timeline = store.timeline_key(scenes)
    try:
        speech.validate_document(body, scenes, vid)
        for cue in body.cues:
            if cue.voice_id:
                speech_voices.resolve_voice(cue.voice_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    body.revision += 1
    store.atomic_json(store.video_dir(vid) / "script.json", body.model_dump(mode="json"))
    # Preserve defaults only when a speaker has a single chosen voice. Per-line
    # overrides stay in the video document without changing that character globally.
    speakers = {store.name_key(c.speaker) for c in body.cues}
    for speaker in speakers:
        voices = {c.voice_id for c in body.cues if store.name_key(c.speaker) == speaker and c.voice_id}
        if len(voices) == 1:
            chosen = next(iter(voices))
            defaults[speaker] = chosen
            matches = [c for c in characters if store.name_key(c["name"]) == speaker]
            if len(matches) == 1:
                store.atomic_json(store.ROOT / "characters" / f"{matches[0]['id']}.json", {"voice_id": chosen})
    store.atomic_json(store.ROOT / "projects" / f"{video['project_id']}.json", defaults)
    return body


@router.post("/videos/{vid}/speech/jobs", status_code=202)
async def generate(vid: str, body: GenerateSpeechRequest):
    _, scenes = await context(vid)
    ensure_idle(vid)
    doc = store.load_document(vid)
    if doc.revision != body.revision:
        raise HTTPException(409, "Lưu kịch bản mới nhất trước khi tạo giọng.")
    try:
        speech.validate_document(doc, scenes, vid)
        if body.automatic:
            return speech.start_automatic(vid, doc, body)
        return speech.start_generation(vid, doc, [str(c) for c in body.cue_ids] if body.cue_ids is not None else None, body.force)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/videos/{vid}/speech/jobs/cancel")
async def cancel_generation(vid: str):
    await context(vid)
    return speech_jobs.request_cancel(vid, store.video_dir(vid) / "job.json")


@router.get("/videos/{vid}/speech/subtitles.srt")
async def download_srt(vid: str):
    await context(vid)
    doc = store.load_document(vid)
    return Response(store.srt_text(doc.cues), media_type="application/x-subrip; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="subtitles.srt"'})


@router.get("/videos/{vid}/speech/cues/{cid}/audio")
async def cue_audio(vid: str, cid: str):
    await context(vid)
    doc = store.load_document(vid)
    cue = next((c for c in doc.cues if str(c.id) == cid), None)
    if not cue or not speech.cue_status(vid, cue)["audio_url"]:
        raise HTTPException(404, "Chưa có audio cho nội dung và giọng hiện tại.")
    path, _, _, _ = speech.cue_audio(vid, cue)
    return FileResponse(path, media_type="audio/wav")


@router.get("/tts/custom-voices")
async def list_voices():
    return await asyncio.to_thread(speech_voices.custom_voices)


@router.post("/tts/custom-voices", status_code=202)
async def upload_voice(name: str = Form(min_length=1, max_length=80), transcript: str = Form(default="", max_length=2000),
                       file: UploadFile = File()):
    if not name.strip():
        raise HTTPException(422, "Đặt tên cho giọng riêng.")
    vid = "custom_" + str(uuid.uuid4())
    directory = speech_voices.voice_dir(vid)
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / "upload"
    try:
        total = 0
        with source.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > 20 * 1024 * 1024:
                    raise HTTPException(413, "Bản ghi tối đa 20 MB, dài 3–8 giây.")
                target.write(chunk)
        duration = await asyncio.to_thread(speech_voices.normalize_reference, source, directory / "reference.wav")
        profile = {"id": vid, "name": name.strip(), "transcript": transcript.strip(), "duration": duration, "status": "PREPARING"}
        store.atomic_json(directory / "profile.json", profile)
        job = speech_voices.start_clone(vid)
        return {**profile, "job": job}
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, str(exc)) from exc
    finally:
        source.unlink(missing_ok=True)
        await file.close()


@router.post("/tts/custom-voices/{voice_id}/prepare", status_code=202)
async def prepare_voice(voice_id: str):
    try:
        return speech_voices.start_clone(voice_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/tts/custom-voices/{voice_id}/{kind}")
async def custom_audio(voice_id: str, kind: str):
    if kind not in {"audio", "reference"}:
        raise HTTPException(404, "Không tìm thấy audio.")
    try:
        directory = speech_voices.voice_dir(voice_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    path = directory / ("demo.wav" if kind == "audio" else "reference.wav")
    if not path.is_file():
        raise HTTPException(404, "Chưa có bản nghe thử.")
    return FileResponse(path, media_type="audio/wav")
