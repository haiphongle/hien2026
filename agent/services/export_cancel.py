"""Cooperative cancellation shared across async workers and FFmpeg threads."""
import asyncio
import os
import signal
import threading
from contextlib import asynccontextmanager
from contextvars import ContextVar


class ExportCancelled(Exception):
    pass


class CancellationToken:
    def __init__(self):
        self.event = threading.Event()
        self.lock = threading.Lock()
        self.pids = set()

    def check(self):
        if self.event.is_set():
            raise ExportCancelled('Đã hủy xuất video.')

    def register(self, pid):
        with self.lock:
            self.pids.add(pid)
            if self.event.is_set():
                self._kill(pid)

    def unregister(self, pid):
        with self.lock:
            self.pids.discard(pid)

    @staticmethod
    def _kill(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def cancel(self):
        self.event.set()
        with self.lock:
            for pid in self.pids:
                self._kill(pid)


current_token: ContextVar[CancellationToken | None] = ContextVar('export_cancel', default=None)


def check_cancelled():
    token = current_token.get()
    if token:
        token.check()


async def cancellable_download(awaitable):
    """Interrupt network waits without cancelling an FFmpeg thread or file copy."""
    token = current_token.get()
    if not token:
        return await awaitable
    task = asyncio.create_task(awaitable)
    try:
        while True:
            token.check()
            done, _ = await asyncio.wait({task}, timeout=.25)
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def export_lock(lock):
    while True:
        check_cancelled()
        try:
            await asyncio.wait_for(lock.acquire(), .25)
            break
        except asyncio.TimeoutError:
            continue
    try:
        check_cancelled()
        yield
    finally:
        lock.release()
