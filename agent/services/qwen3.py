"""Qwen3 multilingual preset voices; inference stays outside the API process."""
import atexit
import asyncio
import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from agent.config import QWEN3_CACHE_DIR, QWEN3_MODEL, QWEN3_PREVIEWS_DIR, QWEN3_PYTHON
from agent.services.tts_runtime import ManagedTTSWorker
from agent.services.voice_samples import wav_duration

SDK_VERSION = "0.1.1"
LANGUAGES = {
    "zh": ("Chinese", "中文", "你好，欢迎来到这个温暖的故事。"),
    "en": ("English", "Tiếng Anh", "Hello, welcome to this little story."),
    "ja": ("Japanese", "Tiếng Nhật", "こんにちは。この小さな物語へようこそ。"),
    "ko": ("Korean", "Tiếng Hàn", "안녕하세요. 이 작은 이야기에 오신 것을 환영합니다."),
    "de": ("German", "Tiếng Đức", "Hallo, willkommen zu dieser kleinen Geschichte."),
    "fr": ("French", "Tiếng Pháp", "Bonjour, bienvenue dans cette petite histoire."),
    "ru": ("Russian", "Tiếng Nga", "Здравствуйте, добро пожаловать в эту маленькую историю."),
    "pt": ("Portuguese", "Tiếng Bồ Đào Nha", "Olá, bem-vindo a esta pequena história."),
    "es": ("Spanish", "Tiếng Tây Ban Nha", "Hola, bienvenido a esta pequeña historia."),
    "it": ("Italian", "Tiếng Ý", "Ciao, benvenuto in questa piccola storia."),
}
SPEAKERS = (
    ("Vivian", "female", "Trung Quốc", "Nữ trẻ, sáng"),
    ("Serena", "female", "Trung Quốc", "Nữ trẻ, ấm và dịu"),
    ("Uncle_Fu", "male", "Trung Quốc", "Nam trầm, điềm đạm"),
    ("Dylan", "male", "Bắc Kinh", "Nam trẻ, rõ chữ"),
    ("Eric", "male", "Tứ Xuyên", "Nam sôi nổi, hơi khàn"),
    ("Ryan", "male", "Tiếng Anh", "Nam năng động, nhịp mạnh"),
    ("Aiden", "male", "Mỹ", "Nam sáng, tự nhiên"),
    ("Ono_Anna", "female", "Nhật Bản", "Nữ vui tươi, linh hoạt"),
    ("Sohee", "female", "Hàn Quốc", "Nữ ấm, giàu cảm xúc"),
)


def catalog(language=None):
    if language is not None and language not in LANGUAGES:
        raise ValueError("Ngôn ngữ Qwen3 không được hỗ trợ.")
    return [
        {"id": f"qwen3_{code}_{speaker.lower()}", "name": speaker.replace("_", " "),
         "voice": speaker, "languages": [code], "gender": gender,
         "region": origin, "description": f"{label} · {description}"}
        for code, (_, label, _) in LANGUAGES.items() if language is None or code == language
        for speaker, gender, origin, description in SPEAKERS
    ]


def get_preset(preset_id):
    for preset in catalog():
        if preset["id"] == preset_id:
            return preset
    raise ValueError("Giọng Qwen3 không tồn tại. Chọn lại trong thư viện.")


def sample_text(preset_id):
    return LANGUAGES[get_preset(preset_id)["languages"][0]][2]


def voice_identity(preset_id):
    preset = get_preset(preset_id)
    return {"engine": "qwen3", "model": QWEN3_MODEL, "sdk": SDK_VERSION,
            "sample_rate": 24000, "preset_id": preset_id, "voice_name": preset["name"],
            "language": preset["languages"][0],
            "language_name": LANGUAGES[preset["languages"][0]][0]}


def preview_path(preset_id):
    key = hashlib.sha256(json.dumps({**voice_identity(preset_id), "text": sample_text(preset_id)}, sort_keys=True).encode()).hexdigest()[:16]
    return QWEN3_PREVIEWS_DIR / f"{preset_id}_{key}.wav"


