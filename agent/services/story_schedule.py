"""Local saved stories and a sequential, restart-safe production schedule."""
from agent.services.image_effects import uses_ai
import asyncio
import logging
import uuid
from datetime import datetime, timezone

from pydantic import Field, field_validator

from agent.config import SHARED_OUTPUT_DIR
from agent.db import crud
from agent.models.stickman import CreateRequest
from agent.models.timeline import TimelineModel
from agent.services import media_jobs, media_library, speech_store, stickman, timeline
from agent.services.flow_client import get_flow_client

ROOT = SHARED_OUTPUT_DIR / 'story_schedule'
lock = asyncio.Lock()
wake = asyncio.Event()
logger = logging.getLogger(__name__)


class ScheduleTime(TimelineModel):
    run_at: datetime | None = None

    @field_validator('run_at')
    @classmethod
    def future(cls, value):
        if value is not None:
            if value.tzinfo is None or value <= datetime.now(timezone.utc):
                raise ValueError('Chọn ngày giờ tương lai có múi giờ.')
            return value.astimezone(timezone.utc)
        return value


class SavedStory(ScheduleTime):
    source: str = Field(min_length=3, max_length=12000)
    settings: CreateRequest


def path(pid, sid):
    return ROOT / speech_store.identifier(pid) / f'{speech_store.identifier(sid)}.json'


def read(pid, sid):
    item = speech_store.read_json(path(pid, sid))
    if not item:
        raise FileNotFoundError('Không tìm thấy kịch bản đã lưu.')
    return item


def write(item, **changes):
    item.update(changes, updated_at=datetime.now(timezone.utc).isoformat())
    speech_store.atomic_json(path(item['project_id'], item['id']), item)
    return item


def entries(pid=None):
    files = (ROOT / speech_store.identifier(pid)).glob('*.json') if pid else ROOT.glob('*/*.json')
    return sorted((item for file in files if (item := speech_store.read_json(file))),
                  key=lambda item: (item['created_at'], -item.get('batch_order', 0)), reverse=True)


def summary(item):
    return {k: v for k, v in item.items() if k not in {'source', 'settings'}}


async def save(pid, body):
    async with lock, media_library.lock:
        if sum(item['status'] in {'DRAFT', 'SCHEDULED', 'WAITING'} for item in entries(pid)) >= 100:
            raise ValueError('Đã có 100 bản nháp/lịch chờ. Xóa bản nháp không dùng trước khi thêm.')
        if body.settings.music_filename:
            stickman.validate_music(body.settings, await crud.get_project(pid))
        item = dict(id=str(uuid.uuid4()), project_id=pid, title=body.settings.title,
                    source=body.source, settings=body.settings.model_dump(mode='json'),
                    created_at=datetime.now(timezone.utc).isoformat(), video_id=None,
                    run_at=body.run_at.isoformat() if body.run_at else None,
                    status='SCHEDULED' if body.run_at else 'DRAFT', error=None)
        return write(item)


async def reschedule(pid, sid, body):
    async with lock, media_library.lock:
        item = read(pid, sid)
        await sync(item)
        if item['video_id'] or item['status'] not in {'DRAFT', 'SCHEDULED', 'WAITING', 'FAILED'}:
            raise ValueError('Kịch bản đã bắt đầu tạo. Mở video để tiếp tục hoặc lưu thành bản mới.')
        if body.run_at and item['settings'].get('music_filename'):
            stickman.validate_music(CreateRequest.model_validate(item['settings']), await crud.get_project(pid))
        return write(item, run_at=body.run_at.isoformat() if body.run_at else None,
                     status='SCHEDULED' if body.run_at else 'DRAFT', error=None, progress=0, stage='')


async def remove(pid, sid):
    async with lock, media_library.lock:
        item = read(pid, sid)
        await sync(item)
        if item['status'] not in {'DRAFT', 'FAILED'} or item['video_id']:
            raise ValueError('Hủy lịch trước khi xóa bản nháp. Video đã tạo được quản lý trong dự án.')
        path(pid, sid).unlink()


async def sync(item):
    if item['status'] == 'PLANNING':
        from agent.services import content_production
        if not content_production.planning(item):
            write(item, status='FAILED', stage='Lên kịch bản bị gián đoạn',
                  error='Máy chủ đã khởi động lại. Bấm Tiếp tục để lên kịch bản; không tự tạo lượt trùng.')
        return
    if item['status'] not in {'STARTING', 'RUNNING', 'REVIEW', 'FAILED', 'CANCELLED'} or not item.get('video_id'):
        return
    video = await crud.get_video(item['video_id'], include_trashed=True)
    if not video or video.get('trashed_at'):
        reason = 'Video đã nằm trong Thùng rác.' if video else 'Video đã bị xóa.'
        write(item, status='FAILED', video_id=None, job_id=None, progress=0, stage='',
              error=reason + ' Kịch bản vẫn được giữ; nạp lại hoặc hẹn tạo bản mới.')
        return
    job = media_jobs.latest(item['video_id'])
    if job and job['kind'] != 'stickman':
        job = speech_store.read_json(stickman.directory(item['video_id'])/'result.json')
    if not job:
        initialized = (stickman.directory(item['video_id'])/'plan.json').is_file()
        if not initialized:
            await crud.update_video(item['video_id'], status='FAILED')
        write(item, status='FAILED', video_id=item['video_id'] if initialized else None,
              job_id=None, progress=0, stage='', error=(
                  'Máy chủ dừng trước khi tạo xong. Mở video để tiếp tục; lịch không tự chạy lại.' if initialized else
                  'Máy chủ dừng khi khởi tạo video. Bản tạo dở được giữ trong dự án; nạp kịch bản để hẹn lại.'))
        return
    state = job['status']
    if item.get('paused') and state not in media_jobs.ACTIVE and job.get('id') == item.get('job_id'):
        return
    status = 'RUNNING' if state in media_jobs.ACTIVE else 'REVIEW' if state == 'COMPLETED' and job.get('result', {}).get('preview_url') else state
    changes = dict(status=status, progress=job.get('progress', 0), stage=job.get('stage', ''), error=job.get('error'),
                   job_id=job.get('id'), url=job.get('result', {}).get('url'), preview_url=job.get('result', {}).get('preview_url'))
    if item.get('paused'):
        changes['paused'] = False  # A new Studio retry can resume the same saved video.
    if any(item.get(key) != value for key, value in changes.items()):
        write(item, **changes)


