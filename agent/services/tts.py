"""OmniVoice TTS service — subprocess-based for compatibility."""
import asyncio
import hashlib
import json
import logging
import os
import re
import selectors
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from agent.config import TTS_DEVICE, TTS_MODEL, TTS_SAMPLE_RATE
from agent.services.voice_samples import wav_duration

logger = logging.getLogger(__name__)

# Use the same Python as the running agent unless explicitly overridden.
PYTHON_BIN = os.environ.get("TTS_PYTHON_BIN", sys.executable)


def narration_path_for_scene(output_dir: str | Path, scene: dict) -> Path | None:
    """Find a scene's generated WAV, preferring the filename for its current order."""
    directory = Path(output_dir).resolve()
    scene_id = str(scene.get("id") or "")
    if not scene_id or not re.fullmatch(r"[0-9a-f-]{36}", scene_id, re.I):
        return None
    current = directory / f"scene_{int(scene.get('display_order', 0)):03d}_{scene_id}.wav"
    if current.is_file():
        return current
    candidates = [path for path in directory.glob(f"scene_*_{scene_id}.wav") if path.is_file()]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def narration_matches_text(audio_path: str | Path, narrator_text: str | None) -> bool:
    """Return false for narration clips that no longer match the saved scene text."""
    if not narrator_text:
        return False
    try:
        metadata = json.loads(Path(str(audio_path) + ".json").read_text())
    except (OSError, json.JSONDecodeError):
        return False
    text_hash = hashlib.sha256(narrator_text.encode("utf-8")).hexdigest()
    return isinstance(metadata, dict) and metadata.get("text_hash") == text_hash


def _voice_signature(instruct, ref_audio, ref_text, speed, engine="omnivoice", preset_id=None) -> str:
    """Include the model and reference contents: a saved template can be replaced."""
    if engine in {"vieneu", "kokoro", "qwen3"}:
        from agent.services.speech_voices import engine_service
        return json.dumps({**engine_service(engine).voice_identity(preset_id), "speed": speed}, ensure_ascii=False, sort_keys=True)
    if engine != "omnivoice":
        raise ValueError("Unknown TTS engine")
    reference_hash = None
    if ref_audio:
        with Path(ref_audio).open("rb") as source:
            reference_hash = hashlib.file_digest(source, "sha256").hexdigest()
    return json.dumps({
        "model": TTS_MODEL, "sample_rate": TTS_SAMPLE_RATE,
        "instruct": None if ref_audio and ref_text else instruct,
        "ref_audio_hash": reference_hash, "ref_text": ref_text, "speed": speed,
    }, ensure_ascii=False, sort_keys=True)

# Inline script template for TTS generation via subprocess
_TTS_SCRIPT = """
import sys, json, torch, torchaudio

args = json.loads(sys.stdin.read())
from omnivoice import OmniVoice
from omnivoice.models.omnivoice import OmniVoiceGenerationConfig, chunk_text_punctuation

def report(progress, stage, **extra):
    print(json.dumps({"event": "progress", "progress": progress, "stage": stage, **extra}), flush=True)

report(2, "loading_model")
model = OmniVoice.from_pretrained(args["model"], device_map=args["device"], dtype=torch.float32)
report(10, "model_ready")

# OmniVoice calls its top-level forward once per iterative decoding step. This
# hook reports actual model work, instead of estimating progress from elapsed time.
# Count the short-text pass and the exact chunk split OmniVoice will use, so the
# percentage denominator remains accurate for both short and long samples.
decode = {"steps": 0, "expected_steps": 32}
original_preprocess = model._preprocess_all
def tracked_preprocess(*preprocess_args, **preprocess_kwargs):
    task = original_preprocess(*preprocess_args, **preprocess_kwargs)
    config = OmniVoiceGenerationConfig(num_step=32)
    frame_rate = model.audio_tokenizer.config.frame_rate
    short_indices, long_indices = task.get_indices(config, frame_rate)
    segments = 1 if short_indices else 0
    if long_indices:
        chunk_counts = []
        for index in long_indices:
            text = task.texts[index]
            avg_tokens_per_char = task.target_lens[index] / max(1, len(text))
            chunk_len = int(config.audio_chunk_duration * frame_rate / avg_tokens_per_char)
            chunks = chunk_text_punctuation(text=text, chunk_len=chunk_len, min_chunk_len=3)
            chunk_counts.append(len(chunks))
        segments += max(chunk_counts)
    decode["expected_steps"] = max(32, segments * 32)
    return task
model._preprocess_all = tracked_preprocess

def on_decode_step(module, inputs, output):
    decode["steps"] += 1
    expected_steps = decode["expected_steps"]
    step = (decode["steps"] - 1) % 32 + 1
    segment = (decode["steps"] - 1) // 32 + 1
    progress = min(94, 10 + int(84 * decode["steps"] / expected_steps))
    report(progress, "synthesizing", step=step, steps=32, segment=segment, segments=expected_steps // 32)
model.register_forward_hook(on_decode_step)

kwargs = {"text": args["text"]}
if args.get("ref_audio") and args.get("ref_text"):
    kwargs["ref_audio"] = args["ref_audio"]
    kwargs["ref_text"] = args["ref_text"]
elif args.get("instruct"):
    kwargs["instruct"] = args["instruct"]
if args.get("speed") and args["speed"] != 1.0:
    kwargs["speed"] = args["speed"]

kwargs["num_step"] = 32
audio = model.generate(**kwargs)
report(96, "saving_audio")
waveform = torch.as_tensor(audio[0]).detach().cpu()
if waveform.ndim == 1:
    waveform = waveform.unsqueeze(0)
torchaudio.save(args["output"], waveform, args["sample_rate"])
print(json.dumps({"event": "result", "ok": True, "path": args["output"]}), flush=True)
"""

# Batch script — loads model once, generates for multiple texts
_TTS_BATCH_SCRIPT = """
import sys, json, torch, torchaudio, uuid
from pathlib import Path

args = json.loads(sys.stdin.read())
from omnivoice import OmniVoice

model = OmniVoice.from_pretrained(args["model"], device_map=args["device"], dtype=torch.float32)

results = []
for idx, item in enumerate(args["items"]):
    staged = Path(item["output"]).with_name("." + uuid.uuid4().hex + ".wav")
    staged_meta = staged.with_suffix(".json")
    try:
        kwargs = {"text": item["text"]}
        if args.get("ref_audio") and args.get("ref_text"):
            kwargs["ref_audio"] = args["ref_audio"]
            kwargs["ref_text"] = args["ref_text"]
        elif args.get("instruct"):
            kwargs["instruct"] = args["instruct"]
        if args.get("speed") and args["speed"] != 1.0:
            kwargs["speed"] = args["speed"]

        audio = model.generate(**kwargs)
        Path(item["output"]).parent.mkdir(parents=True, exist_ok=True)
        waveform = torch.as_tensor(audio[0]).detach().cpu()
        if waveform.ndim == 1:
            waveform = waveform.unsqueeze(0)
        torchaudio.save(str(staged), waveform, args["sample_rate"])
        info = torchaudio.info(str(staged))
        duration = info.num_frames / info.sample_rate
        if duration <= 0:
            raise ValueError("Generated audio is empty")
        staged_meta.write_text(json.dumps({
            "signature": item["signature"],
            "text_hash": item["text_hash"],
        }))
        staged.replace(item["output"])
        staged_meta.replace(item["output"] + ".json")
        results.append({"id": item["id"], "ok": True, "path": item["output"], "duration": duration})
    except Exception as e:
        results.append({"id": item["id"], "ok": False, "error": str(e)})
    finally:
        staged.unlink(missing_ok=True)
        staged_meta.unlink(missing_ok=True)
    print(json.dumps({"event": "progress", "completed": args.get("progress_offset", 0) + idx + 1, "total": args.get("progress_total", len(args["items"])), "id": item["id"], "ok": results[-1]["ok"], "path": results[-1].get("path"), "duration": results[-1].get("duration"), "error": results[-1].get("error")}), flush=True)

print(json.dumps(results))
"""


