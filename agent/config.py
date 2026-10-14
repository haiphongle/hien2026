"""Configuration constants."""
import json
import os
import tempfile
from pathlib import Path

# ─── Paths ───────────────────────────────────────────────────
BASE_DIR = Path(os.environ.get("FLOW_AGENT_DIR", Path(__file__).parent.parent))
DB_PATH = BASE_DIR / "flow_agent.db"

# ─── API Server ──────────────────────────────────────────────
API_HOST = os.environ.get("API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("API_PORT", "8100"))

# ─── WebSocket Server (extension connects here) ─────────────
WS_HOST = os.environ.get("WS_HOST", "127.0.0.1")
WS_PORT = int(os.environ.get("WS_PORT", "9222"))


# ─── Flow batchexecute ──────────────────────────────────────
# Every call is signed in the page with the session cookie plus a per-page `at`
# token, so the extension runs it inside a signed-in flow.google.com tab. This
# is the only transport; the REST path it replaced was removed once Flow stopped
# minting the bearer it needed.

# The Flow project every RPC is scoped to. Project creation went with the old
# labs.google tRPC endpoint, so a project is made once in the Flow UI and its
# uuid pinned here; POST /api/projects falls back to it when no id is given.
FLOW_PROJECT_ID = os.environ.get("FLOW_PROJECT_ID", "")

# Capabilities whose payloads were never captured off the new UI (4K upscale,
# reference-to-video, start+end-frame chaining) fail loudly by default. With
# this on, the two that have a sane fallback degrade instead: chaining and r2v
# both drop to plain i2v off the start frame. Upscale has no fallback.
FLOW_ALLOW_DEGRADED = os.environ.get("FLOW_ALLOW_DEGRADED", "0") == "1"

# The tier no longer picks a model — aspect is its own slot and the model names
# are fixed — so it is only carried for the DB column and the dashboard.
DEFAULT_PAYGATE_TIER = os.environ.get("DEFAULT_PAYGATE_TIER", "PAYGATE_TIER_TWO")

# ─── Worker ──────────────────────────────────────────────────
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "5"))
VIDEO_POLL_INTERVAL = int(os.environ.get("VIDEO_POLL_INTERVAL", "10"))  # polling interval for video/upscale status
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "5"))
VIDEO_POLL_TIMEOUT = int(os.environ.get("VIDEO_POLL_TIMEOUT", "420"))
API_COOLDOWN = int(os.environ.get("API_COOLDOWN", "10"))  # seconds between API calls (anti-spam)
MAX_CONCURRENT_REQUESTS = int(os.environ.get("MAX_CONCURRENT_REQUESTS", "5"))  # Google Flow max parallel requests
STALE_PROCESSING_TIMEOUT = int(os.environ.get("STALE_PROCESSING_TIMEOUT", "600"))  # 10 min

# ─── Model Keys (loaded from models.json for easy updates) ──
_MODELS_FILE = Path(__file__).parent / "models.json"
with open(_MODELS_FILE) as _f:
    _MODELS = json.load(_f)

VIDEO_MODELS = _MODELS["video_models"]
UPSCALE_MODELS = _MODELS["upscale_models"]
IMAGE_MODELS = _MODELS["image_models"]
# Nickname from image_models. Known aliases live in models.json, while the
# batch path also accepts syntactically valid Flow wire model ids directly so
# newly introduced image models do not require a FrameWeaver release.
DEFAULT_IMAGE_MODEL = _MODELS.get("default_image_model", "NANO_BANANA_PRO")

# ─── Output Directories ─────────────────────────────────────
OUTPUT_DIR = BASE_DIR / "output"
SHARED_OUTPUT_DIR = OUTPUT_DIR / "_shared"
TTS_TEMPLATES_DIR = SHARED_OUTPUT_DIR / "tts_templates"
MUSIC_OUTPUT_DIR = SHARED_OUTPUT_DIR / "music"

# ─── TTS (OmniVoice) ─────────────────────────────────────────
TTS_MODEL = os.environ.get("TTS_MODEL", "k2-fsa/OmniVoice")
TTS_DEVICE = os.environ.get("TTS_DEVICE", "cpu")  # MPS produces gibberish; CPU+fp32 works
TTS_SAMPLE_RATE = int(os.environ.get("TTS_SAMPLE_RATE", "24000"))

# VieNeu runs in its own environment; never import its dependencies in the API.
VIENEU_DIR = Path(os.environ.get("VIENEU_DIR", BASE_DIR / "VieNeu-TTS-main"))
_VIENEU_EXECUTABLE = Path("Scripts/python.exe" if os.name == "nt" else "bin/python")
_VIENEU_LEGACY_PYTHON = VIENEU_DIR / ".venv" / _VIENEU_EXECUTABLE
VIENEU_PYTHON = os.environ.get("VIENEU_PYTHON", str(
    _VIENEU_LEGACY_PYTHON if _VIENEU_LEGACY_PYTHON.is_file()
    else BASE_DIR / ".flowkit" / "vieneu-venv" / _VIENEU_EXECUTABLE
))
VIENEU_CACHE_DIR = Path(os.environ.get("VIENEU_CACHE_DIR", BASE_DIR / ".flowkit" / "vieneu-cache"))
VIENEU_MODEL = "pnnbao-ump/VieNeu-TTS-v3-Turbo"
VIENEU_PREVIEWS_DIR = SHARED_OUTPUT_DIR / "vieneu_previews"

