"""Small persisted TTS job runner; page/server reloads never leave a fake spinner."""
import asyncio
import logging
import time
import uuid
from pathlib import Path

from agent.services.speech_store import atomic_json, read_json

ACTIVE = {"QUEUED", "PROCESSING"}
_tasks: dict[str, asyncio.Task] = {}
_cancel_requested: set[str] = set()
logger = logging.getLogger(__name__)


def get_job(key: str, path: Path):
    job = read_json(path)
    if job and job["status"] in ACTIVE and key not in _tasks:
        job.update(status="FAILED", stage="interrupted", error="Máy chủ đã khởi động lại. Bấm tạo lại để tiếp tục các câu còn thiếu.")
        atomic_json(path, job)
    return job


def start_job(key: str, path: Path, total: int, run):
    if key in _tasks:
        raise ValueError("Đang có tác vụ tạo giọng. Vui lòng đợi hoàn tất.")
    job = {"job_id": str(uuid.uuid4()), "status": "QUEUED", "stage": "queued", "total": total,
           "completed": 0, "current": None, "results": {}, "error": None, "created_at": time.time()}
    atomic_json(path, job)

    def update(**changes):
        job.update(changes)
        if key in _cancel_requested:
            job["cancel_requested"] = True
        atomic_json(path, job)

    async def execute():
        try:
            await run(job, update)
            if job.get("cancel_requested"):
                update(status="CANCELLED", stage="cancelled", current=None)
                return
            failed = any(not result.get("ok") for result in job["results"].values())
            update(status="COMPLETED_WITH_ERRORS" if failed else "COMPLETED", stage="done", current=None)
        except asyncio.CancelledError:
            update(status="FAILED", stage="interrupted", error="Tác vụ bị dừng. Có thể tạo lại các câu còn thiếu.")
            raise
        except Exception as exc:
            logger.exception("Speech job failed: %s", key)
            update(status="FAILED", current=None, error=str(exc))
        finally:
            _tasks.pop(key, None)
            _cancel_requested.discard(key)

    _tasks[key] = asyncio.create_task(execute())
    return job


def request_cancel(key: str, path: Path):
    """Stop between cues so the worker thread and TTS semaphore stay owned."""
    job = get_job(key, path)
    if job and key in _tasks:
        _cancel_requested.add(key)
        atomic_json(path, {**job, "cancel_requested": True})
        return {**job, "cancel_requested": True}
    return job


def cancel_requested(key: str):
    return key in _cancel_requested


async def drain_jobs():
    if _tasks:
        await asyncio.gather(*list(_tasks.values()), return_exceptions=True)
