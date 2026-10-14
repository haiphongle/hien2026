import json
import logging
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from agent.models.video import Video, VideoCreate, VideoUpdate
from agent.models.enums import Orientation
from agent.sdk.persistence.sqlite_repository import SQLiteRepository
from agent.db import crud
from agent.materials import get_material
from agent.services.flow_client import get_flow_client
from agent.api.requests import BatchRequestCreate, create_batch
from agent.models.request import RequestCreate
from agent.services.video_assembly import (
    assembled_path,
    assemble_video as assemble_video_service,
)
from agent.services.audio_mix import AudioMixError, finish_audio_mix
from agent.services.audio_mix_jobs import latest_job, start_job

router = APIRouter(prefix="/videos", tags=["videos"])
logger = logging.getLogger(__name__)

_repo = SQLiteRepository()


class StoryboardScene(BaseModel):
    action: str = Field(min_length=3, max_length=1000)
    character_ids: list[str] = Field(default_factory=list, max_length=6)
    speaker_id: str | None = None
    dialogue: str = Field(default="", max_length=200)
    image_prompt: str | None = Field(default=None, min_length=3, max_length=3000)
    video_prompt: str | None = Field(default=None, min_length=3, max_length=4000)


class QuickCreateRequest(BaseModel):
    project_id: str
    idea: str = Field(min_length=3, max_length=2000)
    orientation: Orientation = "VERTICAL"
    material: str = "realistic"
    scene_count: int = Field(default=1, ge=1, le=6)
    scenes: list[StoryboardScene] | None = Field(default=None, min_length=1, max_length=24)


class SceneOrderRequest(BaseModel):
    scene_ids: list[str] = Field(min_length=1, max_length=100)


class VideoAssemblyRequest(BaseModel):
    orientation: Orientation | None = None


class MusicMixRequest(BaseModel):
    filename: str = Field(min_length=5, max_length=125)
    orientation: Orientation | None = None
    volume: float = Field(default=0.25, ge=0.0, le=2.0)


class NarrationMixRequest(BaseModel):
    orientation: Orientation | None = None
    narration_volume: float = Field(default=1.0, ge=0.0, le=2.0)
    source_volume: float = Field(default=0.35, ge=0.0, le=2.0)
    burn_subtitles: bool = False  # Older API callers only requested audio; the UI opts in explicitly.
    speech_revision: int | None = Field(default=None, ge=1)
    captions_only: bool = False


_MEDIA_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _quick_scene_prompts(idea: str, index: int, total: int) -> tuple[str, str]:
    """Make a simple visual storyboard without inventing characters or facts."""
    action = idea.strip().rstrip(". ")
    if total == 1:
        shot = "A continuous cinematic shot"
    elif index == 0:
        shot = "An opening wide shot"
    elif index == total - 1:
        shot = "A closing close-up shot"
    else:
        shot = ("A medium shot" if index % 2 else "A different camera angle")
    image_prompt = f"{shot}. Action: {action}."
    video_prompt = (
        f"0-3s: {shot}; {action}. "
        "3-6s: The action continues with natural movement. "
        "6-8s: The movement settles into a clear final frame."
    )
    return image_prompt, video_prompt


def _storyboard_prompts(action: str, names: list[str], speaker: str | None,
                        dialogue: str) -> tuple[str, str]:
    action = action.strip().rstrip(". ")
    cast = f"{', '.join(names)}. " if names else ""
    image_prompt = f"{cast}{action}."
    video_prompt = f"0-3s: {cast}{action}. "
    if speaker and dialogue:
        video_prompt += (
            f'3-6s: {speaker} says "{dialogue}". '
            "6-8s: The action settles naturally. "
            "Audio: keep character dialogue and natural ambient sounds; no background music."
        )
    else:
        video_prompt += (
            "3-6s: The action continues with natural movement. "
            "6-8s: The movement settles into a clear final frame."
        )
    return image_prompt, video_prompt


def _video_to_flat(sdk_video) -> dict:
    """Convert SDK Video domain model to flat dict matching API response shape."""
    return {
        "id": sdk_video.id,
        "project_id": sdk_video.project_id,
        "title": sdk_video.title,
        "description": sdk_video.description,
        "display_order": sdk_video.display_order,
        "status": sdk_video.status,
        "orientation": sdk_video.orientation,
        "vertical_url": sdk_video.vertical_url,
        "horizontal_url": sdk_video.horizontal_url,
        "thumbnail_url": sdk_video.thumbnail_url,
        "duration": sdk_video.duration,
        "resolution": sdk_video.resolution,
        "youtube_id": sdk_video.youtube_id,
        "privacy": sdk_video.privacy,
        "tags": sdk_video.tags,
        "created_at": sdk_video.created_at,
        "updated_at": sdk_video.updated_at,
    }


