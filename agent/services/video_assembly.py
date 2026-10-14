"""Finish quick-created videos after every Omni scene clip is ready."""

from __future__ import annotations

import asyncio
import logging
import math
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse, unquote

import aiohttp

from agent.config import OUTPUT_DIR
from agent.db import crud
from agent.services.video_concat import merge_videos, media_duration as _media_duration

logger = logging.getLogger(__name__)
# Assembly and audio mixing must hold this lock until the finished cut is published.
video_export_lock = asyncio.Lock()


def assembled_path(video_id: str) -> Path:
    return OUTPUT_DIR / "quick_videos" / f"{video_id}.mp4"


async def _download_clip(url: str, target: Path) -> None:
    from agent.services.export_cancel import cancellable_download, check_cancelled
    check_cancelled()
    if url.startswith("file://"):
        source = Path(unquote(urlparse(url).path))
        await asyncio.to_thread(shutil.copyfile, source, target)
        return
    async def download():
        timeout = aiohttp.ClientTimeout(total=180)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                with target.open("wb") as output:
                    async for chunk in response.content.iter_chunked(1024 * 1024):
                        check_cancelled()
                        output.write(chunk)
    await cancellable_download(download())


def _url_expires_soon(url: str) -> bool:
    try:
        expires = int(parse_qs(urlparse(url).query).get("Expires", [""])[0])
    except (TypeError, ValueError):
        return False
    return expires > 0 and expires <= time.time() + 5 * 60


async def _assemble_video_locked(
    video_id: str, orientation: str, *, output_path: Path | None = None, publish: bool = True,
    progress_callback: Callable[[str, float], None] | None = None,
) -> dict:
    video = await crud.get_video(video_id)
    if not video:
        raise ValueError("Video not found")
    scenes = await crud.list_scenes(video_id)
    if not scenes:
        raise ValueError("Add at least one scene before assembling the video")

    prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
    missing = [scene for scene in scenes if (
        scene.get(f"{prefix}_video_status") != "COMPLETED"
        or not scene.get(f"{prefix}_video_url")
    )]
    if missing:
        numbers = ", ".join(str(scene["display_order"] + 1) for scene in missing[:8])
        raise ValueError(f"Finish the video clips for scene(s) {numbers} before assembling")

    if any(_url_expires_soon(scene[f"{prefix}_video_url"]) for scene in scenes):
        from agent.services.flow_client import get_flow_client
        client = get_flow_client()
        if not client.connected:
            raise ValueError("Reconnect the Flow extension to refresh expired clip links")
        await client.refresh_project_urls(video["project_id"])
        scenes = await crud.list_scenes(video_id)
        if any(
            not scene.get(f"{prefix}_video_url") or _url_expires_soon(scene[f"{prefix}_video_url"])
            for scene in scenes
        ):
            raise RuntimeError("Flow could not refresh every clip URL")

    clip_urls = [scene[f"{prefix}_video_url"] for scene in scenes]
    duration = sum(float(scene.get("duration") or 8) for scene in scenes)
    scene_durations = None
    if len(scenes) == 1 and output_path is None:
        url = clip_urls[0]
    else:
        output = output_path or assembled_path(video_id)
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary_output = output.with_name(f".{video_id}.{uuid.uuid4().hex}.assembling.mp4")
        try:
            with tempfile.TemporaryDirectory(prefix=f"{video_id}-", dir=output.parent) as temp_dir:
                clip_paths = [Path(temp_dir) / f"scene-{index:04d}.mp4" for index in range(len(scenes))]
                for index, (url, path) in enumerate(zip(clip_urls, clip_paths)):
                    if progress_callback:
                        progress_callback("downloading", index / len(clip_paths))
                    await _download_clip(url, path)
                    if progress_callback:
                        progress_callback("downloading", (index + 1) / len(clip_paths))
                if not publish:
                    # Audio and captions need actual clip boundaries, not the DB's
                    # default eight seconds (uploaded/trimmed clips can differ).
                    scene_durations = []
                    for scene, path in zip(scenes, clip_paths):
                        actual = await _media_duration(str(path))
                        if actual is None or not math.isfinite(actual) or actual <= 0:
                            raise RuntimeError("Could not measure a scene clip for audio alignment")
                        scene_durations.append({"id": scene["id"], "duration": actual})
                    duration = sum(item["duration"] for item in scene_durations)
                if progress_callback:
                    progress_callback("assembling", 0)
                if len(clip_paths) == 1:
                    clip_paths[0].replace(temporary_output)
                else:
                    merged = await merge_videos(
                        [str(path) for path in clip_paths], str(temporary_output),
                        **({"progress_callback": lambda value: progress_callback("assembling", value)} if progress_callback else {}),
                    )
                    if not merged:
                        raise RuntimeError("ffmpeg could not join the scene clips")
                if progress_callback:
                    progress_callback("assembling", 1)
            temporary_output.replace(output)
        finally:
            temporary_output.unlink(missing_ok=True)
        # Change the URL each time so an open browser player fetches the new cut.
        url = f"/api/videos/{video_id}/file?v={int(time.time() * 1000)}"

    if publish:
        # A thumbnail can be selected while ffmpeg is working. Keep that choice.
        current = await crud.get_video(video_id)
        thumbnail = {} if current and current.get("thumbnail_url") else {
            "thumbnail_url": scenes[0].get(f"{prefix}_image_url"),
        }
        await crud.update_video(
            video_id, status="COMPLETED", orientation=orientation,
            **{f"{prefix}_url": url}, **thumbnail, duration=duration,
        )
    logger.info("Assembled video %s with %d scene(s) in saved order", video_id[:8], len(scenes))
    return {"ok": True, "video_id": video_id, "orientation": orientation,
            "scene_count": len(scenes), "duration": duration, "url": url,
            "scene_durations": scene_durations}


