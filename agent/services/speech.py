"""Per-cue synthesis and export validation for multi-speaker dialogue/SRT."""
import asyncio
import hashlib
import json
import math
import shutil
import subprocess

from agent.models.speech import Cue, SpeechDocument
from agent.services import speech_store as store, speech_voices, speech_jobs
from agent.services.tts_runtime import TTS_SEMAPHORE
from agent.services.voice_samples import wav_duration


def cue_audio(vid: str, cue: Cue):
    voice, identity = speech_voices.resolve_voice(cue.voice_id)
    signature = hashlib.sha256(json.dumps({"text": cue.text, "voice": identity, "speed": float(cue.speed)}, sort_keys=True).encode()).hexdigest()
    path = store.video_dir(vid) / "audio" / f"{cue.id}_{signature[:24]}.wav"
    return path, voice, identity, signature


def cue_status(vid: str, cue: Cue):
    result = {"id": str(cue.id), "duration": None, "status": "MISSING", "audio_url": None, "error": None}
    if not cue.voice_id:
        return {**result, "status": "UNASSIGNED"}
    try:
        path, _, identity, signature = cue_audio(vid, cue)
        duration = wav_duration(path) if path.is_file() else None
        metadata = store.read_json(path.with_suffix(".wav.json"), {})
        if not duration or not isinstance(metadata, dict) or metadata.get("signature") != signature:
            return result
        if identity["engine"] == "qwen3":
            warning = speech_voices.qwen3.audio_warning(path, cue.text, identity["language"], cue.speed, duration)
            if warning:
                return {**result, "error": warning}
        return {**result, "duration": duration, "status": "TOO_LONG" if duration > cue.end - cue.start + 0.04 else "READY",
                "audio_url": f"/api/videos/{vid}/speech/cues/{cue.id}/audio?v={signature[:24]}-{path.stat().st_mtime_ns}"}
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        return {**result, "status": "INVALID_VOICE", "error": str(exc)}


def validate_document(doc: SpeechDocument, scenes: list[dict], vid=None):
    if doc.timeline != store.timeline_key(scenes):
        raise ValueError("Cảnh hoặc thời lượng đã thay đổi. Kiểm tra lại mốc thời gian và lưu kịch bản để xác nhận.")
    duration = sum(float(s.get("duration") or 8) for s in scenes)
    if doc.edit_signature:
        from agent.services import timeline
        from agent.models.timeline import TimelineDocument
        saved = timeline.load(vid) if vid else None
        edit = TimelineDocument.model_validate(saved['document']) if saved else None
        if not edit or timeline.timing_key(edit) != doc.edit_signature:
            raise ValueError('Bản dựng đã cắt, di chuyển hoặc đổi tốc độ. Nhận dạng lại để phụ đề khớp mốc mới.')
        duration = timeline.duration(edit)
    if doc.cues and doc.cues[-1].end > duration + 0.1:
        raise ValueError(f"Câu cuối vượt thời lượng video ({duration:.2f} giây). Hãy sửa mốc kết thúc.")


def prepare_export(vid: str, revision: int, scenes: list[dict], *, captions_only=False):
    doc = store.load_document(vid)
    if doc.revision != revision:
        raise ValueError("Kịch bản đã thay đổi. Bấm ghép hội thoại lại từ phiên bản mới nhất.")
    validate_document(doc, scenes, vid)
    if doc.edit_signature:
        raise ValueError('Phụ đề dùng mốc bản dựng. Áp dụng lên timeline rồi xuất để giữ đúng các đoạn đã cắt.')
    if not doc.cues:
        raise ValueError("Chưa có câu thoại để ghép.")
    tracks, captions = [], []
    cursor = 0.0
    for i, cue in enumerate(doc.cues, 1):
        if not captions_only:
            status = cue_status(vid, cue)
            if status["status"] == "TOO_LONG":
                raise ValueError(f"Câu {i} dài {status['duration']:.2f}s, vượt khung {cue.end - cue.start:.2f}s. Rút gọn lời, tăng tốc hoặc sửa thời gian rồi tạo lại.")
            if status["status"] != "READY":
                raise ValueError(f"Câu {i} chưa có âm thanh hợp lệ. Hãy chọn giọng và tạo lời trước.")
            path, _, _, _ = cue_audio(vid, cue)
            tracks.append((str(path), cue.start, cue.end - cue.start))
        # Reuse the existing macOS-compatible caption renderer with explicit
        # silent gaps and the complete cue interval, independent of read length.
        if cue.start > cursor:
            captions.append({"duration": cue.start - cursor, "narrator_text": ""})
        captions.append({"duration": cue.end - cue.start, "narrator_text": cue.text})
        cursor = cue.end
    return tracks, captions, doc


