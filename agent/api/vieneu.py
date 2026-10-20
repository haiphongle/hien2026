"""Native voice catalogs and resumable, shared preview preparation."""
import asyncio
import hashlib
import logging
import threading
import time

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from typing import Literal

from agent.services import speech_voices
from agent.services.tts_runtime import TTS_SEMAPHORE
from agent.services.voice_samples import wav_duration

router = APIRouter(tags=["tts"])
logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task] = {}
_jobs: dict[str, dict] = {}
_cancels: dict[str, threading.Event] = {}
Engine = Literal["vieneu", "kokoro", "qwen3"]


@router.get("/tts/voices")
async def all_voices():
    return await voices("vieneu") + await voices("kokoro") + await voices("qwen3")


@router.get("/tts/{engine}/status")
async def status(engine: Engine = "vieneu"):
    return await asyncio.to_thread(speech_voices.engine_service(engine).worker.status)


@router.get("/tts/{engine}/voices")
async def voices(engine: Engine = "vieneu", language: str | None = None):
    try:
        service = speech_voices.engine_service(engine)
        if engine == "qwen3" and language is not None and language not in service.LANGUAGES:
            raise HTTPException(422, "Ngôn ngữ Qwen3 không được hỗ trợ.")
        return await asyncio.to_thread(service.previews, language) if engine == "qwen3" else await asyncio.to_thread(service.previews)
    except (OSError, ValueError) as exc:
        raise HTTPException(503, f"Không đọc được danh sách giọng {engine}.") from exc


