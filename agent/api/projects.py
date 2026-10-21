import base64
import binascii
import json
import logging
import re
from datetime import datetime, timezone
from typing import Literal

import aiohttp
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from agent.config import BASE_DIR, OUTPUT_DIR
from agent.db import crud
from agent.models.project import Project, ProjectCreate, ProjectUpdate
from agent.models.character import Character
from agent.sdk.persistence.sqlite_repository import SQLiteRepository
from agent.services.flow_client import get_flow_client
from agent.utils.slugify import slugify

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projects", tags=["projects"])

_IMAGE_LIMIT = 10 * 1024 * 1024
_REFERENCE_DIR = OUTPUT_DIR / "character_refs"
_MEDIA_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class CharacterPhotoUpload(BaseModel):
    provider: Literal["flow", "muse2api"] = "flow"
    name: str = Field(min_length=1, max_length=100)
    image_base64: str
    voice_description: str = Field(default="", max_length=300)


class CharacterPhotoURL(BaseModel):
    provider: Literal["flow", "muse2api"] = "flow"
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=4096)


class CharacterReferenceGenerate(BaseModel):
    """Description used to generate a reusable portrait reference in Flow."""

    name: str = Field(min_length=1, max_length=100)
    image_prompt: str = Field(min_length=3, max_length=1000)
    description: str = Field(default="", max_length=500)
    voice_description: str = Field(default="", max_length=300)


class CharacterReferenceGenerateResponse(BaseModel):
    character: Character
    request_id: str


def _image_type(content: bytes) -> tuple[str, str] | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", ".png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", ".jpg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return None


COMPOSITION_GUIDELINES = {
    "character": (
        "COMPOSITION: Full body shot from head to toe, standing upright and straight (not tilted or leaning). "
        "Centered in frame with balanced composition. Front-facing view, looking directly at camera. "
        "Neutral simple background that doesn't distract from the subject. "
        "Proper proportions and anatomy. Character perfectly vertical, not skewed or rotated."
    ),
    "location": (
        "COMPOSITION: Establishing shot showing the full environment. "
        "Balanced level composition with straight horizon. Clear focal point. "
        "Atmospheric and richly detailed. Show depth and spatial layout."
    ),
    "creature": (
        "COMPOSITION: Full body shot showing the creature's complete form. "
        "Emphasize natural stance (quadrupedal on all fours, bipedal upright, etc.). "
        "Centered with clear view of distinctive features. Neutral background. "
        "Proper scale and proportions relative to body structure."
    ),
    "visual_asset": (
        "COMPOSITION: Clear detailed view showing the asset's complete form. "
        "Appropriate angle to showcase distinctive features and functional elements. "
        "Centered with proper scale reference. Neutral background. "
        "Show key details, materials, and surface textures."
    ),
    "generic_troop": (
        "COMPOSITION: Military/tactical pose showing readiness. "
        "Full or three-quarter body view. Centered composition. "
        "Neutral background. Proper perspective and proportions."
    ),
    "faction": (
        "COMPOSITION: Military/tactical pose showing readiness. "
        "Full or three-quarter body view. Centered composition. "
        "Neutral background. Proper perspective and proportions."
    ),
}


_STYLE_COMPAT_MAP = {
    "3d": "3d_pixar",
    "3D": "3d_pixar",
    "photorealistic": "realistic",
}


def _resolve_material_id(value: str) -> str:
    """Map legacy style strings to material IDs. Returns value unchanged if no mapping."""
    return _STYLE_COMPAT_MAP.get(value, value)


