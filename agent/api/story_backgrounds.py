"""One background library for Studio and Content; no image generation calls."""
import asyncio
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from agent.models.story_background import BackgroundAnalyze, BackgroundFavorite, BackgroundWrite, TextProvider
from agent.models.story_style import StylePreviewUpload
from agent.models.stickman import PlanRequest
from agent.services import story_backgrounds as library

router = APIRouter(prefix='/story-backgrounds', tags=['story-backgrounds'])


async def call(function, *args):
    try:
        return await function(*args)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('')
async def inventory(include_deleted: bool = False):
    return await call(asyncio.to_thread, library.inventory, include_deleted)


@router.get('/images/{ident}')
async def image(ident: UUID):
    result = await call(asyncio.to_thread, library.image_path, ident)
    return FileResponse(result, media_type='image/webp')


@router.post('/analyze')
async def analyze(body: BackgroundAnalyze):
    return await call(library.analyze, body)


@router.get('/analysis-provider')
async def analysis_provider(text_provider: TextProvider = 'configured'):
    return library.resolve_role('prompt_suggestion', text_provider)


@router.post('/prompt')
async def draft_prompt(body: PlanRequest, project_id: UUID | None = None):
    return await call(asyncio.to_thread, library.sample_prompt, None, body, str(project_id) if project_id else None)


@router.post('', status_code=201)
async def create(body: BackgroundWrite):
    return await call(library.save, body)


@router.post('/{ident}/prompt')
async def prompt(ident: str, body: PlanRequest, project_id: UUID | None = None):
    return await call(asyncio.to_thread, library.sample_prompt, ident, body, str(project_id) if project_id else None)


@router.put('/{ident}/preview')
async def save_preview(ident: str, body: StylePreviewUpload):
    return await call(library.save_preview, ident, body)


@router.delete('/{ident}/preview')
async def reset_preview(ident: str):
    return await call(library.reset_preview, ident)


@router.patch('/{ident}')
async def update(ident: UUID, body: BackgroundWrite):
    return await call(library.save, body, str(ident))


@router.patch('/{ident}/favorite')
async def favorite(ident: str, body: BackgroundFavorite):
    return await call(library.favorite, ident, body.favorite)


@router.post('/{ident}/duplicate', status_code=201)
async def duplicate(ident: str):
    return await call(library.duplicate, ident)


@router.get('/{ident}/usage')
async def usage(ident: str):
    return await call(asyncio.to_thread, library.usage, ident)


@router.delete('/{ident}')
async def remove(ident: UUID):
    return await call(library.remove, str(ident))


@router.post('/{ident}/restore')
async def restore(ident: UUID):
    return await call(library.remove, str(ident), True)