@router.get("/tts/{engine}/voices/{preset_id}/audio")
async def preview_audio(preset_id: str, engine: Engine = "vieneu"):
    try:
        service = speech_voices.engine_service(engine)
        path = service.preview_path(preset_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    duration = wav_duration(path) if path.is_file() else None
    if not duration or (engine == "qwen3" and service.audio_warning(path, service.sample_text(preset_id),
                                                                  service.get_preset(preset_id)["languages"][0], duration=duration)):
        raise HTTPException(404, "Mẫu giọng chưa được chuẩn bị. Bấm Chuẩn bị demo trong thư viện.")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@router.get("/tts/{engine}/previews/job")
async def preview_job(engine: Engine = "vieneu", language: str | None = None):
    if engine == "qwen3" and language is not None and language not in speech_voices.engine_service(engine).LANGUAGES:
        raise HTTPException(422, "Ngôn ngữ Qwen3 không được hỗ trợ.")
    key = f"{engine}:{language or 'en'}" if engine == "qwen3" else engine
    job = _jobs.get(key, {"status": "IDLE", "completed": 0, "total": 0, "stage": "idle", "errors": [], "current_voice": None})
    return {**job, 'elapsed_seconds': round(max(0, job.get('finished_at', time.time()) - job.get('created_at', time.time())))}


@router.post('/tts/{engine}/previews/cancel')
async def cancel_previews(engine: Engine = 'vieneu', language: str | None = None):
    await preview_job(engine, language)  # Validate language before looking up a task.
    key = f"{engine}:{language or 'en'}" if engine == 'qwen3' else engine
    task, event = _tasks.get(key), _cancels.get(key)
    if task and not task.done() and event:
        job = _jobs[key]
        queued = job['status'] == 'QUEUED'
        event.set()
        job.update(status='CANCELLING', stage='cancelling')
        # Queued jobs own no worker. Running threads retain the semaphore until
        # their worker/check finishes cancellation; never interrupt another job.
        if queued:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            job.update(status='CANCELLED', stage='cancelled', current_voice=None, finished_at=time.time())
            if _cancels.get(key) is event:
                _cancels.pop(key, None)
    return await preview_job(engine, language)


@router.post("/tts/{engine}/previews", status_code=202)
async def prepare_previews(engine: Engine = "vieneu", language: str | None = None, preset_id: str | None = None):
    """Generate missing demos once; duplicate submissions share the active task."""
    service = speech_voices.engine_service(engine)
    if engine == "qwen3" and language is not None and language not in service.LANGUAGES:
        raise HTTPException(422, "Ngôn ngữ Qwen3 không được hỗ trợ.")
    if preset_id is not None:
        try:
            preset = service.get_preset(preset_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if engine == "qwen3" and preset["languages"] != [language or "en"]:
            raise HTTPException(422, "Giọng demo không khớp ngôn ngữ đang chọn.")
    key = f"{engine}:{language or 'en'}" if engine == "qwen3" else engine
    if key in _tasks and not _tasks[key].done():
        return _jobs[key]
    ready = await asyncio.to_thread(service.worker.status)
    if not ready["available"]:
        raise HTTPException(503, ready["reason"])
    library = await voices(engine, language or "en" if engine == "qwen3" else None)
    if preset_id is not None:
        library = [voice for voice in library if voice["id"] == preset_id]
    # Re-check after awaits: two simultaneous POSTs must not start two jobs.
    if key in _tasks and not _tasks[key].done():
        return _jobs[key]
    completed = sum(bool(voice["audio_url"]) for voice in library)
    _job = _jobs[key] = {"status": "QUEUED", "completed": completed, "processed": completed, "total": len(library),
            "stage": "queued", "errors": [], "current_voice": None, 'created_at': time.time()}
    cancelled = _cancels[key] = threading.Event()

    async def run():
        try:
            loop = asyncio.get_running_loop()
            def apply_progress(event):
                if not cancelled.is_set() and _job['status'] in {'QUEUED', 'PROCESSING'}:
                    _job["stage"] = event.get("stage", "generating")
            def progress(event):
                loop.call_soon_threadsafe(apply_progress, event)
            for voice in library:
                if cancelled.is_set():
                    break
                if voice["audio_url"]:
                    continue
                _job["current_voice"] = voice["name"]
                _job.update(status='QUEUED', stage='queued')
                # Release between voices so narration requests can use the same model.
                async with TTS_SEMAPHORE:
                    if cancelled.is_set():
                        break
                    _job["status"] = "PROCESSING"
                    preset_id = voice["id"]
                    sample = service.sample_text(preset_id) if engine == "qwen3" else service.SAMPLE_TEXT
                    try:
                        result = await asyncio.to_thread(speech_voices.generate, {
                            "voice": service.get_preset(preset_id)["voice"],
                            "identity": service.voice_identity(preset_id), "speed": 1.0,
                            'cancel_event': cancelled,
                            **({'load_timeout': 180} if engine == 'qwen3' and ready.get('model_downloaded') else {}),
                            "items": [{"id": preset_id, "text": sample,
                                       'preview': True,
                                       "output": str(service.preview_path(preset_id)),
                                       "text_hash": hashlib.sha256(sample.encode()).hexdigest()}],
                        }, progress)
                    except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
                        if cancelled.is_set():
                            break
                        _job["errors"].append(f"{voice['name']}: {exc}")
                        _job["processed"] += 1
                        continue
                _job["processed"] += 1
                if cancelled.is_set():
                    break
                if result[0]["ok"]:
                    _job["completed"] += 1
                else:
                    _job["errors"].append(f"{voice['name']}: {result[0]['error']}")
            _job["status"] = 'CANCELLED' if cancelled.is_set() else "COMPLETED_WITH_ERRORS" if _job["errors"] else "COMPLETED"
            _job["stage"] = 'cancelled' if cancelled.is_set() else "done"
        except asyncio.CancelledError:
            _job["status"] = 'CANCELLED' if cancelled.is_set() else "FAILED"
            _job['stage'] = 'cancelled' if cancelled.is_set() else 'interrupted'
            if not cancelled.is_set():
                _job["errors"].append("Máy chủ đã dừng. Bấm chuẩn bị lại để tiếp tục các mẫu còn thiếu.")
            raise
        except Exception as exc:
            logger.exception("%s preview preparation failed", engine)
            _job["status"] = "FAILED"
            _job["errors"].append(str(exc))
        finally:
            _job.update(finished_at=time.time(), current_voice=None)
            if _cancels.get(key) is cancelled:
                _cancels.pop(key, None)

    _tasks[key] = asyncio.create_task(run())
    return _job


async def shutdown():
    workers = [service.worker for service in speech_voices.ENGINES.values()]
    for worker in workers:
        worker.abort()
    for task in _tasks.values():
        if not task.done():
            task.cancel()
    await asyncio.gather(*_tasks.values(), return_exceptions=True)
    for worker in workers:
        await asyncio.to_thread(worker.close)