def _build_character_profile(char_name: str, char_desc: str | None, story: str | None,
                              entity_type: str = "character", material_id: str = "3d_pixar") -> dict:
    """Build a rich profile (description + image_prompt) for any reference entity.

    The image_prompt generates a reference image used as mediaId for all
    scene generations. Visual appearance is defined HERE, not in scene prompts.
    Scene prompts should only describe actions/environment/composition.

    story may be None — in that case the description omits story context and
    the image_prompt uses a simpler prefix.
    """
    from agent.materials import get_material
    material = get_material(material_id)
    if not material:
        raise ValueError(f"Unknown material: {material_id}")

    base_desc = char_desc or char_name
    composition = COMPOSITION_GUIDELINES.get(entity_type, COMPOSITION_GUIDELINES["character"])

    if story:
        description = f"{char_name}: {base_desc}. Story context: {story}"
        image_prefix = f"Single reference image of {base_desc}. "
        single_image_note = "ONE single image only, NOT a multi-panel grid or multiple views. "
    else:
        description = base_desc
        image_prefix = f"Reference image of {base_desc}. "
        single_image_note = ""

    style_instruction = material["style_instruction"]
    if material.get("negative_prompt"):
        style_instruction += f" {material['negative_prompt']}"
    lighting = material.get("lighting", "Studio lighting, highly detailed")

    image_prompt = (
        f"{image_prefix}"
        f"{style_instruction} "
        f"{composition} "
        f"{single_image_note}"
        f"{lighting}"
    )

    return {"description": description, "image_prompt": image_prompt}


async def _detect_user_tier(client) -> str:
    """Auto-detect user paygate tier from Flow credits API."""
    try:
        result = await client.get_credits()
        data = result.get("data", result)
        tier = data.get("userPaygateTier", "PAYGATE_TIER_ONE")
        logger.info("Auto-detected user tier: %s", tier)
        return tier
    except Exception as e:
        logger.warning("Failed to detect tier, defaulting to TIER_ONE: %s", e)
        return "PAYGATE_TIER_ONE"


def _read_flow_project_id(flow_result: dict) -> str:
    """Pull the project uuid out of whichever transport answered.

    The batch path answers `{"projectId": …}`; the legacy tRPC path buries it
    under result/data/json/result.
    """
    data = flow_result.get("data") or {}
    if isinstance(data, dict):
        if isinstance(data.get("projectId"), str):
            return data["projectId"]
        try:
            return data["result"]["data"]["json"]["result"]["projectId"]
        except (KeyError, TypeError):
            pass
    logger.error("Unexpected Flow response: %s", flow_result)
    raise HTTPException(502, "Could not read a Flow project id from the response")


def _get_repo() -> SQLiteRepository:
    return SQLiteRepository()


@router.post("", response_model=Project)
async def create(body: ProjectCreate):
    from agent.materials import get_material

    # Step 1: Create project on Google Flow to get the real projectId
    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected — cannot create project on Google Flow")

    # Resolve material (support legacy style field + material field)
    material_id = _resolve_material_id(body.material)
    material = get_material(material_id)
    if not material:
        raise HTTPException(400, f"Unknown material: '{material_id}'. Use GET /api/materials to list available materials.")

    # Validate characters before any API calls to avoid orphan projects
    characters_input_raw = body.model_dump(exclude_none=True).get("characters")
    if characters_input_raw:
        slugs = [slugify(c["name"]) for c in characters_input_raw]
        if len(slugs) != len(set(slugs)):
            dupes = [s for s in slugs if slugs.count(s) > 1]
            raise HTTPException(400, f"Duplicate character slugs: {list(set(dupes))}")

    detected_tier = await _detect_user_tier(client)

    # Flow no longer creates projects for us — the uuid comes from the request
    # or from FLOW_PROJECT_ID.
    flow_project_id = client.flow_project_id(body.flow_project_id)
    if flow_project_id:
        logger.info("Flow project reused: %s", flow_project_id)
    else:
        flow_result = await client.create_project(body.name, body.tool_name)
        if flow_result.get("error"):
            raise HTTPException(502, f"Flow API error: {flow_result['error']}")
        flow_project_id = _read_flow_project_id(flow_result)
        logger.info("Flow project created: %s", flow_project_id)

    repo = _get_repo()

    # Step 2: Create local project with the Flow-assigned ID and detected tier
    create_data = body.model_dump(exclude_none=True)
    create_data.pop("tool_name", None)
    create_data.pop("flow_project_id", None)
    create_data.pop("style", None)
    characters_input = create_data.pop("characters", None)

    project = await repo.create_project(
        id=flow_project_id,
        name=create_data["name"],
        description=create_data.get("description"),
        story=create_data.get("story"),
        language=create_data.get("language", "en"),
        user_paygate_tier=detected_tier,
        material=material_id,
        allow_music=create_data.get("allow_music", False),
        allow_voice=create_data.get("allow_voice", False),
    )

    # Step 3: Create reference entities (characters, locations, assets) with profiles
    if characters_input:
        for char_input in characters_input:
            etype = char_input.get("entity_type", "character")
            profile = _build_character_profile(
                char_input["name"],
                char_input.get("description"),
                body.story,
                entity_type=etype,
                material_id=material_id,
            )
            description = profile["description"]
            image_prompt = profile["image_prompt"]
            char = await repo.create_character(
                name=char_input["name"],
                slug=slugify(char_input["name"]),
                entity_type=etype,
                description=description,
                image_prompt=image_prompt,
                voice_description=char_input.get("voice_description"),
            )
            await repo.link_character_to_project(flow_project_id, char.id)
            logger.info("%s '%s' created and linked: %s", etype, char_input["name"], char.id)

    return project


