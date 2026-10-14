"""Build audio exports without replacing the last playable cut on failure."""

import asyncio
import json
import tempfile
import time
from pathlib import Path
from collections.abc import Callable

from agent.config import OUTPUT_DIR
from agent.db import crud
from agent.services.music_library import resolve_music_file
from agent.services.post_process import add_music, add_scene_narrations, add_scene_subtitles
from agent.services.tts import narration_matches_text, narration_path_for_scene
from agent.services.voice_samples import wav_duration
from agent.services.speech import prepare_export as prepare_speech_export
from agent.services.speech_store import load_document, timeline_key, video_dir
from agent.services.speech_jobs import get_job, ACTIVE
from agent.services.video_assembly import assembled_path, render_video_to_file, video_export_lock
from agent.services.imported_video import timeline
from agent.utils.slugify import slugify
from agent.services.export_cancel import export_lock, check_cancelled


class AudioMixError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


def _narration_tracks(scenes: list[dict], directory: Path) -> list[tuple[str, float, float]]:
    tracks = []
    missing = []
    offset = 0.0
    for scene in scenes:
        duration = float(scene.get("duration") or 8)
        text = (scene.get("narrator_text") or "").strip()
        if text:
            path = narration_path_for_scene(directory, scene)
            if path and path.resolve().is_relative_to(directory) and narration_matches_text(path, text):
                tracks.append((str(path), offset, duration))
            else:
                missing.append(str(scene.get("display_order", 0) + 1))
        offset += duration
    if missing:
        raise AudioMixError(409, f"Generate narration for scene(s) {', '.join(missing)} first")
    if not tracks:
        raise AudioMixError(409, "Add narration text to at least one scene and generate its audio first")
    return tracks


