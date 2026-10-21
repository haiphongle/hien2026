"""Content batches share Studio templates, story schedules and production jobs."""
import asyncio
import hashlib
import io
import json
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import Field

from agent.db import crud
from agent.models.stickman import CreateRequest, PlanRequest, ProductionSettings
from agent.models.timeline import TimelineModel
from agent.services import content_library, editing, media_jobs, speech_store, stickman, story_schedule as schedule

_planning = {}
_planning_lock = asyncio.Semaphore(1)


class Batch(schedule.ScheduleTime):
    request_id: UUID
    project_id: UUID
    source_ids: list[UUID] = Field(min_length=1, max_length=200)
    settings: ProductionSettings
    text_provider: Literal['configured', 'codex', 'claude', 'agy', 'muse2api'] = 'configured'
    scene_count: int | None = Field(default=None, ge=1, le=200)
    mode: Literal['review', 'automatic'] = 'review'
    group: str = Field(default='', max_length=80)


class Limits(TimelineModel):
    videos: int = Field(default=2, ge=1, le=3)


def limits():
    return Limits.model_validate(speech_store.read_json(schedule.ROOT/'limits.json', {}))


def save_limits(body):
    speech_store.atomic_json(schedule.ROOT/'limits.json', body.model_dump())
    schedule.wake.set()
    return body


def require_space():
    root = stickman.OUTPUT_DIR
    root.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(root).free < 1024**3:
        raise ValueError('Cần ít nhất 1 GB trống để bắt đầu sản xuất. Dọn ổ đĩa rồi bấm Tiếp tục.')


def recover_batches():
    # A crash during a multi-file enqueue must neither lose articles nor clone
    # the videos already started. Only unfinished commits retain a manifest.
    for path in schedule.ROOT.glob('batch-*.json'):
        saved = speech_store.read_json(path, {})
        for row in saved.get('items', []):
            if not schedule.path(row['project_id'], row['id']).is_file():
                schedule.write(row)
        path.unlink(missing_ok=True)


async def enqueue(body):
    pid, bid = str(body.project_id), str(body.request_id)
    owner = await crud.get_project(pid)
    if not owner or owner.get('status') != 'ACTIVE':
        raise ValueError('Chọn một dự án đang mở để lưu thành phẩm.')
    # Preserve retry IDs from before optional background snapshots were introduced.
    signature = hashlib.sha256(body.model_dump_json(exclude={'settings':{'background'}} if body.settings.background is None else None).encode()).hexdigest()
    async with schedule.lock, content_library.lock, schedule.media_library.lock:
        recover_batches()
        existing = [row for row in schedule.entries(pid) if row.get('batch_id') == bid]
        if existing:
            if any(row.get('batch_signature') != signature for row in existing):
                raise ValueError('Lượt gửi này đã có cấu hình khác. Nạp lại danh sách trước khi tạo nhóm mới.')
            return [schedule.summary(row) for row in existing]
        parts = []
        for ident in dict.fromkeys(map(str, body.source_ids)):
            source = content_library.read(ident)
            if source.get('trashed_at'):
                raise ValueError('Có nguồn ở Thùng rác. Khôi phục trước khi xếp hàng.')
            script = content_library.parts(ident)
            if not script:
                raise ValueError(f'“{source["title"]}” chưa có SRT. Nhận dạng nguồn trước.')
            parts.extend(script)
        if len(parts) > 200:
            raise ValueError('Nguồn dài tạo hơn 200 phần. Chọn ít nguồn hơn cho lượt này.')
        if sum(row['status'] not in {'COMPLETED', 'CANCELLED'} for row in schedule.entries(pid)) + len(parts) > 1000:
            raise ValueError('Dự án đã có quá nhiều bài chờ. Hoàn tất các bài trước khi thêm.')
        settings = body.settings.model_dump(mode='json')
        settings.update(preview_first=body.mode == 'review', preview_all=True)
        stickman.validate_music(body.settings, owner)
        now = datetime.now(timezone.utc).isoformat()
        rows = []
        manifest = schedule.ROOT/f'batch-{bid}.json'
        try:
            for order, part in enumerate(parts):
                row = dict(id=str(uuid.uuid5(body.request_id, part['id'])), project_id=pid, batch_id=bid,
                    batch_signature=signature, batch_order=order, batch_name=body.group.strip() or f'Nhóm {now[:10]}', source_id=part['source_id'], part_id=part['id'],
                    title=part['title'], source=part['srt'], settings=settings.copy(), mode=body.mode,
                    text_provider=body.text_provider, scene_count=body.scene_count, needs_plan=True,
                    content=True, created_at=now, run_at=body.run_at.isoformat() if body.run_at else now,
                    video_id=None, status='SCHEDULED', progress=0, stage='Chờ AI lên kịch bản', error=None)
                rows.append(row)
            speech_store.atomic_json(manifest, {'items':rows})
            for row in rows:
                schedule.write(row)
            manifest.unlink()
        except BaseException:
            for row in rows:
                schedule.path(pid, row['id']).unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            raise
    schedule.wake.set()
    return [schedule.summary(row) for row in rows]