@router.post("", response_model=Video)
async def create(body: VideoCreate):
    sdk_video = await _repo.create_video(**body.model_dump(exclude_none=True))
    return _video_to_flat(sdk_video)


@router.post("/quick-create", status_code=201)
async def quick_create(body: QuickCreateRequest):
    """Create a video and all its scene image + Omni Flash jobs in one action."""
    project = await crud.get_project(body.project_id)
    if not project or project.get("status") != "ACTIVE":
        raise HTTPException(404, "Active project not found")
    material = get_material(body.material)
    if not material:
        raise HTTPException(400, "Unknown visual style")
    if not get_flow_client().connected:
        raise HTTPException(503, "Flow extension is disconnected")

    idea = body.idea.strip()
    project_chars = {c["id"]: c for c in await crud.get_project_characters(body.project_id)}
    if body.scenes:
        for index, item in enumerate(body.scenes, start=1):
            if not item.action.strip():
                raise HTTPException(400, f"Scene {index} needs an action")
            if any(value is not None and len(value.strip()) < 3 for value in (item.image_prompt, item.video_prompt)):
                raise HTTPException(400, f"Scene {index}: custom prompts must not be blank")
            if len(item.character_ids) != len(set(item.character_ids)):
                raise HTTPException(400, f"Scene {index} has duplicate characters")
            for cid in item.character_ids:
                char = project_chars.get(cid)
                if not char:
                    raise HTTPException(400, f"Scene {index} has a character outside this project")
                if not char.get("media_id") or not _MEDIA_UUID.fullmatch(char["media_id"]):
                    raise HTTPException(400, f"Scene {index}: {char['name']} needs a reference photo")
            if item.speaker_id and item.speaker_id not in item.character_ids:
                raise HTTPException(400, f"Scene {index}: speaker must be selected in the scene")
            if item.dialogue.strip() and not item.speaker_id:
                raise HTTPException(400, f"Scene {index}: choose a speaker for the dialogue")
            if len(item.dialogue.split()) > 15:
                raise HTTPException(400, f"Scene {index}: use at most 15 words for the 3-second dialogue segment")

    existing = await crud.list_videos(body.project_id)
    video = await crud.create_video(
        project_id=body.project_id,
        title=idea[:72],
        description=idea,
        display_order=max((v["display_order"] for v in existing), default=-1) + 1,
        orientation=body.orientation,
    )
    try:
        scenes = []
        total = len(body.scenes) if body.scenes else body.scene_count
        for index in range(total):
            item = body.scenes[index] if body.scenes else None
            if item:
                names = [project_chars[cid]["name"] for cid in item.character_ids]
                speaker = project_chars[item.speaker_id]["name"] if item.speaker_id else None
                prompt, video_prompt = _storyboard_prompts(item.action, names, speaker, item.dialogue.strip())
                prompt = item.image_prompt.strip() if item.image_prompt else prompt
                video_prompt = item.video_prompt.strip() if item.video_prompt else video_prompt
            else:
                names = []
                prompt, video_prompt = _quick_scene_prompts(idea, index, total)
            prefix = material.get("scene_prefix") or material["style_instruction"]
            scene = await crud.create_scene(
                video_id=video["id"],
                display_order=index,
                prompt=prompt,
                image_prompt=f"{prefix} {prompt}",
                video_prompt=video_prompt,
                character_names=names or None,
                chain_type="ROOT",
                source="user",
            )
            scenes.append(scene)

        requests = [
            RequestCreate(
                type=req_type, project_id=body.project_id, video_id=video["id"],
                scene_id=scene["id"], orientation=body.orientation,
            )
            for req_type in ("GENERATE_IMAGE", "GENERATE_VIDEO_OMNI")
            for scene in scenes
        ]
        queued = await create_batch(BatchRequestCreate(requests=requests))
        await crud.update_video(video["id"], status="PROCESSING")
    except Exception:
        await crud.delete_video(video["id"])
        raise

    return {
        "project_id": body.project_id,
        "video_id": video["id"],
        "scene_count": len(scenes),
        "request_count": len(queued),
    }


@router.get("", response_model=list[Video])
async def list_by_project(project_id: str):
    videos = await _repo.list_videos(project_id)
    return [_video_to_flat(v) for v in videos]


