"""Run FFmpeg with timestamp progress and bounded execution time."""

import math
import subprocess
import tempfile
import threading
from collections.abc import Callable
from agent.services.export_cancel import check_cancelled, current_token


def processing_timeout(duration: float, *, minimum: int = 600, multiplier: float = 4) -> int:
    """Allow slower-than-realtime processing while retaining a finite timeout."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid media duration")
    return max(minimum, math.ceil(duration * multiplier))


def run_ffmpeg(command: list[str], duration: float, timeout: float,
               progress_callback: Callable[[float], None] | None = None) -> subprocess.CompletedProcess:
    token = current_token.get()
    check_cancelled()
    if progress_callback is None and token is None:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)

    progress_callback = progress_callback or (lambda value: None)

    command = [command[0], "-nostats", "-progress", "pipe:1", *command[1:]]
    timed_out = threading.Event()
    with tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True, start_new_session=True) as process:
            if token:
                token.register(process.pid)
            def expire():
                timed_out.set()
                process.kill()

            timer = threading.Timer(timeout, expire)
            timer.daemon = True
            timer.start()
            try:
                for line in process.stdout:
                    key, _, value = line.strip().partition("=")
                    if key == "out_time_us":
                        try:
                            fraction = int(value) / (duration * 1_000_000)
                        except (ValueError, ZeroDivisionError):
                            continue
                        # Timestamp completion precedes muxing and file close.
                        progress_callback(max(0.0, min(0.99, fraction)))
                process.wait()
            finally:
                timer.cancel()
                if process.poll() is None:
                    process.kill()
                    process.wait()
                if token:
                    token.unregister(process.pid)
            check_cancelled()
            if timed_out.is_set():
                raise subprocess.TimeoutExpired(command, timeout)
            errors.seek(0, 2)
            errors.seek(max(0, errors.tell() - 8000))
            stderr = errors.read().decode("utf-8", errors="replace")
            if process.returncode == 0:
                progress_callback(1.0)
            return subprocess.CompletedProcess(command, process.returncode, "", stderr)