@router.get("", response_model=list[Project])
async def list_all(status: str = None):
    repo = _get_repo()
    rows = await repo.list("project", **({} if status is None else {"status": status}))
    return [repo._row_to_project(r) for r in rows]


@router.get("/{pid}", response_model=Project)
async def get(pid: str):
    repo = _get_repo()
    p = await repo.get_project(pid)
    if not p:
        raise HTTPException(404, "Project not found")
    return p


@router.patch("/{pid}", response_model=Project)
async def update(pid: str, body: ProjectUpdate):
    repo = _get_repo()
    row = await repo.update("project", pid, **body.model_dump(exclude_unset=True))
    if not row:
        raise HTTPException(404, "Project not found")
    return repo._row_to_project(row)


@router.delete("/{pid}")
async def delete(pid: str, name: str = ''):
    from agent.services import library_deletion
    return await library_deletion.delete_project(pid, name)


@router.get('/{pid}/deletion-info')
async def deletion_info(pid: str):
    from agent.services import library_deletion
    return await library_deletion.project_info(pid)


@router.post("/{pid}/characters/upload", response_model=Character)
async def upload_character_photo(pid: str, body: CharacterPhotoUpload):
    """Upload a browser-selected portrait and link it as a reusable Flow reference."""
    repo = _get_repo()
    if not await repo.get_project(pid):
        raise HTTPException(404, "Project not found")
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Character name is required")
    slug = slugify(name)
    existing = await repo.get_project_characters(pid)
    if any(c.slug == slug for c in existing):
        raise HTTPException(409, "A character with this name already exists in the project")
    if len(body.image_base64) > (_IMAGE_LIMIT * 4 // 3 + 8):
        raise HTTPException(413, "Image must be 10 MB or smaller")
    try:
        content = base64.b64decode(body.image_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(400, "Invalid image data") from exc
    if not content or len(content) > _IMAGE_LIMIT:
        raise HTTPException(413, "Image must be 10 MB or smaller")
    detected = _image_type(content)
    if not detected:
        raise HTTPException(400, "Use a PNG, JPEG, or WebP image")
    mime, extension = detected
    target = None
    if body.provider == 'muse2api':
        from agent.services.muse_media import ROOT, save_image
        from uuid import uuid4
        media_id = str(uuid4())
        ROOT.mkdir(parents=True, exist_ok=True)
        target = ROOT / f'{media_id}.png'
        try:
            save_image(content, target)
        except ValueError as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(422, str(exc)) from exc
    else:
        client = get_flow_client()
        if not client.connected:
            raise HTTPException(503, "Flow extension is disconnected")
        result = await client.upload_image(
            body.image_base64, mime_type=mime, project_id=pid,
            file_name=f"{slug}{extension}",
        )
        media_id = result.get("_mediaId")
        if result.get("error") or not isinstance(media_id, str) or not _MEDIA_UUID.fullmatch(media_id):
            raise HTTPException(502, f"Flow image upload failed: {result.get('error', 'no valid media ID')}")

    character = None
    try:
        character = await repo.create_character(
            name=name, slug=slug, entity_type="character", media_id=media_id,
            voice_description=body.voice_description.strip() or None,
        )
        _REFERENCE_DIR.mkdir(parents=True, exist_ok=True)
        (_REFERENCE_DIR / f"{character.id}{extension}").write_bytes(content)
        await repo.update("character", character.id,
                          reference_image_url=f"/api/characters/{character.id}/image")
        if not await repo.link_character_to_project(pid, character.id):
            raise RuntimeError("Could not link character to project")
    except Exception:
        if character:
            await repo.delete_character(character.id)
            (_REFERENCE_DIR / f"{character.id}{extension}").unlink(missing_ok=True)
        if target:
            target.unlink(missing_ok=True)
        raise
    return await repo.get_character(character.id)


@router.post("/{pid}/characters/import-url", response_model=Character)
async def import_character_url(pid: str, body: CharacterPhotoURL):
    from agent.services.reference_images import download
    if not await _get_repo().get_project(pid):
        raise HTTPException(404, 'Project not found')
    if body.provider == 'flow' and not get_flow_client().connected:
        raise HTTPException(503, 'Flow extension is disconnected')
    try:
        content = await download(body.url.strip())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await upload_character_photo(pid, CharacterPhotoUpload(name=body.name, image_base64=base64.b64encode(content).decode(), provider=body.provider))


@router.post("/{pid}/characters/generate", response_model=CharacterReferenceGenerateResponse)
async def generate_character_reference(pid: str, body: CharacterReferenceGenerate):
    """Create a character and queue a portrait reference image from a text description."""
    repo = _get_repo()
    project = await repo.get_project(pid)
    if not project:
        raise HTTPException(404, "Project not found")

    name = body.name.strip()
    image_prompt = body.image_prompt.strip()
    if not name or not image_prompt:
        raise HTTPException(400, "Character name and image description are required")
    slug = slugify(name)
    existing = await repo.get_project_characters(pid)
    if any(c.slug == slug for c in existing):
        raise HTTPException(409, "A character with this name already exists in the project")

    material_id = getattr(project, "material", None) or "realistic"
    # Keep appearance in the reusable entity profile. Scene prompts remain action-only.
    profile = _build_character_profile(
        name,
        body.description.strip() or image_prompt,
        None,
        entity_type="character",
        material_id=material_id,
    )
    character = await repo.create_character(
        name=name,
        slug=slug,
        entity_type="character",
        description=profile["description"],
        image_prompt=f"{profile['image_prompt']} Additional direction: {image_prompt}",
        voice_description=body.voice_description.strip() or None,
    )
    if not await repo.link_character_to_project(pid, character.id):
        await repo.delete_character(character.id)
        raise HTTPException(500, "Could not link character to project")
    try:
        request = await crud.create_request(
            req_type="GENERATE_CHARACTER_IMAGE",
            character_id=character.id,
            project_id=pid,
        )
    except Exception:
        await repo.unlink_character_from_project(pid, character.id)
        await repo.delete_character(character.id)
        raise
    return CharacterReferenceGenerateResponse(
        # The repository returns the SDK Character model; the API response
        # uses the FastAPI model from agent.models.character.
        character=(await repo.get_character(character.id)).model_dump(),
        request_id=request["id"],
    )


@router.post("/{pid}/characters/refresh-images")
async def refresh_character_images(pid: str):
    return await _refresh_character_images(pid)


@router.post("/{pid}/characters/{cid}/refresh-image")
async def refresh_character_image(pid: str, cid: str):
    return await _refresh_character_images(pid, cid)


async def _refresh_character_images(pid: str, cid: str | None = None):
    """Re-sign existing Flow references; never create a new image."""
    project = await crud.get_project(pid)
    if not project:
        raise HTTPException(404, "Project not found")
    try:
        result = await get_flow_client().refresh_project_character_images(pid, cid)
    except ConnectionError as exc:
        raise HTTPException(503, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    if result["failed"] and not result["refreshed"]:
        raise HTTPException(502, "; ".join(result["errors"]))
    result["characters"] = await crud.get_project_characters(pid)
    return result


@router.post("/{pid}/characters/{cid}")
async def link_character(pid: str, cid: str):
    repo = _get_repo()
    if not await repo.link_character_to_project(pid, cid):
        raise HTTPException(400, "Failed to link character")
    return {"ok": True}


@router.delete("/{pid}/characters/{cid}")
async def unlink_character(pid: str, cid: str):
    from agent.services import library_deletion
    return await library_deletion.delete_character(cid, pid, unlink=True)


@router.get("/{pid}/characters", response_model=list[Character])
async def get_characters(pid: str):
    repo = _get_repo()
    return await repo.get_project_characters(pid)


@router.get("/{pid}/output-dir")
async def get_output_dir(pid: str):
    """Get or create project output directory with meta.json."""
    repo = _get_repo()
    project = await repo.get_project(pid)
    if not project:
        raise HTTPException(404, "Project not found")

    project_name = project.name if hasattr(project, "name") else project["name"]
    slug = slugify(project_name)
    output_dir = BASE_DIR / "output" / slug

    for subdir in ["scenes", "4k", "tts", "narrated", "trimmed", "norm", "thumbnails", "subclips", "review"]:
        (output_dir / subdir).mkdir(parents=True, exist_ok=True)

    videos = await repo.list_videos(pid)
    video = videos[0] if videos else None
    video_id = video.id if video else None
    scene_count = 0
    if video_id:
        scenes = await repo.list_scenes(video_id)
        scene_count = len(scenes) if scenes else 0

    # Orientation lives on the video table, not project
    video_orientation = (getattr(video, "orientation", None) if video else None) or "VERTICAL"

    now = datetime.now(timezone.utc).isoformat()
    meta = {
        "project_id": pid,
        "project_name": project_name,
        "slug": slug,
        "video_id": video_id,
        "orientation": video_orientation,
        "material": getattr(project, "material", None) or (project.get("material") if isinstance(project, dict) else None) or "",
        "scene_count": scene_count,
        "created_at": now,
    }
    meta_path = output_dir / "meta.json"
    if meta_path.exists():
        existing = json.loads(meta_path.read_text())
        meta["created_at"] = existing.get("created_at", now)
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))

    return {"slug": slug, "path": f"output/{slug}", "meta": meta}


_ASPECT_RATIO_MAP = {
    "LANDSCAPE": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "PORTRAIT": "IMAGE_ASPECT_RATIO_PORTRAIT",
    "SQUARE": "IMAGE_ASPECT_RATIO_SQUARE",
}


class ThumbnailRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=2000)
    character_names: list[str] = Field(default_factory=list, max_length=30)
    aspect_ratio: str = "LANDSCAPE"
    output_filename: str = Field(default="thumbnail.png", pattern=r"^[A-Za-z0-9_-]{1,100}\.(?:png|jpg|jpeg|webp)$")