@router.get("/{vid}/file")
async def get_assembled_file(vid: str):
    video = await crud.get_video(vid)
    if not video:
        raise HTTPException(404, "Video not found")
    path = assembled_path(vid)
    if not path.is_file():
        from agent.services.imported_video import source_path
        path = source_path(vid)
    if not path.is_file():
        raise HTTPException(404, "Assembled video is not ready")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"{vid}.mp4",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


@router.put("/{vid}/scenes/order")
async def set_scene_order(vid: str, body: SceneOrderRequest):
    """Persist a drag-and-drop scene order and mark the assembled cut as stale."""
    video = await crud.get_video(vid)
    if not video:
        raise HTTPException(404, "Video not found")
    if len(body.scene_ids) != len(set(body.scene_ids)):
        raise HTTPException(400, "Scene order contains duplicate ids")
    if not await crud.reorder_scenes(vid, body.scene_ids):
        raise HTTPException(400, "Provide every scene in this video exactly once")
    await crud.update_video(vid, status="PROCESSING")
    return {"ok": True, "scene_ids": body.scene_ids}


@router.post("/{vid}/assemble")
async def assemble_video(vid: str, body: VideoAssemblyRequest):
    """Rebuild the final video using the scene order currently saved in the project."""
    video = await crud.get_video(vid)
    if not video:
        raise HTTPException(404, "Video not found")
    try:
        return await assemble_video_service(vid, body.orientation)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Video assembly failed: {exc}") from exc


async def _mix_audio(vid: str, orientation: str | None, update: dict):
    try:
        return await finish_audio_mix(vid, orientation, update)
    except AudioMixError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception("Audio mix failed for video %s", vid)
        raise HTTPException(502, f"Audio mix failed: {exc}") from exc


@router.post("/{vid}/mix-music")
async def mix_video_music(vid: str, body: MusicMixRequest):
    return await _mix_audio(vid, body.orientation, {
        "music": {"filename": body.filename, "volume": body.volume},
    })


@router.post("/{vid}/mix-narration")
async def mix_video_narration(vid: str, body: NarrationMixRequest):
    return await _mix_audio(vid, body.orientation, {
        "narration": {
            "narration_volume": body.narration_volume,
            "source_volume": body.source_volume,
            "burn_subtitles": body.burn_subtitles,
            "speech_revision": body.speech_revision,
            "captions_only": body.captions_only,
        },
    })


@router.get("/{vid}/audio-mix-job")
async def get_audio_mix_job(vid: str):
    if not await crud.get_video(vid):
        raise HTTPException(404, "Video not found")
    return latest_job(vid)


async def _queue_audio_mix(vid: str, kind: str, orientation: str | None, update: dict):
    if not await crud.get_video(vid):
        raise HTTPException(404, "Video not found")
    try:
        return start_job(vid, kind, orientation, update)
    except AudioMixError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.post("/{vid}/mix-music/jobs", status_code=202)
async def queue_music_mix(vid: str, body: MusicMixRequest):
    return await _queue_audio_mix(vid, "music", body.orientation, {
        "music": {"filename": body.filename, "volume": body.volume},
    })


@router.post("/{vid}/mix-narration/jobs", status_code=202)
async def queue_narration_mix(vid: str, body: NarrationMixRequest):
    return await _queue_audio_mix(vid, "speech" if body.speech_revision is not None else "narration", body.orientation, {
        "narration": {"narration_volume": body.narration_volume,
                      "source_volume": body.source_volume, "burn_subtitles": body.burn_subtitles,
                      "speech_revision": body.speech_revision, "captions_only": body.captions_only},
    })


@router.get("/{vid}", response_model=Video)
async def get(vid: str):
    sdk_video = await _repo.get_video(vid)
    if not sdk_video:
        raise HTTPException(404, "Video not found")
    return _video_to_flat(sdk_video)


@router.patch("/{vid}", response_model=Video)
async def update(vid: str, body: VideoUpdate):
    if not await crud.get_video(vid):
        raise HTTPException(404, 'Video not found')
    row = await _repo.update("video", vid, **body.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(404, "Video not found")
    sdk_video = _repo._row_to_video(row)
    return _video_to_flat(sdk_video)


@router.delete("/{vid}")
async def delete(vid: str):
    row = await crud.get_video(vid)
    if not row:
        raise HTTPException(404, "Video not found")
    from agent.services.media_library import change
    await change(row['project_id'], vid, 'trash')
    return {"ok": True}