KOKORO_DIR = Path(os.environ.get("KOKORO_DIR", BASE_DIR / "kokoro-main"))
KOKORO_PYTHON = os.environ.get("KOKORO_PYTHON", str(BASE_DIR / ".flowkit" / "kokoro-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")))
KOKORO_CACHE_DIR = BASE_DIR / ".flowkit" / "kokoro-cache"
KOKORO_PREVIEWS_DIR = SHARED_OUTPUT_DIR / "kokoro_previews"
KOKORO_MODEL = "hexgrad/Kokoro-82M"

QWEN3_PYTHON = os.environ.get("QWEN3_PYTHON", str(BASE_DIR / ".flowkit" / "qwen3-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")))
QWEN3_CACHE_DIR = Path(os.environ.get("QWEN3_CACHE_DIR", BASE_DIR / ".flowkit" / "qwen3-cache"))
QWEN3_PREVIEWS_DIR = SHARED_OUTPUT_DIR / "qwen3_previews"
QWEN3_MODEL = os.environ.get("QWEN3_MODEL", "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice")

# ─── Review / Claude Vision ──────────────────────────────────
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
REVIEW_MODEL = os.environ.get("REVIEW_MODEL", "claude-haiku-4-5-20251001")
REVIEW_FPS_LIGHT = float(os.environ.get("REVIEW_FPS_LIGHT", "4"))
REVIEW_FPS_DEEP = float(os.environ.get("REVIEW_FPS_DEEP", "8"))
REVIEW_MAX_FRAMES = int(os.environ.get("REVIEW_MAX_FRAMES", "64"))
REVIEW_SHEET_COLS = int(os.environ.get("REVIEW_SHEET_COLS", "3"))
REVIEW_SHEET_ROWS = int(os.environ.get("REVIEW_SHEET_ROWS", "3"))

# ─── CLI Providers (video review vision analysis) ────────────
_PROVIDERS_FILE = Path(__file__).parent / "providers.json"
with open(_PROVIDERS_FILE) as _pvf:
    CLI_PROVIDERS = json.load(_pvf)  # mutable dict, hot-reloaded like VIDEO_MODELS
REVIEW_CLI_TIMEOUT_S = float(os.environ.get("REVIEW_CLI_TIMEOUT_S", "120"))

# ─── Suno (Music Generation) — sunoapi.org ──────────────────
SUNO_SETTINGS_FILE = BASE_DIR / ".flowkit" / "suno.json"


def _load_suno_key() -> str:
    """Dashboard key takes precedence, then env and legacy channel rules."""
    try:
        saved = json.loads(SUNO_SETTINGS_FILE.read_text())
        key = saved.get("api_key") if isinstance(saved, dict) else None
        if isinstance(key, str) and key.strip():
            return key.strip()
    except (ValueError, OSError):
        pass
    key = os.environ.get("SUNO_API_KEY", "")
    if key:
        return key
    channels_dir = BASE_DIR / "youtube" / "channels"
    if channels_dir.exists():
        for rules_file in channels_dir.glob("*/channel_rules.json"):
            try:
                rules = json.loads(rules_file.read_text())
                key = rules.get("api_keys", {}).get("suno", "")
                if key:
                    return key
            except (json.JSONDecodeError, OSError):
                continue
    return ""


def save_suno_key(api_key: str) -> None:
    """Persist privately and atomically before publishing the new key in memory."""
    global SUNO_API_KEY
    SUNO_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=SUNO_SETTINGS_FILE.parent, prefix=".suno-")
    try:
        # mkstemp creates the file with owner-only read/write permissions (0600).
        with os.fdopen(fd, "w") as handle:
            json.dump({"api_key": api_key}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, SUNO_SETTINGS_FILE)
    finally:
        Path(temporary).unlink(missing_ok=True)
    SUNO_API_KEY = api_key


SUNO_API_KEY = _load_suno_key()
SUNO_BASE_URL = os.environ.get("SUNO_BASE_URL", "https://api.sunoapi.org")
SUNO_MODEL = os.environ.get("SUNO_MODEL", "V4")
SUNO_CALLBACK_URL = os.environ.get("SUNO_CALLBACK_URL", f"http://{API_HOST}:{API_PORT}/api/music/callback")
SUNO_POLL_INTERVAL = int(os.environ.get("SUNO_POLL_INTERVAL", "5"))
SUNO_POLL_TIMEOUT = int(os.environ.get("SUNO_POLL_TIMEOUT", "600"))