def max_audio_seconds(text):
    # Generous bound catches runaway short prompts without cutting their audio.
    return max(15, len(text) * .35)


def short_audio(text, duration, language, speed=1):
    # A generous speech-rate bound is only a trigger for ASR, not proof of bad speech.
    characters = sum(char.isalnum() for char in text)
    rate = 12 if language in {"zh", "ja"} else 20 if language == "ko" else 30
    return duration * speed < characters / rate


def audio_metadata(path):
    from agent.services import speech_store as store
    try:
        data = store.read_json(path.with_suffix(".wav.json"), {})
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def cached_quality(path, text, language, speed=1):
    """A quality verdict belongs to this exact audio file and synthesis input."""
    info = path.stat()
    quality = audio_metadata(path).get("quality")
    quality = quality if isinstance(quality, dict) else {}
    if (quality.get("version") == 1 and quality.get("stamp") == [info.st_size, info.st_mtime_ns]
            and quality.get("text_hash") == hashlib.sha256(text.encode()).hexdigest()
            and quality.get("language") == language and quality.get("speed") == speed):
        return quality
    return None


def audio_warning(path, text, language, speed=1, duration=None):
    """Read cached quality only; listing/playing voices never launches inference."""
    duration = duration or wav_duration(path)
    if not duration:
        return "Không đọc được mẫu âm thanh. Hãy tạo lại."
    if duration * speed > max_audio_seconds(text):
        return "Mẫu dài bất thường. Thử chuẩn bị lại hoặc chọn giọng khác."
    quality = cached_quality(path, text, language, speed)
    if quality is not None:
        return quality.get("error")
    if short_audio(text, duration, language, speed):
        return "Mẫu quá ngắn so với câu, có thể đọc thiếu. Tạo lại để đối chiếu lời đọc."
    return None


def check_audio(path, text, language, speed, callback=None):
    """Reuse signal/word analysis for suspicious output, then persist its verdict."""
    from agent.services import speech_store as store, speech_analysis, transcription
    from agent.services.speech_analysis_core import inspect_signal, union_seconds
    info = path.stat()
    quality = {"version": 1, "stamp": [info.st_size, info.st_mtime_ns],
               "text_hash": hashlib.sha256(text.encode()).hexdigest(), "language": language,
               "speed": speed, "error": None}
    try:
        signal = inspect_signal(path)
        duration = signal["duration"]
        silence = union_seconds([(w["start"], w["end"]) for w in signal["warnings"] if w["kind"] == "silence"])
        if any(w["kind"] in {"empty", "invalid"} for w in signal["warnings"]) or silence >= duration * .95:
            raise ValueError("Âm thanh không có tiếng rõ. Thử tạo lại hoặc chọn giọng khác.")
        if short_audio(text, duration - silence, language, speed):
            model = next((name for name in ("small", "base", "large-v3-turbo-q5_0")
                          if (transcription.MODEL_DIR / f"ggml-{name}.bin").is_file()), None)
            if not model or not transcription.binary():
                raise ValueError("Tiếng quá ngắn, có thể đọc thiếu. Cần Whisper đã cài để đối chiếu; hoặc chọn giọng khác.")
            if callback:
                callback({"stage": "checking_audio"})
            with tempfile.TemporaryDirectory(prefix="qwen-check-") as temp:
                work = Path(temp)
                wav = work / "audio.wav"
                subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path),
                                "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
                               check=True, capture_output=True, timeout=90)
                item = {"id": "quality", "path": str(wav), "text": text, "duration": duration,
                        "start": 0, "end": duration}
                # CPU avoids competing with the resident TTS model on Apple MPS.
                checked = asyncio.run(speech_analysis.recognize([item], model, language, work,
                    lambda **changes: None, cpu=True, minimum_timeout=180))[0]
                quality.update(recognized=checked["recognized"], difference=checked["difference"])
                if checked["needs_review"]:
                    raise ValueError("Lời đọc có thể thiếu hoặc sai câu theo kết quả nhận dạng. Thử tạo lại hoặc chọn giọng khác.")
    except (ValueError, RuntimeError, OSError, TimeoutError, subprocess.SubprocessError) as exc:
        quality["error"] = str(exc)
    metadata_path = path.with_suffix(".wav.json")
    store.atomic_json(metadata_path, {**audio_metadata(path), "quality": quality})
    return quality["error"]


def previews(language=None):
    result = []
    for preset in catalog(language):
        path = preview_path(preset["id"])
        duration = wav_duration(path) if path.is_file() else None
        warning = audio_warning(path, sample_text(preset["id"]), preset["languages"][0], duration=duration) if duration else None
        result.append({**{k: v for k, v in preset.items() if k != "voice"},
                       "sample_text": sample_text(preset["id"]), "duration": duration,
                       "audio_url": f"/api/tts/qwen3/voices/{preset['id']}/audio?v={path.stem}-{path.stat().st_mtime_ns}" if duration and not warning else None,
                       "warning": warning})
    return result


class Qwen3Worker(ManagedTTSWorker):
    def __init__(self):
        super().__init__("qwen3", QWEN3_PYTHON, QWEN3_CACHE_DIR, QWEN3_MODEL,
                         Path(__file__).with_name("qwen3_worker.py"), {"PYTORCH_ENABLE_MPS_FALLBACK": "1"})
        self.idle_timeout = 120

    def _start(self, allow_download=False):
        probe = self._probe or self.status()
        super()._start(allow_download=allow_download or not probe["model_downloaded"])

    def _item_timeout(self, item):
        # Bound runaway decoding without returning a clipped, incomplete sentence.
        # Loading/download has its own longer timeout in the shared runner.
        return min(1800, max(300, len(item["text"]) * 8))

    def generate(self, args, callback=None):
        payload = {**args, "items": [{**item, "max_audio_seconds": max_audio_seconds(item["text"])}
                                     for item in args["items"]]}
        results = super().generate(payload, callback)
        items = {item["id"]: item for item in args["items"]}
        for result in results:
            if not result.get("ok"):
                continue
            item = items[result["id"]]
            error = check_audio(Path(item["output"]), item["text"], args["identity"]["language"],
                                float(item.get("speed", args.get("speed", 1))), callback)
            if error:
                result.update(ok=False, error=error)
        return results

    def status(self):
        if self._probe is None or time.monotonic() - self._probe_time > 30:
            executable = shutil.which(self.python)
            available, device, downloaded = False, "Tự nhận diện", False
            if executable and self.cache_dir.exists():
                try:
                    probe = subprocess.run([executable, "-I", "-c",
                                            "import importlib.metadata as m, importlib.util as u, json, sys\n"
                                            "assert all(u.find_spec(name) for name in ('torch', 'qwen_tts', 'soundfile', 'librosa', 'accelerate', 'torchaudio', 'onnxruntime', 'einops'))\n"
                                            "try:\n ready = json.load(open(sys.argv[1]))\n"
                                            "except (OSError, ValueError):\n ready = {}\n"
                                            "print(m.version('qwen-tts')); print(json.dumps(ready))",
                                            str(self.cache_dir / "ready.json")],
                                           capture_output=True, text=True, timeout=10)
                    lines = probe.stdout.strip().splitlines()
                    available = probe.returncode == 0 and len(lines) == 2 and lines[0] == SDK_VERSION
                    if available:
                        ready = json.loads(lines[1])
                        if isinstance(ready, dict):
                            downloaded = ready.get("model") == self.model and ready.get("sdk") == SDK_VERSION
                            if downloaded:
                                device = ready.get("device", device)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
            self._probe = {"available": available, "device": device, "model_downloaded": downloaded,
                           "reason": None if available else "Chưa đọc được Qwen3-TTS. Kiểm tra ổ chứa model và quyền đọc ổ ngoài của Python trong macOS."}
            self._probe_time = time.monotonic()
        return {**self._probe, "engine": "qwen3", "model": QWEN3_MODEL, "sdk_version": SDK_VERSION,
                "sample_rate": 24000,
                "state": self.state, "error": self.error}


worker = Qwen3Worker()
atexit.register(worker.close)
