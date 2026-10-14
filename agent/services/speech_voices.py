"""Custom voices live outside the SDK, alongside reusable character assignments."""
import asyncio
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from agent.services import speech_store as store, speech_jobs, vieneu, kokoro, qwen3
from agent.services.tts_runtime import TTS_SEMAPHORE
from agent.services.voice_samples import wav_duration


def voice_dir(voice_id: str) -> Path:
    if not voice_id.startswith("custom_"):
        raise ValueError("ID giọng riêng không hợp lệ.")
    return store.ROOT / "voices" / store.identifier(voice_id[7:])


def resolve_voice(voice_id: str) -> tuple[dict, dict]:
    if voice_id.startswith("qwen3_"):
        return qwen3.get_preset(voice_id)["voice"], qwen3.voice_identity(voice_id)
    if voice_id.startswith("kokoro_"):
        return kokoro.get_preset(voice_id)["voice"], kokoro.voice_identity(voice_id)
    if voice_id.startswith("v3_"):
        return vieneu.get_preset(voice_id)["voice"], vieneu.voice_identity(voice_id)
    directory = voice_dir(voice_id)
    profile = store.read_json(directory / "profile.json")
    if not profile or profile.get("status") != "READY":
        raise ValueError("Giọng riêng chưa sẵn sàng. Tạo demo giọng trước.")
    voice = store.read_json(directory / "voice.json")
    if not voice or not voice.get("speaker_emb") or not voice.get("codes"):
        raise ValueError("Dữ liệu giọng riêng bị thiếu. Hãy chuẩn bị lại giọng.")
    digest = hashlib.sha256(json.dumps(voice, sort_keys=True).encode()).hexdigest()
    return voice, {"engine": "vieneu", "model": vieneu.VIENEU_MODEL, "sdk": vieneu.SDK_VERSION,
                   "voice_id": voice_id, "voice_name": profile["name"], "preset_hash": digest}


ENGINES = {"vieneu": vieneu, "kokoro": kokoro, "qwen3": qwen3}


def engine_service(engine):
    if engine not in ENGINES:
        raise ValueError("Bộ máy giọng đọc không hợp lệ.")
    return ENGINES[engine]


def voice_languages(voice_id):
    _, identity = resolve_voice(voice_id)
    if identity["engine"] == "qwen3":
        return [identity["language"]]
    return ["en"] if identity["engine"] == "kokoro" else ["vi"]


def generate(args, callback=None):
    service = engine_service(args["identity"]["engine"])
    # One local model at a time; callers own the shared TTS semaphore.
    for other in ENGINES.values():
        if other is not service:
            other.worker.close()
    return service.worker.generate(args, callback)


def custom_voices():
    voices = []
    for path in sorted((store.ROOT / "voices").glob("*/profile.json")):
        profile = store.read_json(path)
        vid = profile["id"]
        job = speech_jobs.get_job(vid, path.parent / "job.json")
        voices.append({**profile, "job": job,
                       "reference_url": f"/api/tts/custom-voices/{vid}/reference",
                       "audio_url": f"/api/tts/custom-voices/{vid}/audio" if profile.get("status") == "READY" else None})
    return voices


def normalize_reference(source: Path, target: Path):
    ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    result = subprocess.run([ffmpeg, "-v", "error", "-y", "-protocol_whitelist", "file,pipe", "-i", str(source), "-vn", "-t", "9",
                             "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(target)],
                            capture_output=True, timeout=45)
    duration = wav_duration(target) if not result.returncode else None
    if duration is None or not 3 <= duration <= 8.1:
        raise ValueError("Chọn đoạn giọng rõ từ 3 đến 8 giây (WAV, MP3, M4A hoặc FLAC).")
    # Silence is not a useful speaker reference; avoid launching the model for it.
    import array
    import wave
    with wave.open(str(target), "rb") as wav:
        samples = array.array("h", wav.readframes(wav.getnframes()))
        if not samples or sum(float(s) ** 2 for s in samples) / len(samples) < 30 ** 2:
            raise ValueError("Bản ghi quá nhỏ hoặc không có âm thanh. Hãy thu lại giọng rõ hơn.")
    return duration


def start_clone(voice_id: str):
    directory = voice_dir(voice_id)
    profile = store.read_json(directory / "profile.json")
    if not profile:
        raise FileNotFoundError("Không tìm thấy giọng riêng.")

    async def run(job, update):
        async with TTS_SEMAPHORE:
            update(status="PROCESSING", stage="loading_model")
            loop = asyncio.get_running_loop()
            def progress(event):
                loop.call_soon_threadsafe(update_stage, event.get("stage", "generating"))
            def update_stage(stage):
                update(stage=stage)
            result = await asyncio.to_thread(generate, {
                "enroll": str(directory / "reference.wav"), "voice_output": str(directory / "voice.json"),
                "allow_download": not (vieneu.VIENEU_CACHE_DIR / "clone_ready.json").is_file(),
                "identity": {"engine": "vieneu", "voice_id": voice_id, "voice_name": profile["name"]},
                "items": [{"id": voice_id, "text": vieneu.SAMPLE_TEXT, "output": str(directory / "demo.wav")}],
            }, progress)
            if result[0]["ok"]:
                profile["status"] = "READY"
                store.atomic_json(directory / "profile.json", profile)
                store.atomic_json(vieneu.VIENEU_CACHE_DIR / "clone_ready.json", {"sdk": vieneu.SDK_VERSION})
            update(results={voice_id: result[0]}, completed=1)
    return speech_jobs.start_job(voice_id, directory / "job.json", 1, run)
