"""Folder inventory and explicit, cancellable relocation controls."""
import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from agent.services import storage_locations as storage

router = APIRouter(prefix='/storage-locations', tags=['storage'])
Location = Literal['output', 'build-cache', 'build-output', 'release']


class Choice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Location
    directory: str = Field(min_length=1, max_length=4096)


class Move(Choice):
    token: str = Field(pattern=r'^[a-f0-9]{64}$')


class Delete(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: Location
    confirm: Literal[True]


@router.get('')
async def inventory(group: Literal['data','environment','build'] = 'data'):
    try:
        return await asyncio.to_thread(storage.inventory, group)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/preview')
async def preview(body: Choice):
    try:
        return await asyncio.to_thread(storage.preview, body.id, body.directory)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/move', status_code=202)
async def move(body: Move):
    try:
        return await storage.start(body.id, 'move', body.directory, body.token)
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/previous/delete', status_code=202)
async def delete(body: Delete):
    try:
        return await storage.start(body.id, 'delete')
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/cancel')
async def cancel():
    return await storage.stop()


@router.post('/{ident}/open-folder')
async def open_folder(ident: str):
    try:
        await asyncio.to_thread(storage.open_folder, ident)
        return {'ok':True}
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc
