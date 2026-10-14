"""Versioned, cancellable audio review and speaker suggestions for the editor."""
from agent.media_limits import MAX_DURATION
import hashlib
import importlib.util
import json
import logging
import math
from pathlib import Path
import re
import sys
import tempfile
import time
from uuid import uuid4

from agent.config import BASE_DIR
from agent.services import editing, imported_video, media_jobs as jobs, speech, speech_store as store, transcription
from agent.services.ffmpeg_progress import processing_timeout
from agent.services.speech_analysis_core import assign_turns, recognized_cue
from agent.services.tts_runtime import TTS_SEMAPHORE
from agent.services.video_assembly import assembled_path, render_video_to_file, video_export_lock

MODELS = BASE_DIR / ".flowkit" / "models" / "diarization"
SEGMENTATION = MODELS / "sherpa-onnx-pyannote-segmentation-3-0" / "model.onnx"
EMBEDDING = MODELS / "embedding.onnx"
REPORTS = ("signal_speech", "signal_export", "words", "speakers")
KINDS = {"analysis_signal", "analysis_words", "analysis_speakers"}
logger = logging.getLogger(__name__)


def export_source(vid):
    candidates = [e for e in editing.load(vid)['exports'] if e.get('operation') == 'timeline'
        and not e.get('range_start', 0) and e.get('range_end') is None and e.get('export_id')]
    for result in sorted(candidates, key=lambda e: e.get('created_at', 0), reverse=True):
        path = editing.directory(vid) / 'exports' / f"{store.identifier(result['export_id'])}.mp4"
        if path.is_file():
            return path, f"/api/videos/{vid}/editing/exports/{result['export_id']}"
    return assembled_path(vid), f'/api/videos/{vid}/file'


def capabilities():
    measurement = all(importlib.util.find_spec(module) for module in ("numpy", "soundfile"))
    return {"signal_ready": measurement,
            "speakers_ready": bool(measurement and importlib.util.find_spec("sherpa_onnx") and SEGMENTATION.is_file() and EMBEDDING.is_file()),
            "models": transcription.models(), "whisper_available": bool(transcription.binary())}


def stamp(path):
    if not path.is_file():
        return None
    info = path.stat()
    return [info.st_size, info.st_mtime_ns]


def assets(vid, doc):
    items, skipped = [], []
    for cue in doc.cues:
        status = speech.cue_status(vid, cue)
        if status.get("audio_url"):
            path = speech.cue_audio(vid, cue)[0]
            items.append({"id": str(cue.id), "path": str(path), "start": cue.start,
                          "end": cue.end, "text": cue.text})
        else:
            skipped.append(str(cue.id))
    return items, skipped


def fingerprint(vid, doc, scenes, key):
    state = {"timeline": store.timeline_key(scenes)}
    if key == "signal_export":
        path, url = export_source(vid)
        state.update(file=stamp(path), export_url=url)
    else:
        state["document"] = doc.model_dump(mode="json")
        if key == "speakers":
            state["source"] = stamp(imported_video.source_path(vid))
        else:
            audio = []
            for cue in doc.cues:
                try:
                    path = speech.cue_audio(vid, cue)[0]
                    audio.append([str(cue.id), path.name, stamp(path), stamp(path.with_suffix('.wav.json'))])
                except (ValueError, OSError):
                    audio.append([str(cue.id), None])
            state["audio"] = audio
    return hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()


def reports(vid, doc, scenes):
    result = {}
    for key in REPORTS:
        report = store.read_json(jobs.directory(vid) / f"analysis-{key}.json")
        if report:
            result[key] = {**report, "stale": report["fingerprint"] != fingerprint(vid, doc, scenes, key)}
    return result


async def worker(config, work, update, low=10, high=95):
    output = work / f"{config['mode']}.json"
    previous = None
    def on_line(line):
        nonlocal previous
        try:
            event = json.loads(line)
            current = (round(low + min(100, max(0, event["progress"])) / 100 * (high - low)), event["stage"])
            if current != previous:
                update(progress=current[0], stage=current[1])
                previous = current
        except (ValueError, KeyError, TypeError):
            pass
    await jobs.process([sys.executable, "-m", "agent.services.speech_analysis_worker"],
                       stdin=json.dumps({**config, "output": str(output)}), cwd=str(BASE_DIR),
                       timeout=14400, on_line=on_line)
    return store.read_json(output)


async def extract_audio(source, output, update, duration, *, mono=True, low=2, high=9):
    if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
        raise ValueError("Kiểm tra âm thanh hỗ trợ tối đa 180 phút. Hãy chia video dài thành các phần.")
    previous = low
    def progress(line):
        nonlocal previous
        if line.startswith("out_time_us="):
            try:
                fraction = int(line.split("=", 1)[1]) / 1e6 / max(duration, 1)
                previous = max(previous, low + min(high - low, round(max(0, fraction) * (high - low))))
                update(progress=previous)
            except ValueError:
                pass
    update(stage="Chuẩn bị âm thanh để kiểm tra", progress=low)
    await jobs.process(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
                        "-map", "0:a:0", "-vn", "-ac", "1" if mono else "2", "-ar", "16000" if mono else "48000",
                        "-c:a", "pcm_s16le" if mono else "pcm_f32le", "-t", str(MAX_DURATION), "-progress", "pipe:1", str(output)],
                       timeout=processing_timeout(duration, minimum=600), on_line=progress)


async def source_audio(vid, scenes, work, update):
    source = imported_video.source_path(vid)
    duration = sum(float(scene.get("duration") or 8) for scene in scenes)
    generated = not imported_video.metadata(vid)
    if generated:
        source = work / "source.mp4"
        update(stage="Chuẩn bị video gốc", progress=1)
        def render_progress(stage, fraction):
            if stage == "downloading":
                update(stage="Tải các cảnh video gốc", progress=1 + round(fraction * 5))
            elif stage == "assembling":
                update(stage="Ghép video gốc để nghe", progress=6 + round(fraction * 2))
        async with video_export_lock:
            result = await render_video_to_file(vid, source, progress_callback=render_progress)
        duration = result["duration"]
    output = work / "source.wav"
    await extract_audio(source, output, update, duration, low=8 if generated else 2)
    return output


async def source_preview(wav, work, update):
    """Keep a small playable copy of exactly the source used for speaker analysis."""
    update(stage="Lưu bản nghe lại giọng gốc", progress=96)
    output = work / "source.m4a"
    await jobs.process(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(wav),
                        "-c:a", "aac", "-b:a", "48k", "-movflags", "+faststart", str(output)], timeout=1800)
    return output


def publish(vid, key, result, preview=None):
    directory = jobs.directory(vid)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"analysis-{key}.json"
    old = store.read_json(target, {})
    path = None
    if preview:
        token = str(uuid4())
        path = directory / f"analysis-source-{token}.m4a"
        preview.replace(path)
        result.update(source_audio_id=token, source_audio_url=f"/api/videos/{vid}/speech-analysis/source/{token}")
    try:
        store.atomic_json(target, result)
    except Exception:
        if path:
            path.unlink(missing_ok=True)
        raise
    if preview and old.get("source_audio_id"):
        try:
            (directory / f"analysis-source-{store.identifier(old['source_audio_id'])}.m4a").unlink(missing_ok=True)
        except (ValueError, OSError):
            logger.warning("Could not remove the previous speaker preview for %s", vid, exc_info=True)


async def recognize(items, model, language, work, update, *, cpu=False, minimum_timeout=3600):
    """Load the model once per bounded batch; never infer cue identity from timestamps."""
    results, completed = [], 0
    total = len(items)
    for offset in range(0, total, 32):
        batch = items[offset:offset + 32]
        outputs = [work / f"recognized-{offset + index}" for index in range(len(batch))]
        def progress(line):
            nonlocal completed
            if "output_json: saving output" in line:
                completed = min(offset + len(batch), completed + 1)
                update(progress=10 + round(completed / total * 85),
                       stage=f"Đã đối chiếu {completed}/{total} câu")
            else:
                match = re.search(r"progress\s*=\s*(\d+)%", line)
                if match:
                    update(progress=10 + round((completed + int(match[1]) / 100) / total * 85))
        update(stage=f"Whisper đang nghe lại lời đọc · {offset}/{total} câu", progress=10 + round(offset / total * 85))
        args = [transcription.binary(), "-m", str(transcription.MODEL_DIR / f"ggml-{model}.bin"),
                "-l", language, "-ojf", "-pp", "-t", "4"]
        if cpu:
            args.append("-ng")
        for item, output in zip(batch, outputs):
            args += ["-f", item["path"], "-of", str(output)]
        options = {"on_line": progress, "timeout": processing_timeout(sum(item["duration"] for item in batch), minimum=minimum_timeout)}
        try:
            await jobs.process(args, **options)
        except RuntimeError as exc:
            if cpu or "ggml_metal" not in str(exc).lower():
                raise
            completed = offset
            update(stage="Whisper chuyển sang CPU do GPU không sẵn sàng")
            await jobs.process([*args, "-ng"], **options)
        for item, output in zip(batch, outputs):
            transcript = store.read_json(output.with_suffix(".json"))
            if not transcript or "transcription" not in transcript:
                raise ValueError("Whisper không trả đủ kết quả lời đọc. Báo cáo cũ được giữ nguyên; hãy chạy lại.")
            results.append(recognized_cue(transcript, item))
        completed = offset + len(batch)
        update(progress=10 + round(completed / total * 85), stage=f"Đã đối chiếu {completed}/{total} câu")
    return results


def start(vid, doc, scenes, request):
    kind, target = request.kind, request.target
    key = f"signal_{target}" if kind == "signal" else kind
    available = capabilities()
    if not available["signal_ready"]:
        raise ValueError("Chưa có thư viện kiểm tra âm thanh. Cài requirements-speech-analysis.txt vào môi trường FrameWeaver.")
    if kind == "speakers" and not available["speakers_ready"]:
        raise ValueError("Chưa có bộ nhận diện người nói. Xem hướng dẫn trong docs/speech-analysis.md.")
    if kind in {"words", "speakers"} or target == "speech":
        if not doc.cues or not doc.revision:
            raise ValueError("Nhận dạng hoặc nhập lời thoại rồi lưu kịch bản trước.")
        speech.validate_document(doc, scenes, vid)
    if kind == "words":
        if not transcription.binary() or not any(m["id"] == request.model and m["ready"] for m in available["models"]):
            raise ValueError("Chọn model Whisper đã tải trên máy.")
        if request.language not in transcription.LANGUAGES or request.language == "auto":
            raise ValueError("Chọn đúng ngôn ngữ của lời đọc để đối chiếu.")
    items, skipped = assets(vid, doc) if kind == "words" or (kind == "signal" and target == "speech") else ([], [])
    if (kind == "words" or kind == "signal" and target == "speech") and not items:
        raise ValueError("Chưa có lời đọc hợp lệ. Tạo ít nhất một câu trước khi kiểm tra.")
    export_path, export_url = export_source(vid) if kind == 'signal' and target == 'export' else (None, None)
    if export_path is not None and not export_path.is_file():
        raise ValueError("Chưa có bản video đã ghép. Ghép video trước khi kiểm tra.")
    signature = fingerprint(vid, doc, scenes, key)

    async def run(update):
        update(stage="Chờ lượt phân tích âm thanh")
        # Shares the model slot with Whisper/TTS, keeping memory use bounded.
        async with TTS_SEMAPHORE:
            with tempfile.TemporaryDirectory(prefix="analysis-", dir=jobs.directory(vid)) as temp:
                work = Path(temp)
                result = {"key": key, "revision": doc.revision, "fingerprint": signature,
                          "created_at": time.time(), "skipped": skipped, "total_cues": len(doc.cues)}
                preview = None
                if kind == "signal":
                    inputs = items
                    if target == "export":
                        wav = work / "export.wav"
                        info = await imported_video.probe(export_path)
                        await extract_audio(export_path, wav, update, info['duration'], mono=False)
                        result['export_url'] = export_url
                        inputs = [{"id": "export", "path": str(wav), "start": 0, "end": 0, "text": "Video đã ghép"}]
                    result["items"] = await worker({"mode": "signal", "items": inputs}, work, update)
                elif kind == "words":
                    prepared = await worker({"mode": "prepare", "items": items, "directory": str(work)}, work, update, 1, 10)
                    cues = await recognize(prepared, request.model, request.language, work, update)
                    result.update(model=request.model, language=request.language, cues=cues)
                else:
                    wav = await source_audio(vid, scenes, work, update)
                    turns = await worker({"mode": "speakers", "wav": str(wav), "segmentation": str(SEGMENTATION),
                                          "embedding": str(EMBEDDING), "num_speakers": request.num_speakers}, work, update)
                    if not turns:
                        raise ValueError("Không tìm thấy lượt nói rõ ràng. Kiểm tra nguồn hoặc gán người nói thủ công.")
                    result.update(assign_turns(doc.model_dump(mode="json")["cues"], turns))
                    result.update(engine="sherpa-onnx", num_speakers=request.num_speakers)
                    preview = await source_preview(wav, work, update)
                current_scenes = await imported_video.timeline(vid)
                if fingerprint(vid, store.load_document(vid), current_scenes, key) != signature:
                    raise ValueError("Nội dung hoặc âm thanh đã đổi khi đang kiểm tra. Hãy chạy lại trên bản mới.")
                publish(vid, key, result, preview)
    return jobs.start(vid, f"analysis_{kind}", run)
