"""Read-only application resources and writable user data.

Source checkouts keep their existing layout. Desktop sets FRAMEWEAVER_DATA_DIR
so no settings, databases or generated media are written into a signed .app.
"""
import os
from pathlib import Path
import tempfile

RESOURCE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FRAMEWEAVER_DATA_DIR") or
                os.environ.get("FLOW_AGENT_DIR") or RESOURCE_DIR).expanduser().resolve()
DESKTOP = os.environ.get("FRAMEWEAVER_DESKTOP") == "1"


def config_file(name: str, *, seed: bool = False) -> Path:
    folder = DATA_DIR / "config" if DESKTOP else RESOURCE_DIR / "agent"
    if DESKTOP:
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = folder / name
    if seed and not target.exists():
        # Publish a complete file exclusively; a crash must not leave a partial
        # JSON file or overwrite settings created by another process.
        content = (RESOURCE_DIR / "agent" / name).read_bytes()
        fd, temporary = tempfile.mkstemp(prefix='.defaults-', dir=folder)
        try:
            with os.fdopen(fd, 'wb') as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
        finally:
            Path(temporary).unlink(missing_ok=True)
    return target
