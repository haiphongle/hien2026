"""Deletion checks for projects and shared reference characters."""
import asyncio
import json
import logging

from fastapi import HTTPException
from agent.config import OUTPUT_DIR, SHARED_OUTPUT_DIR
from agent.db import crud
from agent.db.schema import get_db
from agent.services import media_library, speech_store, story_schedule

logger = logging.getLogger(__name__)


async def rows(sql, params=()):
    cursor = await (await get_db()).execute(sql, params)
    return [dict(row) for row in await cursor.fetchall()]


async def require_project(pid):
    project = await crud.get_project(pid)
    if not project:
        raise HTTPException(404, 'Không tìm thấy dự án.')
    return project


def idle_reason(videos, requests):
    if any(r['status'] in {'PENDING', 'PROCESSING'} for r in requests):
        return 'Dự án đang có yêu cầu tạo nội dung. Đợi hoàn tất hoặc dừng tác vụ trước khi xóa.'
    from agent.api.speech import ensure_idle
    try:
        for video in videos:
            ensure_idle(video['id'])
    except HTTPException as exc:
        return str(exc.detail)
    if media_library.video_export_lock.locked():
        return 'Đang ghép hoặc xuất video. Đợi hoàn tất trước khi xóa.'
    return None


async def project_info(pid):
    project = await require_project(pid)
    videos = await crud.list_videos(pid, include_trashed=True)
    requests = await crud.list_requests(project_id=pid)
    counts = await rows('SELECT count(*) AS total FROM scene s JOIN video v ON v.id=s.video_id WHERE v.project_id=?', (pid,))
    uses = []
    # An imported source may belong to another project's timeline.
    other_videos = await rows('SELECT * FROM video WHERE project_id<>?', (pid,))
    def external_uses():
        return [dict(kind='video', name=used['name'], project_id=next(v['project_id'] for v in other_videos if v['id'] == used['id']))
                for video in videos for used in media_library.usages(project, {'id': video['id'], 'kind': 'video'}, other_videos)]
    try:
        uses.extend(await asyncio.to_thread(external_uses))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, 'Không đọc được bản dựng để kiểm tra nơi sử dụng. Chưa xóa dự án.') from exc
    try:
        uses.extend(dict(kind='schedule', name=f"Lịch tạo · {item['title']}", project_id=pid)
                    for item in story_schedule.entries(pid)
                    if item['status'] in {'SCHEDULED', 'WAITING', 'STARTING', 'RUNNING', 'REVIEW'})
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(409, 'Không đọc được lịch tạo video. Chưa xóa dự án.') from exc
    reason = idle_reason(videos, requests)
    return dict(id=pid, name=project['name'], video_count=len(videos), scene_count=counts[0]['total'],
                uses=uses, reason=reason, can_delete=not reason and not uses)


def contains_reference(data, cid):
    if isinstance(data, dict):
        return any(contains_reference(value, cid) for value in data.values())
    if isinstance(data, list):
        return any(contains_reference(value, cid) for value in data)
    return data == cid


def saved_uses(cid, videos):
    used = []
    def inspect(path, kind, name, pid):
        if contains_reference(speech_store.read_json(path, {}), cid):
            used.append(dict(kind=kind, name=name, project_id=pid))
    for video in videos:
        for filename in ('plan.json', 'reference.json'):
            inspect(SHARED_OUTPUT_DIR/'media_workbench'/video['id']/'stickman'/filename, 'video', video['title'], video['project_id'])
    for directory, kind in [('story_schedule', 'Kịch bản đã lưu'), ('stickman_templates', 'Mẫu sản xuất'), ('stickman_styles', 'Phong cách')]:
        for path in (SHARED_OUTPUT_DIR/directory).glob('*/*.json'):
            data = speech_store.read_json(path, {})
            if contains_reference(data, cid):
                used.append(dict(kind='saved', name=f"{kind} · {data.get('title') or data.get('name') or path.stem}", project_id=path.parent.name))
    return used