class ThumbnailResponse(BaseModel):
    success: bool
    media_id: str | None = None
    image_url: str | None = None
    output_path: str | None = None
    file_url: str | None = None
    prompt: str | None = None
    error: str | None = None


class SavedThumbnail(BaseModel):
    filename: str
    file_url: str
    created_at: str


@router.post("/{pid}/generate-thumbnail", response_model=ThumbnailResponse)
async def generate_thumbnail(pid: str, body: ThumbnailRequest):
    """Generate a thumbnail image for a project via Google Flow API (synchronous, no queue)."""
    import logging
    logger = logging.getLogger(__name__)
    from agent.materials import get_material
    from agent.sdk.services.result_handler import parse_result

    logger.info("generate_thumbnail: started for project %s", pid)

    client = get_flow_client()
    if not client.connected:
        raise HTTPException(503, "Extension not connected")

    repo = _get_repo()
    project = await repo.get_project(pid)
    if not project:
        raise HTTPException(404, "Project not found")

    # Build full prompt: prepend material scene_prefix for style consistency
    material_id = getattr(project, "material", None) or "realistic"
    material = get_material(material_id)
    scene_prefix = material["scene_prefix"] if material and material.get("scene_prefix") else ""
    full_prompt = f"{scene_prefix} {body.prompt}".strip() if scene_prefix else body.prompt

    # Resolve character reference media_ids (error if any named entity is missing media_id)
    character_media_ids = None
    if body.character_names:
        entities = await repo.get_project_characters(pid)
        valid_ids = []
        missing = []
        for entity in entities:
            name = entity["name"] if isinstance(entity, dict) else entity.name
            mid = entity.get("media_id") if isinstance(entity, dict) else getattr(entity, "media_id", None)
            char_slug = (entity.get("slug") if isinstance(entity, dict) else getattr(entity, "slug", None)) or ""
            if not ((char_slug and char_slug in body.character_names) or (name and name in body.character_names)):
                continue
            if mid:
                valid_ids.append(mid)
            else:
                missing.append(name)
        if missing:
            raise HTTPException(400, f"Missing reference images for: {', '.join(missing)}. Generate ref images first.")
        character_media_ids = valid_ids if valid_ids else None

    aspect_ratio = _ASPECT_RATIO_MAP.get(body.aspect_ratio.upper(), "IMAGE_ASPECT_RATIO_LANDSCAPE")
    tier = getattr(project, "user_paygate_tier", "PAYGATE_TIER_TWO") or "PAYGATE_TIER_TWO"

    logger.info("generate_thumbnail: calling generate_images prompt=%s refs=%s", full_prompt[:60], character_media_ids)
    raw = await client.generate_images(
        prompt=full_prompt,
        project_id=pid,
        aspect_ratio=aspect_ratio,
        user_paygate_tier=tier,
        character_media_ids=character_media_ids,
    )
    logger.info("generate_thumbnail: generate_images returned, error=%s", raw.get("error") if isinstance(raw, dict) else "n/a")

    gen_result = parse_result(raw, "GENERATE_IMAGE")
    if not gen_result.success:
        raise HTTPException(502, gen_result.error or "Image generation failed")

    # Download and save to output/{project_name}/thumbnails/{filename}
    project_name = slugify(getattr(project, "name", "project"))
    out_dir = OUTPUT_DIR / project_name / "thumbnails"
    out_dir.mkdir(parents=True, exist_ok=True)
    output_path = out_dir / body.output_filename

    if gen_result.url and gen_result.url.startswith("http"):
        try:
            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(gen_result.url) as resp:
                    if resp.status == 200:
                        output_path.write_bytes(await resp.read())
                    else:
                        raise HTTPException(502, f"Failed to download image: HTTP {resp.status}")
        except aiohttp.ClientError as e:
            raise HTTPException(502, f"Failed to download image: {e}") from e

    if not output_path.is_file():
        raise HTTPException(502, "Flow generated an image but it could not be saved locally")

    return ThumbnailResponse(
        success=True,
        media_id=gen_result.media_id,
        image_url=gen_result.url,
        output_path=str(output_path),
        file_url=f"/api/projects/{pid}/thumbnails/{body.output_filename}",
        prompt=full_prompt,
    )


