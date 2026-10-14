"""Isolated Qwen3-TTS stdio worker. Heavy imports never enter FastAPI."""
import contextlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path


def synthesize(model, item, language, speaker, emit):
    import numpy as np

    limit = min(item["max_audio_seconds"], (2048 - 2) / 12.5 - 1)
    # The 12 Hz codec uses 1920 samples per frame at 24 kHz (12.5 frames/s).
    # Leave a margin above validation so a token-limited, incomplete result fails.
    tokens = min(2048, math.ceil((limit + 1) * 12.5) + 2)
    for attempt in range(2):
        sampling = {"temperature": .7, "subtalker_temperature": .7} if attempt else {}
        wavs, rate = model.generate_custom_voice(
            text=item["text"], language=language, speaker=speaker,
            max_new_tokens=tokens, **sampling,
        )
        audio = np.asarray(wavs[0]) if wavs else np.array([])
        if not rate or not len(audio) or not np.isfinite(audio).all():
            raise ValueError("Qwen3 trả về âm thanh trống hoặc không hợp lệ.")
        duration = len(audio) / rate
        audible = float(np.sqrt(np.mean(audio.astype("float64") ** 2))) >= 1e-4
        if duration <= limit and audible:
            return audio, rate, attempt + 1
        if not attempt:
            emit(event="progress", stage="retrying", id=item["id"])
    if not audible:
        raise ValueError("Qwen3 đã thử 2 lần nhưng âm thanh không có tiếng rõ. Thử lại hoặc chọn giọng khác.")
    raise ValueError(f"Qwen3 đã thử 2 lần nhưng tiếng vẫn quá dài ({duration:.1f}s, giới hạn {limit:.1f}s). "
                     "Chọn giọng khác cho ngôn ngữ này; với lời đọc dài, hãy tách câu.")


def main():
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    model = None

    def emit(**event):
        protocol.write(json.dumps(event, ensure_ascii=False) + "\n")
        protocol.flush()

    for line in sys.stdin:
        try:
            args = json.loads(line)
            if model is None:
                emit(event="progress", stage="loading_model")
                import soundfile as sf
                import torch
                from huggingface_hub import snapshot_download
                from qwen_tts import Qwen3TTSModel

                torch.set_num_threads(4)
                device = "mps" if torch.backends.mps.is_available() else "cpu"
                # The SDK's processor probes the Hub even with local_files_only.
                # A snapshot path keeps the complete model and tokenizer offline.
                model_path = args["model"] if Path(args["model"]).is_dir() else snapshot_download(
                    args["model"], local_files_only=os.environ.get("HF_HUB_OFFLINE") == "1",
                    allow_patterns=["*.json", "*.safetensors", "*.txt", "speech_tokenizer/*"],
                )
                model = Qwen3TTSModel.from_pretrained(
                    model_path, device_map=device,
                    # BF16 preserves the model's range without FP32's memory cost.
                    # FP16 sampling produces NaNs on Apple MPS for this model.
                    dtype=torch.bfloat16 if device == "mps" else torch.float32,
                    attn_implementation="eager",
                    local_files_only=os.environ.get("HF_HUB_OFFLINE") == "1",
                )
                Path(args["ready_file"]).write_text(json.dumps({"model": args["model"], "sdk": "0.1.1",
                                                             "device": device.upper(), "ready_at": time.time()}))
            emit(event="progress", stage="model_ready")
            results = []
            identity = args["identity"]
            language = identity["language_name"]
            for index, item in enumerate(args["items"]):
                started = time.monotonic()
                emit(event="progress", stage="generating", id=item["id"])
                target = Path(item["output"])
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = target.with_name(f".{target.stem}.{os.getpid()}.wav")
                fitted = target.with_name(f".{target.stem}.{os.getpid()}.fit.wav")
                metadata = Path(str(staged) + ".json")
                try:
                    audio, rate, attempts = synthesize(model, item, language, args["voice"], emit)
                    sf.write(str(staged), audio, rate, subtype="PCM_16")
                    speed = float(item.get("speed", args.get("speed", 1)))
                    if speed != 1:
                        completed = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(staged),
                                                    "-af", f"atempo={speed}", str(fitted)], capture_output=True, text=True, timeout=90)
                        if completed.returncode:
                            raise RuntimeError(completed.stderr[-500:])
                        fitted.replace(staged)
                    duration = sf.info(str(staged)).duration
                    metadata.write_text(json.dumps({"signature": item.get("signature"), "text_hash": item.get("text_hash"),
                                                    "voice": identity, "generation_attempts": attempts,
                                                    "generation_seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False))
                    staged.replace(target)
                    metadata.replace(str(target) + ".json")
                    result = {"id": item["id"], "ok": True, "path": str(target), "duration": duration,
                              "elapsed": round(time.monotonic() - started, 2), "attempts": attempts}
                except Exception as exc:
                    result = {"id": item["id"], "ok": False, "error": str(exc)}
                finally:
                    staged.unlink(missing_ok=True)
                    fitted.unlink(missing_ok=True)
                    metadata.unlink(missing_ok=True)
                    if device == "mps":
                        torch.mps.empty_cache()
                results.append(result)
                emit(event="progress", stage="generating", completed=args.get("progress_offset", 0) + index + 1,
                     total=args.get("progress_total", len(args["items"])), **result)
            emit(event="result", results=results)
        except Exception as exc:
            import traceback
            traceback.print_exc(file=sys.stderr)
            emit(event="result", error=str(exc))


if __name__ == "__main__":
    with contextlib.suppress(BrokenPipeError):
        main()
