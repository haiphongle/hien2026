"""Local Whisper transcription. Results remain drafts until explicitly applied."""
import json
import hashlib
import math
import re
import shutil
import tempfile
from pathlib import Path

from agent.config import BASE_DIR
from agent.models.speech import Cue, SpeechDocument
from agent.media_limits import MAX_DURATION
from agent.services import imported_video, media_jobs as jobs, pacing, timeline
from agent.models.timeline import TimelineDocument
from agent.services.ffmpeg_progress import processing_timeout
from agent.services.speech_store import atomic_json, read_json
from agent.services.video_assembly import render_video_to_file, video_export_lock
from agent.services.tts_runtime import TTS_SEMAPHORE
from agent.services import model_storage, whisper_models

MODEL_DIR = BASE_DIR / ".flowkit" / "models" / "whisper"
MODELS = {"small": "Whisper Small · đa ngôn ngữ", "base": "Whisper Base · nhanh hơn",
          "large-v3-turbo-q5_0": "Whisper Large v3 Turbo · Q5"}
LANGUAGES = {"auto": "Tự nhận diện", "vi": "Tiếng Việt", "en": "Tiếng Anh", "zh": "Tiếng Trung",
             "ja": "Tiếng Nhật", "ko": "Tiếng Hàn", "fr": "Tiếng Pháp", "es": "Tiếng Tây Ban Nha",
             "de": "Tiếng Đức", "th": "Tiếng Thái", "ru": "Tiếng Nga",
             "pt": "Tiếng Bồ Đào Nha", "it": "Tiếng Ý"}
CHUNK_SECONDS = 600
OVERLAP_SECONDS = 2
CHECKPOINT_VERSION = 3


def binary():
    return shutil.which("whisper-cli") or ("/opt/homebrew/bin/whisper-cli" if Path("/opt/homebrew/bin/whisper-cli").is_file() else None)


def models():
    return [{"id": key, "name": name, **whisper_models.status(key, model_path(key).parent)}
            for key, name in MODELS.items()]


def model_path(model):
    return model_storage.location(f'whisper-{model}', MODEL_DIR)/f'ggml-{model}.bin'


def normalize(data: dict, duration: float, *, allow_empty=False) -> list[Cue]:
    cues = []
    for segment in data.get("transcription", []):
        text = re.sub(r"\[_[^\]]+\]", "", segment.get("text", "")).strip()
        if not text:
            continue
        start = max(0.0, float(segment["offsets"]["from"]) / 1000)
        end = min(duration, float(segment["offsets"]["to"]) / 1000)
        if cues:
            start = max(start, cues[-1].end)
        if end <= start:
            continue
        cues.append(Cue(start=round(start, 3), end=round(end, 3), text=text))
    if not cues and not allow_empty:
        raise ValueError("Không nhận ra lời nói. Kiểm tra âm thanh hoặc chọn đúng ngôn ngữ rồi thử lại.")
    # Enforce limits, unique IDs and timing before persisting anything.
    return SpeechDocument(cues=cues).cues


def checkpoint_status(vid):
    saved = read_json(jobs.directory(vid) / "transcription_checkpoint.json")
    if not saved or saved.get("version") != CHECKPOINT_VERSION:
        return None
    source = saved.get('source')
    edit = timeline.load(vid)
    compatible = not isinstance(source, dict) or source.get('edit_revision') == (edit or {}).get('revision')
    return {key: saved[key] for key in ("completed", "total", "model", "language", "start", "end", "timeline")} | {'compatible': compatible}


def windows(start_time, end_time):
    """Bound PCM/model memory; overlap allows words at a cut to retain context."""
    return [(begin, min(end_time, begin + CHUNK_SECONDS))
            for begin in (start_time + i * CHUNK_SECONDS for i in range(math.ceil((end_time-start_time) / CHUNK_SECONDS)))]