def generation_cues(doc: SpeechDocument, cue_ids: list[str] | None):
    selected = set(cue_ids) if cue_ids is not None else {str(c.id) for c in doc.cues}
    cues = [c for c in doc.cues if str(c.id) in selected]
    if not cues or selected != {str(c.id) for c in cues}:
        raise ValueError("Chọn ít nhất một câu có trong kịch bản.")
    for cue in cues:
        speech_voices.resolve_voice(cue.voice_id)
    return cues


async def generate_cues(vid, cues, force, job, update):
    loop = asyncio.get_running_loop()
    # Keep one model resident while processing its cues; timeline order is untouched.
    cues = sorted(cues, key=lambda cue: speech_voices.resolve_voice(cue.voice_id)[1]["engine"])
    for index, cue in enumerate(cues):
        if speech_jobs.cancel_requested(vid):
            update(cancel_requested=True)
            break
        cid = str(cue.id)
        update(current=cid, stage="queued")
        path, voice, identity, signature = cue_audio(vid, cue)
        status = cue_status(vid, cue)
        if not force and status["audio_url"]:
            result = {"ok": True, "id": cid, "duration": status["duration"], "cached": True}
        else:
            async with TTS_SEMAPHORE:
                if speech_jobs.cancel_requested(vid):
                    update(cancel_requested=True)
                    break
                update(status="PROCESSING")
                def apply_progress(event):
                    update(stage=event.get("stage", "generating"))
                def progress(event):
                    loop.call_soon_threadsafe(apply_progress, event)
                try:
                    results = await asyncio.to_thread(speech_voices.generate, {
                        "voice": voice, "identity": identity, "speed": cue.speed,
                        "items": [{"id": cid, "text": cue.text, "output": str(path), "signature": signature,
                                   "text_hash": hashlib.sha256(cue.text.encode()).hexdigest()}],
                    }, progress)
                    result = results[0]
                except (TimeoutError, RuntimeError, OSError, ValueError) as exc:
                    result = {"id": cid, "ok": False, "error": str(exc)}
        update(completed=index + 1, results={**job["results"], cid: result})
        if job.get("cancel_requested"):
            break


def start_generation(vid: str, doc: SpeechDocument, cue_ids: list[str] | None, force: bool):
    cues = generation_cues(doc, cue_ids)

    async def run(job, update):
        await generate_cues(vid, cues, force, job, update)

    return speech_jobs.start_job(vid, store.video_dir(vid) / "job.json", len(cues), run)


def automatic_preview(vid, doc):
    try:
        saved = store.read_json(store.video_dir(vid) / "automatic.json")
        if not isinstance(saved, dict) or saved.get("revision") != doc.revision or type(saved.get("round")) is not int or not 0 <= saved["round"] <= 2:
            return None
        original = SpeechDocument.model_validate(saved["original"])
        if original.timeline != doc.timeline:
            return None
        return saved
    except (KeyError, ValueError, OSError):
        return None


def fit_measured(doc, audio, duration):
    """Consume free gaps only, then cap speed; starts and cue IDs stay fixed."""
    measured = {item["id"]: item for item in audio}
    cues = []
    for index, cue in enumerate(doc.cues):
        item = measured[str(cue.id)]
        length = item["duration"]
        changes = {}
        if item["audio_url"] and length > cue.end - cue.start + .04:
            end = max(cue.end, min(duration, doc.cues[index + 1].start if index + 1 < len(doc.cues) else duration,
                                   cue.start + math.ceil(length * 1000) / 1000))
            changes["end"] = end
            speed = math.ceil(cue.speed * length / max(.01, end - cue.start - .02) * 100) / 100
            if length > end - cue.start + .04 and cue.speed < speed <= 1.25:
                changes["speed"] = speed
        cues.append(cue.model_copy(update=changes))
    return doc.model_copy(update={"cues": cues})


