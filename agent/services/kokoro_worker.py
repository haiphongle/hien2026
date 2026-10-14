"""Private Kokoro stdio worker; never import torch in the API process."""
import contextlib
import json
import os
import sys
import time
from pathlib import Path


def main():
    # Model setup may launch pip; its inherited stdout must not enter stdio JSON.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    pipelines = {}

    def emit(**event):
        protocol.write(json.dumps(event, ensure_ascii=False) + "\n")
        protocol.flush()

    for line in sys.stdin:
        try:
            args = json.loads(line)
            if not pipelines:
                emit(event="progress", stage="loading_model")
                from importlib.metadata import version
                if version("kokoro") != "0.9.4":
                    raise RuntimeError("FrameWeaver yêu cầu Kokoro 0.9.4 trong môi trường riêng.")
                import numpy as np
                import soundfile as sf
                import torch
                from kokoro import KPipeline
                torch.set_num_threads(4)
                us = KPipeline(lang_code="a", repo_id=args["model"])
                gb = KPipeline(lang_code="b", repo_id=args["model"], model=us.model)
                # All advertised voices must work offline before marking ready.
                for key in ("af_heart", "af_bella", "am_michael", "am_puck", "bf_emma", "bm_george"):
                    (us if key[0] == "a" else gb).load_voice(key)
                pipelines = {"a": us, "b": gb}
                Path(args["ready_file"]).write_text(json.dumps({"model": args["model"], "sdk": "0.9.4", "ready_at": time.time()}))
            emit(event="progress", stage="model_ready")
            results = []
            for index, item in enumerate(args["items"]):
                started = time.monotonic()
                emit(event="progress", stage="generating", id=item["id"])
                target = Path(item["output"])
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = target.with_name(f".{target.stem}.{os.getpid()}.wav")
                metadata = staged.with_suffix(".json")
                try:
                    voice = item.get("voice", args.get("voice"))
                    pipeline = pipelines[voice[0]]
                    chunks = [part.audio.detach().cpu().numpy() for part in pipeline(item["text"], voice=voice, speed=item.get("speed", args.get("speed", 1.0))) if part.audio is not None]
                    audio = np.concatenate(chunks) if chunks else np.array([])
                    if not len(audio) or not np.isfinite(audio).all():
                        raise ValueError("Kokoro trả về âm thanh trống hoặc không hợp lệ.")
                    sf.write(str(staged), audio, 24000, subtype="PCM_16")
                    duration = sf.info(str(staged)).duration
                    metadata.write_text(json.dumps({"signature": item.get("signature"), "text_hash": item.get("text_hash"), "voice": item.get("identity", args.get("identity")), "generation_seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False))
                    staged.replace(target)
                    metadata.replace(str(target) + ".json")
                    result = {"id": item["id"], "ok": True, "path": str(target), "duration": duration, "elapsed": round(time.monotonic() - started, 2)}
                except Exception as exc:
                    result = {"id": item["id"], "ok": False, "error": str(exc)}
                finally:
                    staged.unlink(missing_ok=True)
                    metadata.unlink(missing_ok=True)
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