def append_chunk(cues, data, offset, duration, begin, end):
    # Keep the context at both edges. Midpoint ownership can lose a sentence
    # when the two Whisper calls disagree on its timing/segmentation.
    previous = [c for c in cues if c.end > offset]
    cues = list(cues)
    for cue in normalize(data, duration, allow_empty=True):
        a, b = cue.start + offset, cue.end + offset
        overlapping = [c for c in previous if c.start < b and c.end > a]
        words = list(re.finditer(r"\w+", cue.text))
        incoming = [w.group().casefold() for w in words]
        known = re.findall(r"\w+", ' '.join(c.text for c in overlapping).casefold())
        if incoming and known:
            # Deduplicate only actual overlap with an earlier chunk. Repeated
            # speech inside this call (or after a pause) is always retained.
            if any(known[i:i+len(incoming)] == incoming for i in range(len(known)-len(incoming)+1)):
                if overlapping[-1].id == cues[-1].id and b > cues[-1].end:
                    cues[-1] = cues[-1].model_copy(update={"end": round(b, 3)})
                continue
            shared = next((n for n in range(min(len(known), len(incoming)), 0, -1)
                           if known[-n:] == incoming[:n]), 0)
            if shared:
                cue = cue.model_copy(update={"text": cue.text[words[shared].start():]})
        if overlapping and cues and b <= cues[-1].end:
            # Timing disagreement must not silently discard unmatched words.
            cues[-1] = Cue(**{**cues[-1].model_dump(), "text": cues[-1].text + ' ' + cue.text})
            continue
        a = max(0, a, cues[-1].end if cues else 0)
        if b > a:
            cues.append(cue.model_copy(update={"start": round(a, 3), "end": round(b, 3)}))
    return SpeechDocument(cues=cues).cues


def bounds(duration, start_time, end_time):
    end_time = duration if end_time is None else end_time
    if not all(math.isfinite(v) for v in (duration, start_time, end_time)) or not 0 <= start_time < end_time <= min(duration, MAX_DURATION):
        raise ValueError("Chọn đoạn nhận dạng có điểm đầu nhỏ hơn điểm cuối và nằm trong video (tối đa 180 phút).")
    return end_time