def retime_cue_audio(vid, before, after):
    """Qwen/VieNeu apply speed after inference; reuse speech when only speed changes."""
    if (before.id, before.text, before.voice_id) != (after.id, after.text, after.voice_id) or before.speed >= after.speed:
        return False
    source, _, identity, _ = cue_audio(vid, before)
    if identity["engine"] not in {"qwen3", "vieneu"} or not cue_status(vid, before)["audio_url"]:
        return False
    if cue_status(vid, after)["audio_url"]:
        return True
    target, _, _, signature = cue_audio(vid, after)
    staged = target.with_name(f".{target.stem}.fit.wav")
    metadata_path = target.with_suffix(".wav.json")
    quality = speech_voices.qwen3.cached_quality(source, before.text, identity["language"], before.speed) if identity["engine"] == "qwen3" else None
    metadata = store.read_json(source.with_suffix(".wav.json"), {})
    try:
        ratio = after.speed / before.speed
        tempo = f"atempo=2,atempo={ratio / 2}" if ratio > 2 else f"atempo={ratio}"
        subprocess.run([shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg", "-nostdin", "-v", "error", "-y",
                        "-i", str(source), "-af", tempo, "-c:a", "pcm_s16le", str(staged)],
                       check=True, capture_output=True, timeout=90)
        if not wav_duration(staged):
            raise ValueError("Không đọc được âm thanh sau khi chỉnh tốc độ.")
        metadata = {**metadata, "signature": signature, "speed": after.speed}
        metadata.pop("quality", None)
        if quality is not None:
            info = staged.stat()
            metadata["quality"] = {**quality, "stamp": [info.st_size, info.st_mtime_ns], "speed": after.speed}
        staged.replace(target)
        try:
            store.atomic_json(metadata_path, metadata)
            if not cue_status(vid, after)["audio_url"]:
                raise ValueError("Âm thanh sau khi chỉnh tốc độ chưa hợp lệ.")
        except Exception:
            target.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            raise
        return True
    finally:
        staged.unlink(missing_ok=True)


def fit_nearby_cues(doc, audio):
    """Borrow spare time from adjacent cues without moving a boundary over 0.75s."""
    measured = {item["id"]: item for item in audio}
    cues = list(doc.cues)
    first = 0
    while first < len(cues):
        last = first + 1
        while last < len(cues) and cues[last].start <= cues[last - 1].end + .001:
            last += 1
        group = cues[first:last]
        samples = [measured[str(cue.id)] for cue in group]
        if len(group) > 1 and all(item["audio_url"] and item["duration"] for item in samples) and any(
            item["status"] == "TOO_LONG" for item in samples
        ):
            required = [item["duration"] + .01 for item in samples]
            original = [group[0].start] + [cue.end for cue in group]
            lower = [original[0]]
            for index in range(1, len(group)):
                lower.append(max(original[index] - .75, lower[-1] + required[index - 1]))
            upper = [0.0] * len(group) + [original[-1]]
            for index in range(len(group) - 1, 0, -1):
                upper[index] = min(original[index] + .75, upper[index + 1] - required[index])
            if lower[-1] + required[-1] <= original[-1] + 1e-6 and all(
                lower[index] <= upper[index] + 1e-6 for index in range(1, len(group))
            ):
                boundaries = [original[0]]
                for index in range(1, len(group)):
                    boundaries.append(round(max(lower[index], min(original[index], upper[index]),
                                                boundaries[-1] + required[index - 1]), 3))
                boundaries.append(original[-1])
                for index, cue in enumerate(group):
                    cues[first + index] = cue.model_copy(update={"start": boundaries[index], "end": boundaries[index + 1]})
        first = last
    return doc.model_copy(update={"cues": cues})


