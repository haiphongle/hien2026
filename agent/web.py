"""Optional production dashboard, mounted after all API routes."""
from pathlib import Path
import asyncio
import os
import signal

from fastapi import HTTPException
from fastapi.responses import FileResponse


async def watch_desktop_parent(stop=None) -> None:
    """Avoid an orphaned backend after the owning desktop process crashes."""
    parent = os.environ.get('FRAMEWEAVER_PARENT_PID')
    if os.environ.get('FRAMEWEAVER_DESKTOP') != '1' or not parent or not parent.isdigit():
        return
    if os.name == 'nt' and callable(stop):
        import sys
        import threading
        import psutil
        loop = asyncio.get_running_loop()
        def control():
            # The owning Electron process closes its private stdin pipe to quit.
            # A daemon reader avoids leaving an executor blocked during shutdown.
            sys.stdin.buffer.read(1)
            try:
                loop.call_soon_threadsafe(stop)
            except RuntimeError:
                pass  # Server already closed.
        if os.environ.get('FRAMEWEAVER_DESKTOP_CONTROL') == 'stdin':
            threading.Thread(target=control, daemon=True).start()
        while psutil.pid_exists(int(parent)):
            await asyncio.sleep(2)
        stop()
        return
    while True:
        await asyncio.sleep(2)
        if os.getppid() != int(parent):
            os.kill(os.getpid(), signal.SIGTERM)
            return


def mount_dashboard(app, directory: Path) -> None:
    root = directory.resolve(strict=True)
    if not (root / "index.html").is_file():
        raise RuntimeError("Missing dashboard build: index.html")

    @app.get("/{path:path}", include_in_schema=False)
    async def dashboard(path: str):
        if path.split("/", 1)[0] in {"api", "ws", "health", "docs", "openapi.json"}:
            raise HTTPException(404)
        target = (root / path).resolve()
        if not target.is_relative_to(root):
            raise HTTPException(404)
        if target.is_file():
            return FileResponse(target)
        # Missing assets must not be replaced by HTML (invalid media responses).
        if path.startswith("assets/") or target.suffix:
            raise HTTPException(404)
        return FileResponse(root / "index.html", headers={"Cache-Control": "no-cache"})
