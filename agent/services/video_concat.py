"""Join Flow clips with cancellable subprocesses and a consistent MP4 time base."""
import math
import tempfile
from pathlib import Path

from agent.services import media_jobs
from agent.services.ffmpeg_progress import processing_timeout


async def media_duration(path):
    output = await media_jobs.process([
        "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path),
    ], timeout=30)
    try:
        duration = float(output.strip())
    except ValueError as exc:
        raise ValueError("Could not measure scene clip duration") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid scene clip duration")
    return duration


async def merge_videos(video_paths, output_path, progress_callback=None):
    if not video_paths:
        return False
    with tempfile.TemporaryDirectory(prefix="frameweaver-concat-") as directory:
        work = Path(directory)
        expected = 0.0
        for index, path in enumerate(video_paths):
            expected += await media_duration(path)
            await media_jobs.process([
                "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(path), "-map", "0:v:0", "-map", "0:a?",
                "-c", "copy", "-video_track_timescale", "90000", str(work / f"{index}.mp4"),
            ], timeout=120)
            if progress_callback:
                progress_callback(.6 * (index + 1) / len(video_paths))
        manifest = work / "clips.ffconcat"
        manifest.write_text("".join(f"file '{index}.mp4'\n" for index in range(len(video_paths))))

        def progress(line):
            if progress_callback and line.startswith("out_time_us="):
                try:
                    fraction = int(line.split("=", 1)[1]) / (expected * 1e6)
                    progress_callback(.6 + min(.99, max(0, fraction)) * .4)
                except ValueError:
                    pass

        await media_jobs.process([
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
            "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", "-progress", "pipe:1", str(output_path),
        ], timeout=processing_timeout(expected), on_line=progress)
        actual = await media_duration(output_path)
        if abs(actual - expected) > max(.5, len(video_paths) * .1):
            raise RuntimeError("Merged clip duration does not match the source scenes")
        if progress_callback:
            progress_callback(1.0)
        return True
