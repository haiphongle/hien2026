"""Background audio exports shared by both post-production panels."""

import asyncio
import logging
import time
import uuid
from copy import deepcopy

from agent.services.audio_mix import AudioMixError, finish_audio_mix
from agent.config import SHARED_OUTPUT_DIR
from agent.services.speech_store import atomic_json, identifier, read_json
from agent.services.export_cancel import CancellationToken, ExportCancelled, current_token

logger = logging.getLogger(__name__)
_JOBS: dict[str, dict] = {}  # Latest export for each video; survives page reloads.
_TASKS: set[asyncio.Task] = set()
_TOKENS: dict[str, CancellationToken] = {}
ROOT = SHARED_OUTPUT_DIR / 'audio_exports'
ACTIVE = {"QUEUED", "PROCESSING", 'CANCELLING'}
# Overall percentage measures workflow steps, not elapsed/remaining time.
_RANGES = {
    "waiting": (0, 0), "validating": (1, 5), "downloading": (5, 30),
    "assembling": (30, 40), "narration": (40, 60), "music": (60, 70),
    "layout": (70, 82), "subtitles": (82, 97), "saving": (98, 99),
}


def latest_job(video_id: str) -> dict | None:
    job = _JOBS.get(video_id) or read_json(ROOT / identifier(video_id) / 'job.json')
    if not job:
        return None
    if job['status'] in ACTIVE and video_id not in _TOKENS:
        job.update(status='FAILED', stage='interrupted', error='Máy chủ đã khởi động lại. Các bản đã xuất vẫn được giữ.', finished_at=time.time())
        atomic_json(ROOT / identifier(video_id) / 'job.json', job)
    return {**job, "elapsed_seconds": round((job.get("finished_at") or time.time()) - job["created_at"])}


def cancel_job(video_id: str, job_id: str):
    job = _JOBS.get(video_id)
    token = _TOKENS.get(video_id)
    if not job or job['job_id'] != job_id or job['status'] not in ACTIVE or not token:
        raise AudioMixError(409, 'Tác vụ này đã kết thúc hoặc không còn là tác vụ đang chạy.')
    job.update(status='CANCELLING', stage='cancelling')
    token.cancel()
    try:
        atomic_json(ROOT / identifier(video_id) / 'job.json', job)
    except OSError:
        logger.exception('Could not persist cancellation for %s', job_id)
    return latest_job(video_id)


def start_job(video_id: str, kind: str, orientation: str | None, update: dict, *, edit_export: dict | None = None) -> dict:
    from agent.services import media_jobs
    media = media_jobs.latest(video_id)
    if media and media["status"] in media_jobs.ACTIVE:
        raise AudioMixError(409, "Đợi hoàn tất nhận dạng hoặc kiểm tra âm thanh trước khi ghép video.")
    current = latest_job(video_id)
    if current and current["status"] in ACTIVE:
        raise AudioMixError(409, "This video already has an active audio export")
    # Retain recent outcomes for page reload without unbounded history.
    finished = sorted((j for j in _JOBS.values() if j["status"] not in ACTIVE), key=lambda j: j["created_at"])
    for old in finished[:max(0, len(_JOBS) - 127)]:
        _JOBS.pop(old["video_id"], None)
    job = {
        "job_id": str(uuid.uuid4()), "video_id": video_id, "kind": kind,
        "status": "QUEUED", "stage": "waiting", "progress": 0,
        "stage_progress": 0, "created_at": time.time(), "finished_at": None,
        "error": None, "error_status": None, "result": None,
        'results': [], 'variant_index': 1, 'variant_total': len((edit_export or {}).get('variants', [])) or 1,
    }
    _JOBS[video_id] = job
    update = deepcopy(update)
    edit_export = deepcopy(edit_export)
    token = CancellationToken()
    _TOKENS[video_id] = token
    path = ROOT / identifier(video_id) / 'job.json'
    try:
        atomic_json(path, job)
    except OSError:
        _TOKENS.pop(video_id, None)
        _JOBS.pop(video_id, None)
        raise
    loop = asyncio.get_running_loop()

    def apply(stage: str, fraction: float, variant_index: int):
        if job["status"] not in {'QUEUED', 'PROCESSING'} or job['variant_index'] != variant_index:
            return
        lo, hi = _RANGES[stage]
        fraction = max(0.0, min(1.0, fraction))
        job.update(stage=stage, stage_progress=round(fraction * 100),
                   progress=max(job["progress"], round(((job['variant_index'] - 1) * 100 + lo + (hi - lo) * fraction) / job['variant_total'])),
                   status="QUEUED" if stage == "waiting" else "PROCESSING")
        atomic_json(path, job)

    async def run():
        context = current_token.set(token)
        try:
            variants = edit_export.get('variants', [edit_export]) if edit_export else [None]
            for index, variant in enumerate(variants):
                token.check()
                job['variant_index'] = index + 1
                def report(stage: str, fraction: float, variant_index=index + 1):
                    token.check()
                    loop.call_soon_threadsafe(apply, stage, fraction, variant_index)
                result = await finish_audio_mix(video_id, orientation, update, progress_callback=report,
                                               **({'edit_export': variant} if variant else {}))
                if variant:
                    from agent.services.editing import record_export
                    record_export(video_id, result)
                job['results'].append(result)
                job['result'] = result
                atomic_json(path, job)
            job.update(status="COMPLETED", stage="completed", progress=100, stage_progress=100, result=result)
        except ExportCancelled:
            job.update(status='CANCELLED', stage='cancelled', error=None)
        except Exception as exc:
            logger.exception("Audio export %s failed", job["job_id"])
            job.update(status="FAILED", error=str(exc),
                       error_status=exc.status_code if isinstance(exc, AudioMixError) else 502)
        finally:
            job["finished_at"] = time.time()
            try:
                atomic_json(path, job)
            except OSError:
                logger.exception('Could not persist final export status for %s', job['job_id'])
            finally:
                _TOKENS.pop(video_id, None)
                current_token.reset(context)

    task = asyncio.create_task(run())
    _TASKS.add(task)
    task.add_done_callback(_TASKS.discard)
    return latest_job(video_id)


async def drain_jobs():
    """Keep DB and temporary files alive until FFmpeg closes its output on shutdown."""
    if _TASKS:
        await asyncio.gather(*list(_TASKS), return_exceptions=True)


async def shutdown_jobs():
    for token in list(_TOKENS.values()):
        token.cancel()
    await drain_jobs()