def start_automatic(vid, doc, options):
    from agent.services import imported_video, media_jobs, subtitle_translation
    generation_cues(doc, None)
    if any(options.language not in speech_voices.voice_languages(c.voice_id) for c in doc.cues):
        language = {"vi": "tiếng Việt", "en": "tiếng Anh"}.get(options.language, f"ngôn ngữ {options.language}")
        raise ValueError(f"Giọng đã chọn không hỗ trợ {language}. Chọn giọng đúng ngôn ngữ cho từng người nói.")
    if options.cue_ids is not None or options.force:
        raise ValueError("Tự xử lý dùng toàn bộ kịch bản và giữ lại các giọng hợp lệ.")
    settings = {key: getattr(options, key) for key in ("language", "provider", "synopsis", "glossary")}
    preview = automatic_preview(vid, doc)
    if not preview or preview.get("options") != settings or preview.get("round", 0) >= 2:
        preview = {"original": doc.model_dump(mode="json"), "revision": doc.revision, "round": 0, "options": settings}
    path = store.video_dir(vid) / "automatic.json"

    async def run(job, update):
        candidate = doc
        failures = {}
        store.atomic_json(path, preview)
        update(mode="automatic", status="PROCESSING", progress=0)

        async def verify():
            scenes = await imported_video.timeline(vid)
            validate_document(candidate, scenes, vid)
            if store.load_document(vid) != candidate:
                raise ValueError("Kịch bản đã đổi ở cửa sổ khác. Đã giữ phần hoàn tất; tải lại trước khi tiếp tục.")
            if candidate.edit_signature:
                from agent.services import timeline
                from agent.models.timeline import TimelineDocument
                return timeline.duration(TimelineDocument.model_validate(timeline.load(vid)['document']))
            return sum(float(s.get("duration") or 8) for s in scenes)

        async def commit(next_doc):
            nonlocal candidate
            await verify()
            if next_doc != candidate:
                next_doc = SpeechDocument.model_validate({**next_doc.model_dump(), "revision": candidate.revision + 1})
                store.atomic_json(store.video_dir(vid) / "script.json", next_doc.model_dump(mode="json"))
                candidate = next_doc
                preview["revision"] = candidate.revision
                store.atomic_json(path, preview)

        async def create_missing(progress_start, progress_end, label="Đang tạo giọng"):
            await verify()
            missing = [cue for cue in candidate.cues if str(cue.id) not in failures and not cue_status(vid, cue)["audio_url"]]
            child = {"results": {}}
            def report(**changes):
                child.update(changes)
                stage = changes.pop("stage", None)
                completed = child.get("completed", 0)
                update(**changes, stage=f"{label} · {completed}/{len(missing)} câu" if stage in {"queued", "generating"} else stage or "Đang kiểm tra giọng",
                       progress=round(progress_start + (progress_end - progress_start) * completed / max(1, len(missing))))
            if missing:
                await generate_cues(vid, missing, False, child, report)
                failures.update({cid: result for cid, result in child["results"].items() if not result.get("ok")})
            await verify()

        async def fit_audio(progress_start, progress_end):
            duration = await verify()
            update(stage="Đang căn vào khoảng trống và kiểm tra tốc độ", progress=progress_start)
            previous = candidate
            await commit(fit_measured(candidate, [cue_status(vid, cue) for cue in candidate.cues], duration))
            for before, after in zip(previous.cues, candidate.cues):
                if speech_jobs.cancel_requested(vid):
                    break
                if before.speed != after.speed:
                    update(current=str(after.id), stage="Đang chỉnh tốc độ file giọng đã có")
                    try:
                        await asyncio.to_thread(retime_cue_audio, vid, before, after)
                    except (ValueError, OSError, subprocess.SubprocessError):
                        pass  # Normal generation below retries any invalid/missing output.
            await create_missing(progress_start, progress_end)
            if not speech_jobs.cancel_requested(vid):
                await commit(fit_nearby_cues(candidate, [cue_status(vid, cue) for cue in candidate.cues]))

        await create_missing(0, 25)
        while preview["round"] < 2:
            if speech_jobs.cancel_requested(vid):
                update(cancel_requested=True)
                break
            measured = [cue_status(vid, cue) for cue in candidate.cues]
            if not any(item["status"] == "TOO_LONG" for item in measured):
                break
            start = 25 + preview["round"] * 35
            await fit_audio(start, start + 10)
            if speech_jobs.cancel_requested(vid):
                break
            if not any(cue_status(vid, cue)["status"] == "TOO_LONG" for cue in candidate.cues):
                break
            selection = {}
            for cue in candidate.cues:
                item = cue_status(vid, cue)
                if item["status"] == "TOO_LONG":
                    slot = cue.end - cue.start
                    selection[str(cue.id)] = max(1, int(min(84, slot * 22, len(cue.text) * slot / item["duration"] * .95) + 1e-9))
            if selection:
                update(current=None)
                process = subtitle_translation.runner(vid, candidate, options.provider, "original", options.synopsis,
                    options.glossary, selection=selection, cancelled=lambda: speech_jobs.cancel_requested(vid))
                def ai_progress(**changes):
                    update(stage=changes.get("stage", "AI đang rút gọn câu dài"),
                           progress=round(start + 10 + changes.get("progress", 0) / 100 * 10))
                await process(ai_progress)
                if speech_jobs.cancel_requested(vid):
                    break
                result = store.read_json(media_jobs.directory(vid) / "shortening.json")
                if not result or result.get("original") != candidate.model_dump(mode="json"):
                    raise ValueError("Bản rút gọn không còn khớp kịch bản. Chưa áp dụng kết quả.")
                await commit(SpeechDocument.model_validate(result["document"]))
                await create_missing(start + 20, start + 35, "Đang tạo lại lời AI đã rút gọn")
                if speech_jobs.cancel_requested(vid):
                    break
            preview["round"] += 1
            store.atomic_json(path, preview)
        if not speech_jobs.cancel_requested(vid) and any(cue_status(vid, cue)["status"] == "TOO_LONG" for cue in candidate.cues):
            # The last AI round produces new audio too; fit it before declaring failure.
            await fit_audio(95, 99)
        await verify()
        statuses = [cue_status(vid, cue) for cue in candidate.cues]
        update(completed=sum(item["status"] == "READY" for item in statuses) if speech_jobs.cancel_requested(vid) else len(candidate.cues),
               total=len(candidate.cues), current=None,
               results={item["id"]: {**failures.get(item["id"], {}), "ok": item["status"] == "READY"} for item in statuses},
               progress=100 if not speech_jobs.cancel_requested(vid) else job.get("progress", 0))

    return speech_jobs.start_job(vid, store.video_dir(vid) / "job.json", len(doc.cues), run)