async def _resolve_orientation(video_id: str, orientation: str | None) -> str:
    video = await crud.get_video(video_id)
    if not video:
        raise ValueError("Video not found")
    selected = orientation or video.get("orientation")
    if selected in ("VERTICAL", "HORIZONTAL"):
        return selected
    scenes = await crud.list_scenes(video_id)
    vertical_ready = bool(scenes) and all(
        scene.get("vertical_video_status") == "COMPLETED" and scene.get("vertical_video_url")
        for scene in scenes
    )
    return "VERTICAL" if vertical_ready else "HORIZONTAL"


async def assemble_video(video_id: str, orientation: str | None = None) -> dict:
    """Download clips in saved scene order and rebuild the final video."""
    async with video_export_lock:
        selected = await _resolve_orientation(video_id, orientation)
        return await _assemble_video_locked(video_id, selected)


async def render_video_to_file(video_id: str, output: Path, orientation: str | None = None,
                               progress_callback: Callable[[str, float], None] | None = None) -> dict:
    """Build an unpublished source cut. Caller holds video_export_lock until publication.

    Audio post-processing uses a temporary output so a failed mix cannot replace
    the playable video or mark an unfinished export as completed.
    """
    selected = await _resolve_orientation(video_id, orientation)
    from agent.services import imported_video
    if imported_video.metadata(video_id):
        return await imported_video.render(video_id, output, orientation, progress_callback)
    return await _assemble_video_locked(video_id, selected, output_path=output, publish=False,
                                        progress_callback=progress_callback)


async def maybe_finish_quick_video(video_id: str, orientation: str) -> None:
    """Complete quick-created videos once all Omni scene clips are ready."""
    async with video_export_lock:
        video = await crud.get_video(video_id)
        if not video or video["status"] == "COMPLETED":
            return
        scenes = await crud.list_scenes(video_id)
        if not scenes:
            return
        prefix = "vertical" if orientation == "VERTICAL" else "horizontal"
        if any(scene[f"{prefix}_video_status"] != "COMPLETED" or not scene[f"{prefix}_video_url"] for scene in scenes):
            return
        await _assemble_video_locked(video_id, orientation)