async def finish_audio_mix(vid: str, orientation: str | None, mix_update: dict,
                           progress_callback: Callable[[str, float], None] | None = None,
                           edit_export: dict | None = None) -> dict:
    def report(stage: str, fraction: float = 0):
        check_cancelled()
        if progress_callback:
            progress_callback(stage, fraction)

    report("waiting")
    # Use the same lock as ordinary assembly, including the entire ffmpeg export.
    async with export_lock(video_export_lock):
        report("validating")
        video = await crud.get_video(vid)
        if not video:
            raise AudioMixError(404, "Video not found")
        project = await crud.get_project(video["project_id"])
        if not project:
            raise AudioMixError(404, "Project not found")
        project_dir = (OUTPUT_DIR / slugify(project.get("name") or "unnamed_project")).resolve()
        mix_dir = project_dir / "audio_mix"
        settings_path = mix_dir / f"{vid}.json"
        try:
            settings = json.loads(settings_path.read_text()) if settings_path.is_file() else {}
        except (OSError, json.JSONDecodeError) as exc:
            raise AudioMixError(409, "Could not read saved audio settings; the previous export was kept") from exc
        if not isinstance(settings, dict):
            raise AudioMixError(409, "Invalid saved audio settings")
        settings.update(mix_update)
        editor = None
        if edit_export:
            from agent.models.editing import EditingSettings
            editor = EditingSettings.model_validate(edit_export["settings"])
            # Freeze the selected layers at submission, not when the lock becomes available.
            settings = edit_export["audio_mix"]
        narration = settings.get("narration")
        music = settings.get("music")
        if not narration and not music and not editor:
            raise AudioMixError(400, "Choose narration or music before mixing")

        # Validate every saved layer before downloading clips or running ffmpeg.
        scenes = await timeline(vid)
        scene_key = timeline_key(scenes)
        scene_texts = [s.get("narrator_text") for s in scenes]
        if editor and (edit_export["timeline"] != scene_key or edit_export["scene_texts"] != scene_texts):
            raise AudioMixError(409, "Cảnh đã thay đổi sau khi bắt đầu xuất. Hãy xuất lại.")
        speech_revision = narration.get("speech_revision") if narration else None
        captions_only = bool(narration and narration.get("captions_only"))
        if captions_only and ((speech_revision is None and not editor) or not narration.get("burn_subtitles")):
            raise AudioMixError(422, "Chọn kịch bản đã lưu để xuất phụ đề.")
        speech_doc = None
        captions = scenes
        if speech_revision is not None:
            speech_job = get_job(vid, video_dir(vid) / "job.json")
            if speech_job and speech_job["status"] in ACTIVE:
                raise AudioMixError(409, "Đợi tạo xong các câu thoại trước khi ghép video.")
            try:
                tracks, captions, speech_doc = prepare_speech_export(vid, speech_revision, scenes, captions_only=captions_only)
            except ValueError as exc:
                raise AudioMixError(409, str(exc)) from exc
        else:
            tracks = _narration_tracks(scenes, (project_dir / "tts").resolve()) if narration and not captions_only else []
        burn_subtitles = bool(narration and narration.get("burn_subtitles"))
        durations = {}
        if burn_subtitles and speech_doc is None and not captions_only:
            # Fail before downloads/encoding. OmniVoice also writes IEEE-float WAV,
            # which Python's PCM-only wave reader cannot decode.
            for scene, track in zip((s for s in scenes if (s.get("narrator_text") or "").strip()), tracks):
                duration = await asyncio.to_thread(wav_duration, track[0])
                if duration is None:
                    raise AudioMixError(422, f"Could not read narration audio for scene {scene.get('display_order', 0) + 1}; regenerate that scene")
                durations[scene["id"]] = duration
        music_path = None
        if music:
            try:
                music_path = resolve_music_file(project_dir / "music", music.get("filename", ""))
                from agent.services.media_library import audio_record
                if audio_record(project['id'], music_path).get('trashed_at'):
                    raise AudioMixError(409, 'Nhạc đang ở thùng rác. Khôi phục trước khi ghép.')
            except ValueError as exc:
                raise AudioMixError(400, str(exc)) from exc
            except FileNotFoundError as exc:
                raise AudioMixError(404, str(exc)) from exc

        output = Path(edit_export["output"]) if editor else assembled_path(vid)
        output.parent.mkdir(parents=True, exist_ok=True)
        mix_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=f".{vid}-audio-", dir=output.parent) as staging:
            work_dir = Path(staging)
            source = work_dir / "source.mp4"
            report("downloading")
            progress_options = {"progress_callback": report} if progress_callback else {}
            result = await render_video_to_file(vid, source, orientation, **progress_options)
            timing = result.get("scene_durations")
            if timing:
                if [item["id"] for item in timing] != [scene["id"] for scene in scenes]:
                    raise AudioMixError(409, "Scene order changed during export; please try again")
                scenes = [{**scene, "duration": item["duration"]} for scene, item in zip(scenes, timing)]
                if speech_doc:
                    if speech_doc.cues[-1].end > sum(item["duration"] for item in timing) + 0.04:
                        raise AudioMixError(409, "Mốc SRT vượt thời lượng clip thực tế. Hãy rút ngắn mốc kết thúc rồi ghép lại.")
                elif narration:
                    tracks = _narration_tracks(scenes, (project_dir / "tts").resolve()) if not captions_only else []
                    captions = scenes
            if narration and not captions_only:
                report("narration")
                target = work_dir / "narrated.mp4"
                ok = await asyncio.to_thread(
                    add_scene_narrations, str(source), tracks, str(target),
                    narration_volume=float(narration.get("narration_volume", 1.0)),
                    source_volume=float(narration.get("source_volume", 0.35)),
                    **({"progress_callback": lambda value: report("narration", value)} if progress_callback else {}),
                )
                if not ok or not target.is_file():
                    raise AudioMixError(502, "Could not mix narration into the assembled video")
                source = target
            elif editor:
                from agent.services.editing import source_volume
                report("narration")
                source = await asyncio.to_thread(source_volume, source, work_dir / "volume.mp4", editor.source_volume,
                                                lambda value: report("narration", value))
            if music_path:
                report("music")
                target = work_dir / "music.mp4"
                ok = await asyncio.to_thread(
                    add_music, str(source), str(music_path), str(target),
                    music_volume=float(music.get("volume", 0.25)),
                    **({"progress_callback": lambda value: report("music", value)} if progress_callback else {}),
                )
                if not ok or not target.is_file():
                    raise AudioMixError(502, "Could not mix music into the assembled video")
                source = target
            if editor:
                from agent.services.editing import transform, validate_effects
                validate_effects(vid, editor, result['duration'])
                report("layout")
                target = work_dir / "layout.mp4"
                await asyncio.to_thread(transform, source, target, editor, lambda value: report("layout", value), video_id=vid)
                source = target
            if burn_subtitles:
                report("subtitles")
                target = work_dir / "subtitled.mp4"
                ok = await asyncio.to_thread(
                    add_scene_subtitles, str(source), captions, str(target), durations,
                    **({"progress_callback": lambda value: report("subtitles", value)} if progress_callback else {}),
                    **({"caption_style": editor.captions.model_dump()} if editor else {}),
                )
                if not ok or not target.is_file():
                    raise AudioMixError(502, "Could not burn narration subtitles into the video")
                source = target

            report("saving")
            if speech_doc:
                latest = load_document(vid)
                if latest.revision != speech_revision or timeline_key(await timeline(vid)) != speech_doc.timeline:
                    raise AudioMixError(409, "Kịch bản hoặc cảnh đã thay đổi trong lúc ghép. Bản video trước được giữ nguyên.")
            if editor and (timeline_key(await timeline(vid)) != scene_key or
                           [s.get("narrator_text") for s in await timeline(vid)] != scene_texts):
                raise AudioMixError(409, "Cảnh thay đổi trong lúc xuất. Bản nguồn được giữ nguyên.")
            if editor:
                from agent.services.editing import probe
                info = await asyncio.to_thread(probe, source)
                if abs(info["duration"] - result["duration"]) > .2:
                    raise AudioMixError(502, "Thời lượng bản xuất không khớp nguồn.")
                check_cancelled()
                source.replace(output)
                return {"ok": True, "video_id": vid, "url": edit_export["url"],
                        "width": info["width"], "height": info["height"], "duration": info["duration"],
                        "revision": edit_export["revision"], "settings": editor.model_dump(mode='json'),
                        'export_id': edit_export.get('export_id'), 'bytes': output.stat().st_size}
            staged_settings = work_dir / "settings.json"
            staged_settings.write_text(json.dumps(settings, indent=2, ensure_ascii=False))
            check_cancelled()
            source.replace(output)
            staged_settings.replace(settings_path)

        selected = result["orientation"]
        prefix = "vertical" if selected == "VERTICAL" else "horizontal"
        url = f"/api/videos/{vid}/file?v={int(time.time() * 1000)}"
        await crud.update_video(
            vid, status="COMPLETED", orientation=selected,
            **{f"{prefix}_url": url}, duration=result.get("duration"),
        )
        return {"ok": True, "video_id": vid, "orientation": selected,
                "duration": result.get("duration"), "url": url, "audio_mix": settings}
