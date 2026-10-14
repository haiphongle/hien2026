"""Immutable imported sources, shared by the speech editor and all exports."""
import asyncio
import json
import math
import shutil

from agent.db import crud
from agent.media_limits import MAX_DURATION, MAX_UPLOAD_BYTES
from agent.services import media_jobs as jobs
from agent.services.ffmpeg_progress import processing_timeout
from agent.services.speech_store import atomic_json, read_json

MAX_BYTES = MAX_UPLOAD_BYTES
UPLOAD_TIMEOUT_SECONDS = 3 * 60 * 60
MIN_FREE_BYTES = 1024 * 1024 * 1024
_slots = asyncio.Semaphore(1)


def metadata(vid):
    return read_json(jobs.directory(vid) / "source.json")


def source_path(vid):
    return jobs.directory(vid) / "source.mp4"


async def timeline(vid):
    info = metadata(vid)
    if info:
        if info["status"] != "READY" or not source_path(vid).is_file():
            raise ValueError("Video tải lên đang được chuẩn bị hoặc bị gián đoạn. Hãy nhập lại video nếu tác vụ đã dừng.")
        # A stable virtual clip keeps imported sources out of Flow generation queues.
        return [{"id": vid, "display_order": 0, "duration": info["duration"],
                 "vertical_video_media_id": info["source_id"]}]
    return await crud.list_scenes(vid)


async def probe(path):
    raw = await jobs.process(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", "mov,matroska,webm", "-show_entries",
        "format=duration:stream=codec_type,codec_name,pix_fmt,width,height:stream_side_data=rotation",
        "-of", "json", str(path)], timeout=30)
    data = json.loads(raw)
    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    duration = float(data.get("format", {}).get("duration", 0))
    if not video or not math.isfinite(duration) or not 0 < duration <= MAX_DURATION:
        raise ValueError(f"Chọn video có hình ảnh, dài tối đa {MAX_DURATION / 60:g} phút.")
    width, height = video.get("width", 0), video.get("height", 0)
    rotation = next((s.get("rotation", 0) for s in video.get("side_data_list", []) if "rotation" in s), 0)
    if abs(rotation) % 180 == 90:
        width, height = height, width
    if not width or not height or max(width, height) > 7680:
        raise ValueError("Kích thước video không hợp lệ hoặc vượt 8K.")
    audio = next((s for s in data["streams"] if s.get("codec_type") == "audio"), None)
    # Browser-safe streams can be remuxed without encoding every frame of a long film.
    remux = (video.get("codec_name") == "h264" and video.get("pix_fmt") == "yuv420p"
             and width % 2 == height % 2 == 0 and not rotation
             and (audio is None or audio.get("codec_name") == "aac"))
    return dict(duration=duration, width=width, height=height, remux=remux,
                has_audio=any(s.get("codec_type") == "audio" for s in data["streams"]),
                orientation="VERTICAL" if height > width else "HORIZONTAL")


def prepare(vid, upload, info):
    async def prepare_file(update):
        temp = jobs.directory(vid) / "preparing.mp4"
        try:
            remux = info.get("remux", False)
            update(stage="Chuẩn bị nhanh · giữ nguyên hình và tiếng" if remux else "Chuẩn hoá video để xem và hậu kỳ", progress=1)
            def progress(line):
                if line.startswith("out_time_us="):
                    try:
                        update(progress=min(98, round(int(line.split("=", 1)[1]) / 1e6 / info["duration"] * 98)))
                    except ValueError:
                        pass
            codecs = ["-c", "copy"] if remux else ["-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k"]
            await jobs.process(["ffmpeg", "-nostdin", "-v", "error", "-y", "-protocol_whitelist", "file,pipe",
                "-format_whitelist", "mov,matroska,webm", "-i", str(upload),
                "-map", "0:v:0", "-map", "0:a:0?", *codecs,
                "-movflags", "+faststart", "-progress", "pipe:1", str(temp)],
                on_line=progress, timeout=processing_timeout(info["duration"], minimum=3600))
            measured = await probe(temp)
            if abs(measured["duration"] - info["duration"]) > .5:
                raise ValueError("Thời lượng video đã thay đổi khi chuẩn bị. Bản nguồn chưa được áp dụng.")
            temp.replace(source_path(vid))
            atomic_json(jobs.directory(vid) / "source.json", {**info, **measured, "status": "READY"})
            prefix = "vertical" if info["orientation"] == "VERTICAL" else "horizontal"
            await crud.update_video(vid, status="COMPLETED", duration=measured["duration"],
                                    **{f"{prefix}_url": f"/api/videos/{vid}/file"})
        except BaseException:
            # Keep a failed item visible, with an explicit error instead of a dead player.
            atomic_json(jobs.directory(vid) / "source.json", {**info, "status": "FAILED"})
            await crud.update_video(vid, status="FAILED")
            raise
        finally:
            upload.unlink(missing_ok=True)
            temp.unlink(missing_ok=True)
    async def run(update):
        try:
            update(stage="Chờ lượt chuẩn bị video")
            async with _slots:
                await prepare_file(update)
        finally:
            upload.unlink(missing_ok=True)
    return jobs.start(vid, "import", run)


async def render(vid, output, orientation=None, progress_callback=None):
    info = metadata(vid)
    await timeline(vid)  # readiness and source existence
    if orientation and orientation != info["orientation"]:
        raise ValueError("Video nhập giữ tỷ lệ khung hình gốc. Chọn đúng chiều video trước khi ghép.")
    output.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(shutil.copyfile, source_path(vid), output)
    if progress_callback:
        progress_callback("assembling", 1)
    return dict(ok=True, video_id=vid, orientation=info["orientation"], duration=info["duration"],
                scene_count=1, scene_durations=[{"id": vid, "duration": info["duration"]}])