def start(vid, model, language, document, scenes, *, start_time=0, end_time=None, restart=False, source_info=None):
    executable = binary()
    weights = model_path(model)
    model_storage.assert_available(f'whisper-{model}', weights.parent)
    if not executable or model not in MODELS or not whisper_models.status(model, weights.parent)['ready']:
        raise ValueError("Chưa có whisper-cli hoặc model đã chọn trên máy.")
    if language not in LANGUAGES:
        raise ValueError("Ngôn ngữ không hợp lệ.")
    info = source_info if source_info is not None else imported_video.metadata(vid)
    saved_edit = None if source_info is not None else timeline.load(vid)
    edit = TimelineDocument.model_validate(saved_edit['document']) if saved_edit and saved_edit['document']['clips'] else None
    edit_assets = timeline.validate_assets(vid, edit) if edit else {}
    if edit:
        if not timeline.sound_clips(edit):
            raise ValueError('Bản dựng chưa có lớp âm thanh đang bật. Đưa lời đọc lên timeline hoặc bật Âm gốc trước khi nhận dạng; cũng có thể nhập SRT.')
        document = document.model_copy(update={'edit_signature': timeline.timing_key(edit)})
    # Scene duration in the DB is only an estimate. Resolve full-video bounds
    # from the actual assembled file before planning chunks or cache identity.
    bounds(timeline.duration(edit) if edit else float(info["duration"]) if info else MAX_DURATION, start_time, end_time)
    async def run(update):
        update(stage="Chờ lượt nhận dạng")
        async with TTS_SEMAPHORE:
            with tempfile.TemporaryDirectory(prefix="asr-", dir=jobs.directory(vid)) as temp:
                work = Path(temp)
                source = Path(source_info['path']) if source_info is not None else imported_video.source_path(vid)
                if edit:
                    duration = timeline.duration(edit)
                elif info:
                    duration = float(info["duration"])
                else:
                    source = work / "source.mp4"
                    update(stage="Chuẩn bị video", progress=1)
                    async with video_export_lock:
                        result = await render_video_to_file(vid, source)
                    duration = float(result["duration"])
                stop = bounds(duration, start_time, end_time)
                if not edit:
                    measured = await pacing.probe(source)
                    if not measured['has_audio']:
                        raise ValueError('Video nguồn không có âm thanh. Đưa lời đọc lên timeline rồi nhận dạng lại, hoặc dùng lời bình/SRT có sẵn.')
                source_stat = source.stat() if info and not edit else None
                weight_stat = weights.stat()
                settings = dict(model=model, language=language, start=start_time, end=stop, timeline=document.timeline,
                    source=(source_stat.st_size, source_stat.st_mtime_ns) if source_stat else None, duration=duration,
                    weights=(weight_stat.st_size, weight_stat.st_mtime_ns), chunk_seconds=CHUNK_SECONDS, version=CHECKPOINT_VERSION)
                if edit:
                    settings['source'] = {'edit_revision': saved_edit['revision'], 'timing': document.edit_signature,
                        'assets': {aid: a['fingerprint'] for aid, a in edit_assets.items()}}
                key = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
                parts = windows(start_time, stop)
                checkpoint = jobs.directory(vid) / "transcription_checkpoint.json"
                saved = read_json(checkpoint, {}) if not restart else {}
                completed = saved.get("completed", 0) if saved.get("key") == key else 0
                cues = SpeechDocument(cues=saved.get("cues", []) if completed else []).cues
                detected = saved.get("detected_language", language) if completed else language
                def save():
                    atomic_json(checkpoint, {**settings, "key": key, "completed": completed, "total": len(parts),
                                            "detected_language": detected, "cues": [c.model_dump(mode="json") for c in cues]})
                save()
                for index, (begin, end) in enumerate(parts):
                    if index < completed:
                        continue
                    offset = max(start_time, begin-OVERLAP_SECONDS)
                    span = min(stop, end+OVERLAP_SECONDS) - offset
                    base = 5 + 90 * index / len(parts)
                    step = 90 / len(parts)
                    stage = f"Nhận dạng phần {index+1}/{len(parts)}"
                    update(stage=f"Trích âm thanh · phần {index+1}/{len(parts)}", progress=round(base))
                    wav = work / "audio.wav"
                    if edit:
                        def mixing_progress(**changes):
                            update(stage=f"Trộn âm thanh bản dựng · phần {index+1}/{len(parts)}",
                                   progress=round(base+step*.1*changes.get('progress', 0)/100))
                        span = await timeline.render_audio(vid, edit, wav, mixing_progress, start=offset, end=offset+span, allow_silence=True)
                        offset = round(offset*edit.fps)/edit.fps
                    else:
                        await jobs.process(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(offset),
                            "-i", str(source), "-t", str(span), "-map", "0:a:0", "-ar", "16000", "-ac", "1",
                            "-c:a", "pcm_s16le", str(wav)], timeout=processing_timeout(span))
                    update(stage=stage, progress=round(base + step*.1))
                    (work / "transcript.json").unlink(missing_ok=True)
                    previous = None
                    def progress(line):
                        nonlocal previous
                        match = re.search(r"progress\s*=\s*(\d+)%", line)
                        if match:
                            value = round(base + step * (.1 + min(100, int(match[1])) / 100 * .9))
                            if value != previous:
                                update(stage=stage, progress=value)
                                previous = value
                    await jobs.process([executable, "-m", str(weights), "-f", str(wav), "-l", language,
                        "-oj", "-of", str(work / "transcript"), "-pp", "-ml", "84", "-sow", "-t", "4"],
                        on_line=progress, timeout=processing_timeout(span, minimum=3600))
                    data = json.loads((work / "transcript.json").read_text())
                    cues = append_chunk(cues, data, offset, span, begin, end)
                    detected = data.get("result", {}).get("language", detected)
                    completed = index+1
                    save()
                    update(stage=f"Đã lưu phần {completed}/{len(parts)}", progress=round(base+step))
                if not cues:
                    raise ValueError("Không nhận ra lời nói. Kiểm tra âm thanh hoặc chọn đúng ngôn ngữ rồi thử lại.")
                result = {"document": document.model_copy(update={"mode": "srt", "cues": cues}).model_dump(mode="json"),
                          "language": detected, "model": model, "start": start_time, "end": stop,
                          "scope": "range" if start_time > 0 or stop < duration else "all",
                          "kind": "transcript"}
                atomic_json(jobs.directory(vid) / "transcript.json", result)
    return jobs.start(vid, "content_transcribe" if source_info is not None else "transcribe", run,
                      metadata={'model': model, 'engine': 'Whisper'} if source_info is not None else None)
