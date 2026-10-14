from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from agent.services import media_library as library

router = APIRouter(prefix='/projects/{pid}/library', tags=['media-library'])


class Name(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=120)


class ConfirmName(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=1024)


@router.get('/trash', dependencies=[Depends(library.guard)])
async def trash(pid: UUID):
    return await library.trash_list(str(pid))


@router.get('/items/{key}', dependencies=[Depends(library.guard)])
async def details(pid: UUID, key: UUID):
    return await library.describe(str(pid), str(key))


@router.patch('/items/{key}')
async def rename(pid: UUID, key: UUID, body: Name):
    return await library.change(str(pid), str(key), 'rename', body.name)


@router.post('/items/{key}/trash')
async def move_to_trash(pid: UUID, key: UUID):
    return await library.change(str(pid), str(key), 'trash')


@router.post('/items/{key}/restore')
async def restore(pid: UUID, key: UUID):
    return await library.change(str(pid), str(key), 'restore')


@router.delete('/items/{key}')
async def purge(pid: UUID, key: UUID, body: ConfirmName):
    return await library.change(str(pid), str(key), 'purge', body.name)
