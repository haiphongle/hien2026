"""Content library endpoints, independent of existing project videos."""
from typing import Literal
from uuid import UUID
import shutil

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agent.services import content_library as library, content_production, local_models, media_jobs, story_schedule, transcription
from agent.models.stickman import ProductionSettings, StoryScene
from agent.services.speech_store import srt_text

router = APIRouter(prefix='/content', tags=['content-library'])
WhisperModel = Literal['base', 'small', 'large-v3-turbo-q5_0']


class Changes(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str | None = Field(default=None, min_length=1, max_length=160)
    group: str | None = Field(default=None, max_length=80)
    trashed: bool | None = None

    @field_validator('title')
    @classmethod
    def nonempty(cls, value):
        if value is not None and not value.strip():
            raise ValueError('Tên không được để trống.')
        return value


class Batch(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=200)
    model: WhisperModel = 'small'
    language: str = 'auto'
    restart: bool = False


class ProductionActions(BaseModel):
    ids: list[UUID] = Field(min_length=1, max_length=200)
    action: Literal['stop', 'resume', 'approve']


class ProductionChanges(story_schedule.ScheduleTime):
    settings: ProductionSettings | None = None
    scenes: list[StoryScene] | None = Field(default=None, min_length=1, max_length=200)


@router.get('/production')
async def production():
    from agent.services.flow_client import get_flow_client
    async with story_schedule.lock:
        items = [item for item in story_schedule.entries() if item.get('content')]
        for item in items:
            await story_schedule.sync(item)
    root = content_production.stickman.OUTPUT_DIR
    root.mkdir(parents=True, exist_ok=True)
    return {'items': [story_schedule.summary(item) for item in items],
            'limits': content_production.limits().model_dump(),
            'conditions': {'flow_connected':get_flow_client().connected, 'free_bytes':shutil.disk_usage(root).free}}


@router.post('/production', status_code=202)
async def produce(body: content_production.Batch):
    try:
        return await content_production.enqueue(body)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put('/production/limits')
async def production_limits(body: content_production.Limits):
    return content_production.save_limits(body)


@router.post('/production/actions')
async def production_actions(body: ProductionActions):
    items = {item['id']:item for item in story_schedule.entries() if item.get('content')}
    ids = list(dict.fromkeys(map(str, body.ids)))
    if not set(ids) <= items.keys():
        raise HTTPException(404, 'Có bài không còn trong hàng chờ. Nạp lại danh sách.')
    results, errors = [], []
    for ident in ids:
        try:
            results.append(await content_production.action(items[ident]['project_id'], ident, body.action))
        except (ValueError, FileNotFoundError) as exc:
            errors.append({'id':ident, 'title':items[ident]['title'], 'error':str(exc)})
    return {'items':results, 'errors':errors}


@router.patch('/production/{pid}/{sid}')
async def revise_production(pid: UUID, sid: UUID, body: ProductionChanges):
    try:
        return await content_production.revise(str(pid), str(sid), body.settings, body.scenes, body.run_at)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/production/download')
async def download_production(ids: list[UUID] = Query(min_length=1, max_length=200)):
    items = {item['id']:item for item in story_schedule.entries() if item.get('content')}
    wanted = list(dict.fromkeys(map(str, ids)))
    if not set(wanted) <= items.keys():
        raise HTTPException(404, 'Có thành phẩm không còn trong danh sách.')
    try:
        async with story_schedule.lock:
            selected = [items[ident] for ident in wanted]
            for item in selected:
                await story_schedule.sync(item)
            files = content_production.exports(selected)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return StreamingResponse(content_production.zip_exports(files), media_type='application/zip',
        headers={'Content-Disposition':'attachment; filename="frameweaver-videos.zip"'})


@router.get('')
async def list_content():
    models = [{**model, 'engine': 'Whisper', 'management_url': f'/api/content/models/{model["id"]}',
               'source_url': 'https://huggingface.co/ggerganov/whisper.cpp'} for model in transcription.models()]
    return {'items': library.entries(), 'models': models,
            'whisper_available': bool(transcription.binary()), 'languages': transcription.LANGUAGES}


@router.post('/models/{model}/{action}', status_code=202)
async def model_action(model: WhisperModel, action: Literal['download', 'verify', 'cancel']):
    try:
        return await local_models.action(f'whisper-{model}', action)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/upload', status_code=201)
async def upload(request: Request, filename: str = Query(min_length=1, max_length=255)):
    try:
        return await library.upload(request, filename)
    except (ValueError, RuntimeError, TimeoutError, UnicodeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/transcribe', status_code=202)
async def transcribe(body: Batch):
    try:
        return await library.enqueue(list(dict.fromkeys(map(str, body.ids))), body.model, body.language, body.restart)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/cancel')
async def cancel(body: Batch):
    for ident in dict.fromkeys(map(str, body.ids)):
        try:
            library.read(ident)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        await media_jobs.cancel(ident)
    return {'ok': True}


@router.post('/scripts')
async def scripts(body: Batch):
    items = [get(ident) for ident in body.ids]
    if any(i.get('trashed_at') or not library.transcript(i['id']) for i in items):
        raise HTTPException(409, 'Có nguồn chưa có SRT hoặc đang ở Thùng rác. Nạp lại danh sách.')
    try:
        return [part for item in items for part in library.parts(item['id'])]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def get(ident):
    try:
        return library.read(str(ident))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get('/{ident}')
async def detail(ident: UUID):
    item = get(ident)
    try:
        return {**library.summary(item), 'parts': [] if item.get('trashed_at') else library.parts(str(ident))}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch('/{ident}')
async def change(ident: UUID, body: Changes):
    get(ident)
    try:
        return await library.change(str(ident), body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/{ident}/source')
async def source(ident: UUID):
    item = get(ident)
    if item.get('trashed_at'):
        raise HTTPException(409, 'Nguồn đang ở Thùng rác.')
    path = library.directory(ident) / f"source{item['suffix']}"
    if not path.is_file():
        raise HTTPException(404, 'File nguồn đã mất.')
    return FileResponse(path)


@router.get('/{ident}/subtitles.srt')
async def subtitles(ident: UUID):
    item = get(ident)
    doc = library.transcript(str(ident))
    if item.get('trashed_at') or not doc:
        raise HTTPException(404, 'Chưa có SRT. Nhận dạng hoặc khôi phục nguồn trước.')
    return Response(srt_text(doc.cues), media_type='application/x-subrip; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="frameweaver-subtitles.srt"'})