def planning(item):
    return item['id'] in _planning


def paused_video(vid):
    try:
        return (media_jobs.directory(vid)/'production-pause.json').is_file()
    except ValueError:
        return False


def start_plan(item):
    pid, sid = item['project_id'], item['id']
    schedule.write(item, status='PLANNING', progress=0, stage='AI đang lên kịch bản', error=None)
    async def run():
        try:
            async with _planning_lock:
                settings = item['settings']
                fields = {key: value for key, value in settings.items() if key in PlanRequest.model_fields}
                request = PlanRequest(**fields, source=item['source'], text_provider=item.get('text_provider', 'configured'),
                                      scene_count=item.get('scene_count'), auto_timing=settings['motion'] == 'ai')
                result = await stickman.plan(request, pid)
                body = CreateRequest.model_validate({**settings, **result.model_dump(mode='json')})
            async with schedule.lock:
                current = schedule.read(pid, sid)
                schedule.write(current, settings=body.model_dump(mode='json'), needs_plan=False, title=body.title,
                    status='REVIEW_SCRIPT' if current.get('mode') == 'review' else 'SCHEDULED',
                    stage='Kịch bản chờ duyệt' if current.get('mode') == 'review' else 'Chờ tạo ảnh / giọng', progress=0)
        except asyncio.CancelledError:
            # Explicit stop/shutdown owns the final status. No automatic duplicate planning.
            raise
        except Exception as exc:
            schedule.write(schedule.read(pid, sid), status='FAILED', stage='AI lên kịch bản lỗi', error=str(exc))
        finally:
            _planning.pop(sid, None)
            schedule.wake.set()
    task = asyncio.create_task(run())
    _planning[sid] = task
    def finished(done):
        if _planning.get(sid) is done:
            _planning.pop(sid, None)
        schedule.wake.set()
    task.add_done_callback(finished)


async def shutdown():
    tasks = list(_planning.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _planning.clear()


async def action(pid, sid, kind):
    async with schedule.lock:
        item = schedule.read(pid, sid)
        if not item.get('content'):
            raise ValueError('Bài này được quản lý ở Studio.')
        await schedule.sync(item)
        if kind == 'stop':
            if item['status'] == 'COMPLETED':
                return schedule.summary(item)
            task = _planning.get(sid)
            if task:
                task.cancel()
                # Planner may be waiting for this lock; cancellation releases it.
                await asyncio.gather(task, return_exceptions=True)
            if item.get('video_id'):
                speech_store.atomic_json(media_jobs.directory(item['video_id'])/'production-pause.json', {'paused':True})
                job = media_jobs.latest(item['video_id'])
                if job and job['kind'] == 'stickman' and job['status'] in media_jobs.ACTIVE:
                    await media_jobs.cancel(item['video_id'])
            schedule.write(item, status='CANCELLED', paused=True, stage='Đã dừng · giữ kết quả đã tạo', error=None)
        elif kind in {'resume', 'approve'}:
            if item['status'] in {'RUNNING', 'STARTING', 'PLANNING', 'COMPLETED'}:
                return schedule.summary(item)
            if item['status'] in {'REVIEW', 'REVIEW_SCRIPT'} and kind != 'approve':
                raise ValueError('Duyệt kịch bản / ảnh trước khi tiếp tục.')
            if kind == 'approve' and item['status'] not in {'REVIEW', 'REVIEW_SCRIPT'}:
                raise ValueError('Bài chưa ở bước chờ duyệt.')
            if kind == 'resume' and item.get('video_id') and (media_jobs.latest(item['video_id']) or {}).get('result', {}).get('preview_url'):
                return schedule.summary(schedule.write(item, status='REVIEW', paused=False, stage='Ảnh chờ duyệt'))
            # Dispatch handles retry and image approval through the existing Studio route.
            schedule.write(item, status='SCHEDULED', run_at=datetime.now(timezone.utc).isoformat(),
                           error=None, approve_preview=item['status'] == 'REVIEW', paused=False, stage='Chờ tiếp tục')
            schedule.wake.set()
        return schedule.summary(item)


async def revise(pid, sid, settings=None, scenes=None, run_at=None):
    async with schedule.lock, schedule.media_library.lock:
        item = schedule.read(pid, sid)
        if not item.get('content') or item.get('video_id') or item['status'] in {'PLANNING', 'STARTING', 'RUNNING'}:
            raise ValueError('Chỉ sửa bài chưa tạo ảnh/video. Mở Studio để chỉnh video đã bắt đầu.')
        owner = await crud.get_project(pid)
        if not owner or owner.get('status') != 'ACTIVE':
            raise ValueError('Dự án chưa mở. Mở lại dự án trước khi sửa bài.')
        current = item['settings']
        old_background = current.get('background') or {}
        new_background = settings.background.model_dump(mode='json') if settings is not None and settings.background else {}
        replan = settings is not None and any(old_background.get(key) != new_background.get(key) for key in ('description','image_id'))
        if settings is not None:
            current = {**current, **settings.model_dump(mode='json'), 'preview_first':item['mode'] == 'review', 'preview_all':True}
        if scenes is not None:
            current['scenes'] = [scene.model_dump(mode='json') for scene in scenes]
        if replan:
            current = {key:value for key,value in current.items() if key in ProductionSettings.model_fields}
            current.update(story_assets=[], analysis={})
        needs_plan = item.get('needs_plan') or replan
        parsed = (ProductionSettings if needs_plan else CreateRequest).model_validate(current)
        if not needs_plan and parsed.motion != 'talk':
            from agent.services.stickman_direction import validate_shots
            validate_shots(parsed)
        stickman.validate_music(parsed, owner)
        changes = {'needs_plan':True, 'status':'SCHEDULED', 'paused':False, 'stage':'Bối cảnh đổi · chờ AI lên lại kịch bản'} if replan else {}
        if run_at:
            changes.update(run_at=run_at.isoformat(), status='SCHEDULED', paused=False)
        result = schedule.write(item, settings=parsed.model_dump(mode='json'), **changes, error=None)
        if replan:
            schedule.wake.set()
        return schedule.summary(result)


def exports(items):
    result = []
    for item in items:
        if item['status'] != 'COMPLETED' or not item.get('video_id'):
            raise ValueError('Chỉ chọn những bài hoàn tất để tải nhóm.')
        saved = speech_store.read_json(stickman.directory(item['video_id'])/'result.json', {})
        eid = (saved.get('result') or {}).get('export_id')
        if not eid:
            raise ValueError('Bản xuất chưa có hoặc đã bị xóa.')
        path = editing.directory(item['video_id'])/'exports'/f'{UUID(eid)}.mp4'
        if not path.is_file():
            raise ValueError(f'Bản xuất “{item["title"]}” không còn trên máy.')
        result.append((item, path))
    return result


def zip_exports(files):
    """Stream stored MP4s without another full disk copy or a whole-file RAM buffer."""
    class Buffer(io.RawIOBase):
        def __init__(self):
            self.data = bytearray()
        def writable(self):
            return True
        def write(self, data):
            self.data.extend(data)
            return len(data)
        def take(self):
            result = bytes(self.data)
            self.data.clear()
            return result
    buffer = Buffer()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_STORED) as archive:
        for index, (item, path) in enumerate(files, 1):
            from agent.utils.slugify import slugify
            name = f'{index:03d}-{slugify(item["title"])[:80]}-{item["id"][:8]}.mp4'
            with path.open('rb') as source, archive.open(name, 'w', force_zip64=True) as target:
                while chunk := source.read(1024**2):
                    target.write(chunk)
                    yield buffer.take()
            yield buffer.take()
    yield buffer.take()