async def tick():
    from agent.services import storage_locations
    if storage_locations.busy() or storage_locations.offline('output'):
        return
    from agent.services import content_production
    async with lock:
        content_production.recover_batches()
        items = entries()
        for item in items:
            await sync(item)
        # Reviews release their slot. ASR/TTS and exports keep their shared locks;
        # provider image/video requests still go through the existing batch queue.
        active = sum(item['status'] in {'STARTING', 'RUNNING', 'PLANNING'} for item in items)
        capacity = content_production.limits().videos
        if active >= capacity:
            return
        due = sorted((item for item in items if item['status'] in {'SCHEDULED', 'WAITING'}
                      and datetime.fromisoformat(item['run_at']) <= datetime.now(timezone.utc)),
                     key=lambda item: (item['run_at'], item['created_at'], item.get('batch_order', 0)))
        for item in due:
            if active >= capacity:
                break
            try:
                project = await crud.get_project(item['project_id'])
                if not project or project.get('status') != 'ACTIVE':
                    raise ValueError('Dự án chưa mở. Mở lại dự án rồi đặt lịch lại.')
                if item.get('needs_plan'):
                    if not any(row['status'] == 'PLANNING' for row in items):
                        content_production.start_plan(item)
                        active += 1
                    continue
                body = CreateRequest.model_validate(item['settings'])
                needs_media = True
                if item.get('content') and item.get('video_id'):
                    pending = speech_store.read_json(stickman.directory(item['video_id'])/'pending.json', {})
                    needs_media = not timeline.load(item['video_id']) or bool(pending and not pending.get('ready'))
                try:
                    content_production.require_space()
                except ValueError as exc:
                    if item['status'] != 'WAITING' or item.get('error') != str(exc):
                        write(item, status='WAITING', error=str(exc))
                    continue
                if needs_media and body.motion != 'talk' and (body.image_provider == 'flow' or uses_ai(body) and body.video_provider == 'flow') and not get_flow_client().connected:
                    if item['status'] != 'WAITING':
                        write(item, status='WAITING', error='Đang chờ Google Flow và extension kết nối.')
                    continue
                if needs_media and body.motion != 'talk' and (body.image_provider == 'muse2api' or uses_ai(body) and body.video_provider == 'muse2api'):
                    from agent.services.muse_client import MuseClient, MuseError
                    try:
                        await asyncio.wait_for(MuseClient().readiness(), timeout=8)
                    except (MuseError, TimeoutError) as exc:
                        reason = str(exc) or 'Muse chưa trả trạng thái. Kiểm tra kết nối gateway.'
                        if item['status'] != 'WAITING' or item.get('error') != reason:
                            write(item, status='WAITING', error=reason)
                        continue
                async with media_library.lock:
                    await stickman.validate(body, project)
                    if item.get('content') and item.get('video_id'):
                        from agent.api.stickman import retry
                        from agent.models.stickman import RetryRequest
                        job = await retry(uuid.UUID(item['video_id']), RetryRequest(approve_preview=item.get('approve_preview', False)))
                        write(item, status='RUNNING', job_id=job['id'], approve_preview=False, paused=False)
                        active += 1
                        continue
                    # Persist identity before any side effect, so an interrupted
                    # startup cannot accidentally create the same story twice.
                    vid = str(uuid.uuid4())
                    write(item, status='STARTING', video_id=vid, error=None)
                    result = await stickman.create_story(project, body, video_id=vid)
                    write(item, status='RUNNING', job_id=result['job']['id'])
                    active += 1
            except asyncio.CancelledError:
                if item['status'] == 'STARTING':
                    write(item, status='FAILED', video_id=None, error='Máy chủ đã dừng. Nạp kịch bản để kiểm tra và đặt lịch lại.')
                raise
            except Exception as exc:
                logger.warning('Scheduled story %s failed: %s', item['id'], exc)
                exists = await crud.get_video(item['video_id']) if item.get('video_id') else None
                write(item, status='FAILED', video_id=item['video_id'] if exists else None, error=str(exc))


async def run():
    while True:
        wake.clear()
        try:
            await tick()
        except Exception:
            logger.exception('Could not check saved story schedule')
        try:
            await asyncio.wait_for(wake.wait(), timeout=5)
        except TimeoutError:
            pass
