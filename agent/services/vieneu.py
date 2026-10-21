"""VieNeu catalog and a managed, reusable worker in an isolated Python environment."""
import atexit
import hashlib
import json
import logging
import shutil
import subprocess
import time
from functools import lru_cache
from pathlib import Path

from agent.config import VIENEU_DIR, VIENEU_PYTHON, VIENEU_CACHE_DIR, VIENEU_MODEL, VIENEU_PREVIEWS_DIR
from agent.services.voice_samples import wav_duration
from agent.services.tts_runtime import ManagedTTSWorker

logger = logging.getLogger(__name__)
SDK_VERSION = "3.8.3"
SAMPLE_TEXT = "Xin chào bạn. Hãy chậm lại một chút, lắng nghe và tận hưởng những điều bình dị quanh mình."
_BUNDLED_CATALOG = Path(__file__).resolve().parents[1] / "assets/vieneu/voices_v3_turbo.json"


def catalog() -> list[dict]:
    """Read the bundled catalog without importing or loading the model."""
    path = VIENEU_DIR / "src/vieneu/assets/voices_v3_turbo.json"
    if not path.is_file():
        path = _BUNDLED_CATALOG
    stat = path.stat()
    return _catalog(path, stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=2)
def _catalog(path: Path, mtime: int, size: int) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    result = []
    for name, entry in data["presets"].items():
        if not entry.get("speaker_emb") or not entry.get("codes"):
            continue
        description = entry.get("description", "")
        region = entry.get("region", "Khác")
        result.append({
            "id": "v3_" + hashlib.sha256(name.encode()).hexdigest()[:16],
            "name": name, "gender": entry.get("gender", ""), "region": region,
            "languages": ["vi"],
            "description": description, "style": entry.get("style", "tu_nhien"),
            "featured": entry.get("featured"), "voice": entry,
        })
    return sorted(result, key=lambda v: (v["featured"] is None, v["featured"] or 0))


def get_preset(preset_id: str) -> dict:
    for item in catalog():
        if item["id"] == preset_id:
            return item
    raise ValueError("Giọng VieNeu không tồn tại. Vui lòng chọn lại trong thư viện.")


def voice_identity(preset_id: str) -> dict:
    preset = get_preset(preset_id)
    digest = hashlib.sha256(json.dumps(preset["voice"], sort_keys=True).encode()).hexdigest()
    return {"engine": "vieneu", "model": VIENEU_MODEL, "sdk": SDK_VERSION,
            "precision": "fp32", "sample_rate": 48000, "preset_id": preset_id,
            "voice_name": preset["name"], "preset_hash": digest}


def preview_path(preset_id: str) -> Path:
    key = hashlib.sha256(json.dumps({**voice_identity(preset_id), "text": SAMPLE_TEXT}, sort_keys=True).encode()).hexdigest()[:16]
    return VIENEU_PREVIEWS_DIR / f"{preset_id}_{key}.wav"


def previews() -> list[dict]:
    result = []
    for preset in catalog():
        path = preview_path(preset["id"])
        duration = wav_duration(path) if path.is_file() else None
        result.append({
            **{key: value for key, value in preset.items() if key != "voice"},
            "sample_text": SAMPLE_TEXT, "duration": duration,
            "audio_url": f"/api/tts/vieneu/voices/{preset['id']}/audio?v={path.stem}" if duration else None,
        })
    return result


class VieNeuWorker(ManagedTTSWorker):
    def __init__(self):
        super().__init__("vieneu", VIENEU_PYTHON, VIENEU_CACHE_DIR, VIENEU_MODEL,
                         Path(__file__).with_name("vieneu_worker.py"))

    def status(self) -> dict:
        if self._probe is None or time.monotonic() - self._probe_time > 30:
            executable = shutil.which(VIENEU_PYTHON)
            available = False
            reason = "Chưa cài môi trường VieNeu."
            if executable:
                try:
                    probe = subprocess.run([executable, "-I", "-c", "import importlib.metadata as m; import onnxruntime, soundfile, sea_g2p; print(m.version('vieneu'))"], capture_output=True, text=True, timeout=10)
                    available = probe.returncode == 0 and probe.stdout.strip() == SDK_VERSION
                    reason = None if available else f"Cần VieNeu {SDK_VERSION} và các thư viện ONNX trong môi trường riêng."
                except (OSError, subprocess.TimeoutExpired):
                    reason = "Không kiểm tra được môi trường VieNeu."
            self._probe = {"available": available, "reason": reason}
            self._probe_time = time.monotonic()
        ready = (self.cache_dir / "ready.json").is_file()
        return {**self._probe, "engine": "vieneu", "model": VIENEU_MODEL,
                "sdk_version": SDK_VERSION, "sample_rate": 48000, "device": "CPU / ONNX FP32",
                "model_downloaded": ready, "state": self.state, "error": self.error}

worker = VieNeuWorker()
atexit.register(worker.close)
