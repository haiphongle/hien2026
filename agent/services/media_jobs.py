"""Persisted, cancellable local import/ASR/translation jobs."""
import asyncio
import os
import signal
import time
import uuid
from pathlib import Path

from agent.config import SHARED_OUTPUT_DIR
from agent.services.speech_store import atomic_json, identifier, read_json

ROOT = SHARED_OUTPUT_DIR / "media_workbench"
ACTIVE = {"QUEUED", "PROCESSING"}
_tasks: dict[str, asyncio.Task] = {}


def busy():
    return bool(_tasks)


def directory(vid: str) -> Path:
    return ROOT / identifier(vid)


def latest(vid: str):
    path = directory(vid) / "job.json"
    job = read_json(path)
    if job and job["status"] in ACTIVE and vid not in _tasks:
        job.update(status="FAILED", error="Máy chủ đã khởi động lại. Bạn có thể chạy lại tác vụ.")
        atomic_json(path, job)
        if job.get("kind") == "import":
            import_failure_cleanup(vid)
    return job


def import_failure_cleanup(vid: str):
    """Clean only this job's uncommitted upload, never its playable source."""
    directory_path = directory(vid)
    info = read_json(directory_path / "source.json")
    if info and info.get("status") != "READY":
        atomic_json(directory_path / "source.json", {**info, "status": "FAILED"})
        (directory_path / "upload").unlink(missing_ok=True)
        (directory_path / "preparing.mp4").unlink(missing_ok=True)


def start(vid: str, kind: str, run, *, metadata=None):
    if vid in _tasks:
        raise ValueError("Video đang được xử lý. Đợi hoàn tất hoặc bấm Dừng.")
    path = directory(vid) / "job.json"
    job = dict(id=str(uuid.uuid4()), kind=kind, status="QUEUED", progress=0,
               stage="Đang chờ", error=None, created_at=time.time())
    job.update(metadata or {})

    def update(**changes):
        job.update(changes)
        atomic_json(path, job)

    async def execute():
        try:
            update(status="PROCESSING")
            await run(update)
            update(status="COMPLETED", progress=100, stage="Hoàn tất")
        except asyncio.CancelledError:
            update(status="CANCELLED", stage="Đã dừng", error=None)
            raise
        except Exception as exc:
            update(status="FAILED", error=str(exc))
        finally:
            _tasks.pop(vid, None)

    update()
    task = asyncio.create_task(execute())
    _tasks[vid] = task
    def finished(done):
        if _tasks.get(vid) is done:
            _tasks.pop(vid, None)
        if done.cancelled() and job["status"] in ACTIVE:
            update(status="CANCELLED", stage="Đã dừng")
        if kind == "import" and job["status"] != "COMPLETED":
            import_failure_cleanup(vid)
    task.add_done_callback(finished)
    return job


async def cancel(vid: str):
    task = _tasks.get(vid)
    if task:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return latest(vid)


async def shutdown():
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def process(args: list[str], *, timeout=1800, on_line=None, cwd=None, stdin=None):
    """Drain output continuously; cancellation/timeout also stops subprocess children."""
    from agent.services.export_cancel import current_token, check_cancelled
    token = current_token.get()
    check_cancelled()
    proc = await asyncio.create_subprocess_exec(
        *args, cwd=cwd, start_new_session=True,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        limit=1024 * 1024,
    )
    if token:
        token.register(proc.pid)
    tail = ""

    async def consume():
        nonlocal tail
        if stdin is not None:
            proc.stdin.write(stdin.encode())
            await proc.stdin.drain()
            proc.stdin.close()
        while line := await proc.stdout.readline():
            text = line.decode(errors="replace")
            tail = (tail + text)[-1024 * 1024:]
            if on_line:
                on_line(text)
        await proc.wait()

    try:
        try:
            await asyncio.wait_for(consume(), timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"{Path(args[0]).name} quá thời gian chờ ({timeout}s). Có thể chạy lại tác vụ.") from exc
        check_cancelled()
        if proc.returncode:
            raise RuntimeError(f"{Path(args[0]).name} không xử lý được file: {tail[-1200:]}")
        return tail
    finally:
        if proc.returncode is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if token:
            token.unregister(proc.pid)
        await proc.wait()