async def generate_speech(
    text: str,
    output_path: str,
    instruct: Optional[str] = None,
    ref_audio: Optional[str] = None,
    ref_text: Optional[str] = None,
    speed: float = 1.0,
    progress_callback: Optional[Callable[[dict], None]] = None,
) -> str:
    """Generate speech for text via subprocess. Returns path to WAV file."""
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    args = {
        "model": TTS_MODEL,
        "device": TTS_DEVICE,
        "text": text,
        "output": output_path,
        "sample_rate": TTS_SAMPLE_RATE,
        "speed": speed,
    }
    if instruct:
        args["instruct"] = instruct
    if ref_audio:
        args["ref_audio"] = ref_audio
    if ref_text:
        args["ref_text"] = ref_text

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _run_tts_subprocess, args, progress_callback)

    if not result.get("ok"):
        raise RuntimeError(f"TTS failed: {result.get('error', 'unknown')}")

    logger.info("TTS saved to %s", output_path)
    return output_path


def _run_tts_subprocess(args: dict, progress_callback: Optional[Callable[[dict], None]] = None) -> dict:
    """Run one TTS request, allowing CPU inference several minutes on Mac."""
    from agent.services import vieneu, kokoro, qwen3
    vieneu.worker.close()
    kokoro.worker.close()  # Release the other model's RAM before starting OmniVoice.
    qwen3.worker.close()
    timeout = int(os.environ.get("TTS_REQUEST_TIMEOUT_SECONDS", "600"))
    started = time.monotonic()
    output_lines: list[str] = []
    with tempfile.TemporaryFile(mode="w+t") as stderr_file:
        try:
            proc = subprocess.Popen(
                [PYTHON_BIN, "-u", "-c", _TTS_SCRIPT],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr_file, bufsize=0,
            )
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

        assert proc.stdout is not None
        assert proc.stdin is not None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        pending = b""

        def write_request():
            try:
                proc.stdin.write(json.dumps(args).encode("utf-8"))
            except (BrokenPipeError, OSError):
                pass
            finally:
                proc.stdin.close()

        writer = threading.Thread(target=write_request, daemon=True)
        writer.start()

        def consume_line(raw_line: bytes):
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                return
            output_lines.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return
            if isinstance(event, dict) and event.get("event") == "progress" and progress_callback:
                progress_callback(event)

        try:
            while selector.get_map():
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    proc.kill()
                    raise subprocess.TimeoutExpired(proc.args, timeout)
                events = selector.select(min(1.0, remaining))
                if not events and proc.poll() is not None:
                    break
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        if pending:
                            consume_line(pending)
                            pending = b""
                        continue
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        consume_line(line)
            proc.wait(timeout=max(1.0, timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return {"ok": False, "error": f"TTS generation exceeded {timeout} seconds"}
        finally:
            selector.close()
            proc.stdout.close()
            writer.join(timeout=5)

        stderr_file.seek(0)
        stderr_text = stderr_file.read()

    if proc.returncode != 0:
        return {"ok": False, "error": stderr_text[-500:] if stderr_text else "unknown error"}
    try:
        result = json.loads(output_lines[-1])
        if result.get("event") == "result":
            result.pop("event", None)
        return result
    except (json.JSONDecodeError, IndexError):
        return {"ok": False, "error": "\n".join(output_lines[-3:])[-200:] + stderr_text[-200:]}


async def generate_video_narration(
    scenes: list[dict],
    output_dir: str,
    instruct: Optional[str] = None,
    ref_audio: Optional[str] = None,
    ref_text: Optional[str] = None,
    speed: float = 1.0,
    force: bool = False,
    progress_callback: Optional[Callable[[dict], None]] = None,
    engine: str = "omnivoice",
    preset_id: str | None = None,
) -> list[dict]:
    """Generate narration WAVs for scenes with narrator_text.

    Uses batch subprocess — loads model once for all scenes.
    Returns list of result dicts.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Build batch items (only scenes with narrator_text)
    items = []
    scene_map = {}
    cached_scene_ids = []
    progress_total = sum(bool(scene.get("narrator_text")) for scene in scenes)
    signature_data = await asyncio.to_thread(_voice_signature, instruct, ref_audio, ref_text, speed, engine, preset_id)
    for scene in scenes:
        scene_id = scene.get("id")
        display_order = scene.get("display_order", 0)
        narrator_text = scene.get("narrator_text")

        if not narrator_text:
            continue

        wav_path = str(out_dir / f"scene_{display_order:03d}_{scene_id}.wav")
        signature = hashlib.sha256((signature_data + "\n" + narrator_text).encode("utf-8")).hexdigest()
        text_hash = hashlib.sha256(narrator_text.encode("utf-8")).hexdigest()
        cached_path = narration_path_for_scene(out_dir, scene)
        try:
            saved_signature = json.loads(Path(str(cached_path) + ".json").read_text()).get("signature")
        except (OSError, json.JSONDecodeError, AttributeError):
            saved_signature = None
        # Reuse audio only when its text and voice settings still match.
        if not force and cached_path and cached_path.stat().st_size > 1024 and saved_signature == signature and wav_duration(cached_path):
            wav_path = str(cached_path)
            logger.info("Skipping scene %03d (WAV exists: %s)", display_order, wav_path)
            scene_map[scene_id] = {"display_order": display_order, "narrator_text": narrator_text, "skipped": True, "wav_path": wav_path}
            cached_scene_ids.append(scene_id)
            continue
        items.append({"id": scene_id, "text": narrator_text, "output": wav_path, "signature": signature, "text_hash": text_hash})
        scene_map[scene_id] = {"display_order": display_order, "narrator_text": narrator_text}

    # Run batch subprocess if there are items
    batch_results = {}
    if items:
        args = {
            "model": TTS_MODEL,
            "device": TTS_DEVICE,
            "sample_rate": TTS_SAMPLE_RATE,
            "speed": speed,
            "items": items,
            "progress_offset": len(cached_scene_ids),
            "progress_total": progress_total,
        }
        if instruct:
            args["instruct"] = instruct
        if ref_audio:
            args["ref_audio"] = ref_audio
        if ref_text:
            args["ref_text"] = ref_text

        if progress_callback:
            for index, scene_id in enumerate(cached_scene_ids, start=1):
                progress_callback({"event": "progress", "completed": index, "total": progress_total, "id": scene_id, "ok": True, "path": scene_map[scene_id]["wav_path"], "skipped": True})
        loop = asyncio.get_running_loop()
        if engine in {"vieneu", "kokoro", "qwen3"}:
            from agent.services.speech_voices import engine_service, generate
            service = engine_service(engine)
            args.update(voice=service.get_preset(preset_id)["voice"], identity=service.voice_identity(preset_id))
            raw = await loop.run_in_executor(None, generate, args, progress_callback)
        else:
            raw = await loop.run_in_executor(None, _run_batch_subprocess, args, progress_callback)
        for r in raw:
            batch_results[r["id"]] = r
    elif progress_callback:
        for index, scene_id in enumerate(cached_scene_ids, start=1):
            progress_callback({"event": "progress", "completed": index, "total": progress_total, "id": scene_id, "ok": True, "path": scene_map[scene_id]["wav_path"], "skipped": True})

    # Build final results for all scenes
    results = []
    for scene in scenes:
        scene_id = scene.get("id")
        display_order = scene.get("display_order", 0)
        narrator_text = scene.get("narrator_text")

        if not narrator_text:
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": None,
                "audio_path": None,
                "duration": None,
                "status": "SKIPPED",
                "error": None,
            })
            continue

        sm = scene_map.get(scene_id, {})
        if sm.get("skipped"):
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": narrator_text,
                "audio_path": sm["wav_path"],
                "duration": wav_duration(sm["wav_path"]),
                "status": "COMPLETED",
                "error": None,
            })
            continue

        br = batch_results.get(scene_id, {})
        if br.get("ok"):
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": narrator_text,
                "audio_path": br.get("path"),
                "duration": br.get("duration"),
                "status": "COMPLETED",
                "error": None,
            })
        else:
            results.append({
                "scene_id": scene_id,
                "display_order": display_order,
                "narrator_text": narrator_text,
                "audio_path": None,
                "duration": None,
                "status": "FAILED",
                "error": br.get("error", "not processed"),
            })

    return results


def _run_batch_subprocess(args: dict, progress_callback: Optional[Callable[[dict], None]] = None) -> list[dict]:
    """Run one TTS batch, streaming per-scene progress while the model stays loaded."""
    from agent.services import vieneu, kokoro, qwen3
    vieneu.worker.close()
    kokoro.worker.close()
    qwen3.worker.close()
    model_load_timeout = int(os.environ.get("TTS_BATCH_BASE_TIMEOUT_SECONDS", "600"))
    scene_timeout = int(os.environ.get("TTS_BATCH_SCENE_TIMEOUT_SECONDS", "240"))
    timeout = model_load_timeout + len(args.get("items", [])) * scene_timeout
    started = time.monotonic()
    output_lines: list[str] = []
    with tempfile.TemporaryFile(mode="w+t") as stderr_file:
        try:
            proc = subprocess.Popen(
                [PYTHON_BIN, "-u", "-c", _TTS_BATCH_SCRIPT],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=stderr_file, bufsize=0,
            )
        except OSError as exc:
            return [{"id": item["id"], "ok": False, "error": str(exc)} for item in args["items"]]

        assert proc.stdout is not None
        assert proc.stdin is not None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        pending = b""

        def write_request():
            try:
                proc.stdin.write(json.dumps(args).encode("utf-8"))
            except (BrokenPipeError, OSError):
                pass
            finally:
                proc.stdin.close()

        writer = threading.Thread(target=write_request, daemon=True)
        writer.start()

        def consume_line(raw_line: bytes):
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                return
            output_lines.append(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                return
            if isinstance(event, dict) and event.get("event") == "progress" and progress_callback:
                progress_callback(event)

        try:
            while selector.get_map():
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    proc.kill()
                    raise subprocess.TimeoutExpired(proc.args, timeout)
                events = selector.select(min(1.0, remaining))
                if not events and proc.poll() is not None:
                    break
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        if pending:
                            consume_line(pending)
                            pending = b""
                        continue
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        consume_line(line)
            proc.wait(timeout=max(1.0, timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            error = f"TTS generation timed out after {timeout} seconds"
            return [{"id": item["id"], "ok": False, "error": error} for item in args["items"]]
        finally:
            selector.close()
            proc.stdout.close()
            writer.join(timeout=5)

        stderr_file.seek(0)
        stderr_text = stderr_file.read()
    if proc.returncode != 0:
        error = stderr_text[-500:] if stderr_text else "unknown"
        return [{"id": item["id"], "ok": False, "error": error} for item in args["items"]]
    try:
        return json.loads(output_lines[-1])
    except (json.JSONDecodeError, IndexError):
        error = "\n".join(output_lines[-3:])[-200:] + stderr_text[-200:]
        return [{"id": item["id"], "ok": False, "error": error} for item in args["items"]]