async def character_info(cid, pid=None):
    character = await crud.get_character(cid)
    if not character:
        raise HTTPException(404, 'Không tìm thấy nhân vật.')
    projects = await rows('SELECT p.id,p.name FROM project p JOIN project_character pc ON pc.project_id=p.id WHERE pc.character_id=?', (cid,))
    if pid and not any(p['id'] == pid for p in projects):
        raise HTTPException(404, 'Nhân vật không thuộc dự án này.')
    videos = await rows('SELECT * FROM video')
    scenes = await rows('SELECT s.character_names,v.id,v.title,v.project_id FROM scene s JOIN video v ON v.id=s.video_id')
    uses = []
    linked = {p['id'] for p in projects}
    names = {character['name'], character.get('slug')} - {None, ''}
    try:
        for scene in scenes:
            if scene['project_id'] not in linked:
                continue
            assigned = json.loads(scene['character_names'] or '[]')
            if not isinstance(assigned, list):
                raise ValueError('Invalid character list')
            if names.intersection(assigned):
                item = dict(kind='video', name=scene['title'], project_id=scene['project_id'])
                if item not in uses:
                    uses.append(item)
        uses.extend(await asyncio.to_thread(saved_uses, cid, videos))
    except (OSError, ValueError) as exc:
        raise HTTPException(409, 'Không đọc được kịch bản để kiểm tra nơi sử dụng. Chưa xóa nhân vật.') from exc
    uses = list({(use['kind'], use['project_id'], use['name']): use for use in uses}.values())
    requests = await rows("""SELECT * FROM request WHERE status IN ('PENDING','PROCESSING') AND
        (character_id=? OR project_id IN (SELECT project_id FROM project_character WHERE character_id=?))""", (cid, cid))
    relevant_videos = [v for v in videos if v['project_id'] in linked]
    reason = idle_reason(relevant_videos, requests)
    local_uses = [use for use in uses if use['project_id'] == pid]
    other_projects = [p for p in projects if p['id'] != pid]
    return dict(id=cid, name=character['name'], projects=projects, other_projects=other_projects, uses=uses, reason=reason,
                can_remove=bool(pid) and not reason and not local_uses,
                can_delete=not reason and not uses and not other_projects)


async def delete_project(pid, name):
    # Use the existing setup locks in the same order as scheduled production.
    async with story_schedule.lock, media_library.lock:
        info = await project_info(pid)
        if name != info['name']:
            raise HTTPException(409, 'Nhập đúng tên dự án để xác nhận xóa.')
        if not info['can_delete']:
            raise HTTPException(409, info['reason'] or 'Dự án còn lịch chờ hoặc tư liệu dùng ở dự án khác. Gỡ nơi sử dụng trước khi xóa.')
        if not await crud.delete_project(pid):
            raise HTTPException(404, 'Không tìm thấy dự án.')
        # These JSON files are project settings, not source media or exports.
        for folder in ('story_schedule', 'stickman_templates', 'stickman_styles'):
            root = SHARED_OUTPUT_DIR/folder/pid
            if root.is_symlink() or not root.resolve().is_relative_to(SHARED_OUTPUT_DIR.resolve()):
                continue
            for path in root.glob('*.json'):
                try:
                    path.unlink()
                except OSError:
                    logger.warning('Could not remove deleted project metadata: %s', path.name)
        from agent.api import active_project
        if (active_project._read_state() or {}).get('project_id') == pid:
            active_project._clear_state()
        return {'ok': True}


async def delete_character(cid, pid=None, *, unlink=False):
    async with story_schedule.lock, media_library.lock:
        info = await character_info(cid, pid)
        allowed = info['can_remove'] if unlink else info['can_delete']
        if not allowed:
            raise HTTPException(409, info['reason'] or 'Nhân vật đang được dùng. Gỡ khỏi cảnh, kịch bản hoặc dự án liên quan trước khi xóa.')
        if unlink:
            await crud.unlink_character_from_project(pid, cid)
        else:
            await crud.delete_character(cid)
            # Only the reference cache named by this character UUID is owned.
            key = speech_store.identifier(cid)
            for extension in ('.png', '.jpg', '.webp'):
                try:
                    (OUTPUT_DIR/'character_refs'/f'{key}{extension}').unlink(missing_ok=True)
                except OSError:
                    logger.warning('Could not remove reference cache for %s', key)
        return {'ok': True}
