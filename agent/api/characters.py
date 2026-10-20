import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from agent.config import OUTPUT_DIR
from agent.models.character import Character, CharacterCreate, CharacterUpdate
from agent.sdk.persistence.sqlite_repository import SQLiteRepository
from agent.utils.slugify import slugify

router = APIRouter(prefix="/characters", tags=["characters"])
_MEDIA_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


def _get_repo() -> SQLiteRepository:
    return SQLiteRepository()


@router.post("", response_model=Character)
async def create(body: CharacterCreate):
    repo = _get_repo()
    return await repo.create_character(**body.model_dump(exclude_none=True))


@router.get("", response_model=list[Character])
async def list_all():
    repo = _get_repo()
    rows = await repo.list("character", order_by="created_at DESC")
    return [repo._row_to_character(r) for r in rows]


@router.get("/{cid}/image")
async def character_image(cid: str):
    if not _MEDIA_UUID.fullmatch(cid):
        raise HTTPException(404, "Image not found")
    image_dir = OUTPUT_DIR / "character_refs"
    for extension, mime in ((".png", "image/png"), (".jpg", "image/jpeg"), (".webp", "image/webp")):
        path = image_dir / f"{cid}{extension}"
        if path.is_file():
            return FileResponse(path, media_type=mime)
    raise HTTPException(404, "Image not found")


@router.get("/{cid}", response_model=Character)
async def get(cid: str):
    repo = _get_repo()
    c = await repo.get_character(cid)
    if not c:
        raise HTTPException(404, "Character not found")
    return c


@router.patch("/{cid}", response_model=Character)
async def update(cid: str, body: CharacterUpdate):
    repo = _get_repo()
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates:
        updates["slug"] = slugify(updates["name"])
    row = await repo.update("character", cid, **updates)
    if not row:
        raise HTTPException(404, "Character not found")
    return repo._row_to_character(row)


@router.delete("/{cid}")
async def delete(cid: str, project_id: str | None = None):
    from agent.services import library_deletion
    return await library_deletion.delete_character(cid, project_id)


@router.get('/{cid}/deletion-info')
async def deletion_info(cid: str, project_id: str | None = None):
    from agent.services import library_deletion
    return await library_deletion.character_info(cid, project_id)