@router.get("/{pid}/thumbnails", response_model=list[SavedThumbnail])
async def list_project_thumbnails(pid: str):
    """List locally saved thumbnails so the dashboard can restore them after reload."""
    repo = _get_repo()
    project = await repo.get_project(pid)
    if not project:
        raise HTTPException(404, "Project not found")
    name = project.name if hasattr(project, "name") else project["name"]
    directory = (OUTPUT_DIR / slugify(name) / "thumbnails").resolve()
    if not directory.is_relative_to(OUTPUT_DIR.resolve()) or not directory.is_dir():
        return []

    thumbnails = []
    for path in directory.iterdir():
        if path.is_symlink() or not path.is_file() or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}\.(?:png|jpg|jpeg|webp)", path.name, re.I):
            continue
        stat = path.stat()
        thumbnails.append(SavedThumbnail(
            filename=path.name,
            file_url=f"/api/projects/{pid}/thumbnails/{path.name}",
            created_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        ))
    thumbnails.sort(key=lambda item: item.created_at, reverse=True)
    return thumbnails


@router.get("/{pid}/thumbnails/{filename}")
async def get_project_thumbnail(pid: str, filename: str):
    """Serve only generated thumbnail files belonging to this project."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}\.(?:png|jpg|jpeg|webp)", filename, re.I):
        raise HTTPException(400, "Invalid thumbnail filename")
    repo = _get_repo()
    project = await repo.get_project(pid)
    if not project:
        raise HTTPException(404, "Project not found")
    name = project.name if hasattr(project, "name") else project["name"]
    directory = (OUTPUT_DIR / slugify(name) / "thumbnails").resolve()
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(404, "Thumbnail file not found")
    return FileResponse(path, filename=f"frameweaver-{filename}", headers={"Cache-Control": "private, no-cache"})
