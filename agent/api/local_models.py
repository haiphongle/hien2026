"""Shared local model controls for Settings, Content and voice libraries."""
import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from agent.services import local_models, model_storage

router = APIRouter(prefix='/local-models', tags=['local-models'])


class StorageChoice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    directory: str | None = Field(default=None, max_length=4096)


class MoveChoice(StorageChoice):
    ids: list[str] = Field(min_length=1, max_length=8)


class MovePlan(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(max_length=80)
    directory: str = Field(min_length=1, max_length=4096)
    token: str = Field(pattern=r'^[a-f0-9]{64}$')


class MoveBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    plans: list[MovePlan] = Field(min_length=1, max_length=8)


class ConfirmDelete(BaseModel):
    confirm: Literal[True]


@router.get('/storage')
async def storage_info():
    try:
        return await asyncio.to_thread(model_storage.info)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/storage/folders')
async def storage_folders(path: str | None = None):
    try:
        return await asyncio.to_thread(model_storage.browse, path)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put('/storage')
async def storage_default(body: StorageChoice):
    try:
        return await model_storage.set_default(body.directory)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/storage/preview')
async def storage_preview(body: MoveChoice):
    if not body.directory or len(set(body.ids)) != len(body.ids):
        raise HTTPException(422, 'Chọn thư mục đích và danh sách model không trùng.')
    try:
        return await asyncio.to_thread(lambda: [model_storage.preview(ident, body.directory) for ident in body.ids])
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/storage/move', status_code=202)
async def storage_move(body: MoveBatch):
    try:
        return model_storage.start_moves([plan.model_dump() for plan in body.plans])
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/{ident}/previous/delete')
async def delete_previous(ident: str, body: ConfirmDelete):
    try:
        return await model_storage.delete_previous(ident)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('')
async def catalogue():
    return await asyncio.to_thread(local_models.catalogue)


@router.get('/{ident}')
async def detail(ident: str):
    try:
        return await asyncio.to_thread(local_models.inventory, ident)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/{ident}/{operation}')
async def action(ident: str, operation: Literal['download', 'verify', 'cancel', 'delete', 'unload', 'open-folder']):
    try:
        if operation == 'open-folder':
            await asyncio.to_thread(local_models.open_folder, ident)
            return {'ok': True}
        return await local_models.action(ident, operation)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
