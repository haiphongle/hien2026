"""Private stdio worker; executed by VieNeu's Python, never imported by the API."""
import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


def install_local_artifact_loader(cache_dir: Path):
    """Materialize HF snapshot symlinks as sibling files for ONNX external data.

    Recent ONNX Runtime checks real paths. HF blobs live in different folders,
    so loading a snapshot symlink fails external-data path validation. Hard links
    give each graph and its data a real common directory without another copy.
    This adapter is confined to the worker; no installed SDK files are changed.
    """
    import hashlib
    from huggingface_hub import hf_hub_download
    from vieneu._v3_turbo_engine import onnx_runtime_lite

    class LocalArtifactsEngine(onnx_runtime_lite.OnnxV3LiteEngine):
        @staticmethod
        def _fetch(repo, files, subfolder):
            revision = None
            directory = None
            for filename in files:
                source = Path(hf_hub_download(repo, filename, subfolder=subfolder or None, revision=revision))
                # Keep every graph/data pair on the same immutable HF snapshot.
                if revision is None:
                    revision = source.parts[source.parts.index("snapshots") + 1]
                    directory = cache_dir / "models" / hashlib.sha256(repo.encode()).hexdigest()[:12] / revision / (subfolder or "root")
                    directory.mkdir(parents=True, exist_ok=True)
                target = directory / filename
                if not target.is_file():
                    try:
                        os.link(source.resolve(), target)
                    except OSError:
                        staged = target.with_suffix(target.suffix + ".tmp")
                        shutil.copyfile(source, staged)
                        staged.replace(target)
            return directory

    onnx_runtime_lite.OnnxV3LiteEngine = LocalArtifactsEngine


def main():
    protocol = sys.stdout
    sys.stdout = sys.stderr  # SDK progress/logs must not corrupt the JSON protocol.
    model = None

    def emit(**event):
        protocol.write(json.dumps(event, ensure_ascii=False) + "\n")
        protocol.flush()

    for line in sys.stdin:
        try:
            args = json.loads(line)
            if model is None:
                emit(event="progress", stage="loading_model")
                from importlib.metadata import version
                if version("vieneu") != "3.8.3":
                    raise RuntimeError("FrameWeaver yêu cầu VieNeu 3.8.3 trong môi trường riêng.")
                import numpy as np
                import soundfile as sf
                from vieneu import Vieneu
                from vieneu_utils.core_utils import strip_encoder_pad_frame
                install_local_artifact_loader(Path(args["ready_file"]).parent)
                model = Vieneu(mode="v3turbo", device="cpu", backend="onnx", precision="fp32", threads=4)
                ready = Path(args["ready_file"])
                ready.write_text(json.dumps({"model": args["model"], "sdk": "3.8.3", "ready_at": time.time()}))
            emit(event="progress", stage="model_ready")
            if args.get("enroll"):
                emit(event="progress", stage="enrolling")
                embedding, codes = model.encode_reference(args["enroll"], denoise=False)
                voice = {"speaker_emb": np.asarray(embedding, dtype=np.float32).reshape(-1).tolist(),
                         "codes": np.asarray(codes, dtype=np.int64).tolist()}
                destination = Path(args["voice_output"])
                staged_voice = destination.with_suffix(".tmp")
                staged_voice.write_text(json.dumps(voice))
                staged_voice.replace(destination)
                args["voice"] = voice
            results = []
            for index, item in enumerate(args["items"]):
                started = time.monotonic()
                emit(event="progress", stage="generating", id=item["id"])
                target = Path(item["output"])
                target.parent.mkdir(parents=True, exist_ok=True)
                staged = target.with_name(f".{target.stem}.{os.getpid()}.wav")
                adjusted = staged.with_suffix(".speed.wav")
                metadata = staged.with_suffix(".json")
                try:
                    voice = item.get("voice", args.get("voice"))
                    voice = {**voice, "speaker_emb": np.asarray(voice["speaker_emb"], dtype=np.float32),
                             "codes": strip_encoder_pad_frame(np.asarray(voice["codes"], dtype=np.int64))}
                    audio = model.infer(item["text"], voice=voice)
                    if not len(audio) or not np.isfinite(audio).all():
                        raise ValueError("VieNeu trả về âm thanh trống hoặc không hợp lệ.")
                    sf.write(str(staged), audio, model.sample_rate, subtype="PCM_16")
                    speed = item.get("speed", args.get("speed", 1.0))
                    if speed != 1.0:
                        ffmpeg = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
                        subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(staged), "-af", f"atempo={speed}", str(adjusted)], check=True, capture_output=True, timeout=120)
                        adjusted.replace(staged)
                    duration = sf.info(str(staged)).duration
                    if duration <= 0:
                        raise ValueError("Âm thanh không có thời lượng.")
                    metadata.write_text(json.dumps({"signature": item.get("signature"), "text_hash": item.get("text_hash"), "voice": item.get("identity", args.get("identity")), "generation_seconds": round(time.monotonic() - started, 2)}, ensure_ascii=False))
                    staged.replace(target)
                    metadata.replace(str(target) + ".json")
                    result = {"id": item["id"], "ok": True, "path": str(target), "duration": duration, "elapsed": round(time.monotonic() - started, 2)}
                except Exception as exc:
                    result = {"id": item["id"], "ok": False, "error": str(exc)}
                finally:
                    for path in (staged, adjusted, metadata):
                        path.unlink(missing_ok=True)
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
