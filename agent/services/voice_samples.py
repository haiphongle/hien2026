"""Bundled preview voices. No model download or inference is needed to listen."""

from functools import lru_cache
from pathlib import Path
import math
import subprocess
import wave

SAMPLES_DIR = Path(__file__).resolve().parents[1] / "assets" / "voice_samples"
VOICE_SAMPLES = (
    ("vieneu_binh_north_male", "Bình", "Nam", "Miền Bắc"),
    ("vieneu_ly_north_female", "Ly", "Nữ", "Miền Bắc"),
    ("vieneu_ngoc_north_female", "Ngọc", "Nữ", "Miền Bắc"),
    ("vieneu_tuyen_north_male", "Tuyên", "Nam", "Miền Bắc"),
    ("vieneu_vinh_south_male", "Vĩnh", "Nam", "Miền Nam"),
    ("vieneu_doan_south_female", "Đoan", "Nữ", "Miền Nam"),
)


def wav_duration(path: str | Path) -> float | None:
    """Fast PCM headers, with ffprobe for IEEE-float WAV produced by OmniVoice."""
    try:
        with wave.open(str(path), "rb") as audio:
            duration = audio.getnframes() / audio.getframerate()
            return duration if duration > 0 else None
    except (OSError, EOFError, wave.Error, ZeroDivisionError):
        pass
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(result.stdout.strip())
        return duration if result.returncode == 0 and math.isfinite(duration) and duration > 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


@lru_cache(maxsize=24)
def _metadata(audio: Path, transcript: Path, audio_mtime: int, text_mtime: int) -> tuple[str, float]:
    text = transcript.read_text(encoding="utf-8").strip()
    duration = wav_duration(audio)
    if not text or not duration:
        raise ValueError("Voice sample audio or transcript is invalid")
    return text, duration


def get_voice_sample(sample_id: str) -> dict:
    entry = next((item for item in VOICE_SAMPLES if item[0] == sample_id), None)
    if entry is None:
        raise FileNotFoundError("Voice sample not found")
    audio = (SAMPLES_DIR / f"{sample_id}.wav").resolve()
    transcript = (SAMPLES_DIR / f"{sample_id}.txt").resolve()
    if not all(path.is_relative_to(SAMPLES_DIR.resolve()) for path in (audio, transcript)):
        raise ValueError("Voice sample must be inside the bundled library")
    text, duration = _metadata(audio, transcript, audio.stat().st_mtime_ns, transcript.stat().st_mtime_ns)
    return {
        "id": entry[0], "name": entry[1], "gender": entry[2], "region": entry[3],
        "source": "VieNeu-TTS", "audio_path": audio, "text": text, "duration": duration,
    }
