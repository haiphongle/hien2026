"""English preset catalog; model dependencies stay in the isolated worker."""
import atexit
import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from agent.config import KOKORO_CACHE_DIR, KOKORO_MODEL, KOKORO_PREVIEWS_DIR, KOKORO_PYTHON
from agent.services.tts_runtime import ManagedTTSWorker
from agent.services.voice_samples import wav_duration

SDK_VERSION = "0.9.4"
SAMPLE_TEXT = "Welcome! Every story begins with a small moment. Take a breath, look around, and enjoy the journey with us."
PRESETS = (("af_heart", "Heart"), ("af_bella", "Bella"), ("am_michael", "Michael"),
           ("am_puck", "Puck"), ("bf_emma", "Emma"), ("bm_george", "George"))


def catalog():
    return [{"id": "kokoro_" + key, "name": name, "voice": key, "languages": ["en"],
             "gender": "female" if key[1] == "f" else "male", "region": "Mỹ" if key[0] == "a" else "Anh",
             "description": f"Tiếng Anh · {'Mỹ' if key[0] == 'a' else 'Anh'} · {'nữ' if key[1] == 'f' else 'nam'}"}
            for key, name in PRESETS]


def get_preset(preset_id):
    for preset in catalog():
        if preset["id"] == preset_id:
            return preset
    raise ValueError("Giọng Kokoro không tồn tại. Chọn lại trong thư viện tiếng Anh.")


def voice_identity(preset_id):
    preset = get_preset(preset_id)
    return {"engine": "kokoro", "model": KOKORO_MODEL, "sdk": SDK_VERSION,
            "sample_rate": 24000, "preset_id": preset_id, "voice_name": preset["name"]}


def preview_path(preset_id):
    key = hashlib.sha256(json.dumps({**voice_identity(preset_id), "text": SAMPLE_TEXT}, sort_keys=True).encode()).hexdigest()[:16]
    return KOKORO_PREVIEWS_DIR / f"{preset_id}_{key}.wav"


def previews():
    result = []
    for preset in catalog():
        path = preview_path(preset["id"])
        duration = wav_duration(path) if path.is_file() else None
        result.append({**{k: v for k, v in preset.items() if k != "voice"}, "sample_text": SAMPLE_TEXT,
                       "duration": duration, "audio_url": f"/api/tts/kokoro/voices/{preset['id']}/audio?v={path.stem}" if duration else None})
    return result


class KokoroWorker(ManagedTTSWorker):
    def __init__(self):
        super().__init__("kokoro", KOKORO_PYTHON, KOKORO_CACHE_DIR, KOKORO_MODEL,
                         Path(__file__).with_name("kokoro_worker.py"), {"PYTORCH_ENABLE_MPS_FALLBACK": "1"})

    def status(self):
        if self._probe is None or time.monotonic() - self._probe_time > 30:
            executable = shutil.which(self.python)
            available = False
            device = "CPU"
            if executable:
                try:
                    probe = subprocess.run([executable, "-I", "-c", "import importlib.metadata as m; import torch, soundfile, misaki; print(m.version('kokoro')); print('MPS' if torch.backends.mps.is_available() else 'CPU')"], capture_output=True, text=True, timeout=15)
                    lines = probe.stdout.strip().splitlines()
                    available = probe.returncode == 0 and len(lines) == 2 and lines[0] == SDK_VERSION
                    if available:
                        device = lines[1]
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self._probe = {"available": available, "device": device,
                           "reason": None if available else "Chưa cài môi trường Kokoro tiếng Anh."}
            self._probe_time = time.monotonic()
        return {**self._probe, "engine": "kokoro", "model": KOKORO_MODEL, "sdk_version": SDK_VERSION,
                "sample_rate": 24000, "model_downloaded": (self.cache_dir / "ready.json").is_file(),
                "state": self.state, "error": self.error}


worker = KokoroWorker()
atexit.register(worker.close)
