"""Names and recoverable deletion for local library media; never rename sources."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
import shutil
import uuid

from fastapi import HTTPException

from agent.config import OUTPUT_DIR, SHARED_OUTPUT_DIR
from agent.db import crud
from agent.services import audio_mix_jobs, editing, media_jobs, pacing, speech_store, timeline
from agent.services.speech_store import atomic_json, identifier, read_json
from agent.services.video_assembly import assembled_path, video_export_lock
from agent.utils.slugify import slugify

ROOT = SHARED_OUTPUT_DIR / 'media_library'
lock = asyncio.Lock()


async def guard():
    # Serialize request setup with deletion, never hold this during an export.
    async with lock:
        yield


def manifest(pid):
    return ROOT / f'{identifier(pid)}.json'


def audio_id(pid, path):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f'flowkit:library:{identifier(pid)}:{path.absolute()}'))


def audio_records(pid):
    try:
        path = manifest(pid)
    except ValueError:
        return {}  # Older music APIs also accept the shared/non-UUID namespace.
    try:
        records = read_json(path, {})
        if not isinstance(records, dict) or any(not isinstance(v, dict) for v in records.values()):
            raise ValueError('Invalid library manifest')
        return records
    except (OSError, ValueError) as exc:
        raise HTTPException(409, 'Không đọc được dữ liệu thư viện. Giữ nguyên file; kiểm tra lại dữ liệu trước khi thao tác.') from exc


def audio_record(pid, path, records=None):
    records = audio_records(pid) if records is None else records
    return records.get(audio_id(pid, path), {}) if records else {}


def remember_audio(project, owner, path, name, category, records=None):
    allowed_path(project, owner, path, category)
    persist = records is None
    records = audio_records(project['id']) if persist else records
    key = audio_id(project['id'], path)
    if key not in records:
        records[key] = dict(id=key, kind='audio', owner=owner, path=str(path.absolute()),
                            name=name[:120], category=category, trashed_at=None)
        if persist:
            atomic_json(manifest(project['id']), records)
    return records[key]


def allowed_path(project, owner, path, category):
    root = (OUTPUT_DIR / slugify(project['name']) / 'music' if category == 'music'
            else pacing.directory(owner) / 'assets')
    if (path.is_symlink() or path.parent.resolve() != root.resolve()
            or not root.resolve().is_relative_to(OUTPUT_DIR.resolve())):
        raise HTTPException(422, 'Tư liệu phải nằm trong thư mục do FrameWeaver quản lý.')


async def item(pid, key):
    project = await crud.get_project(pid)
    if not project:
        raise HTTPException(404, 'Không tìm thấy dự án.')
    row = await crud.get_video(key, include_trashed=True)
    if row and row['project_id'] == pid:
        return project, dict(id=key, kind='video', name=row['title'], trashed_at=row['trashed_at'])
    record = audio_records(pid).get(key)
    if not record:
        raise HTTPException(404, 'Không tìm thấy tư liệu trong dự án.')
    if record['category'] != 'music':
        owner = await crud.get_video(record['owner'], include_trashed=True)
        if not owner or owner['project_id'] != pid:
            raise HTTPException(404, 'Không tìm thấy video sở hữu tư liệu.')
    allowed_path(project, record['owner'], Path(record['path']), record['category'])
    return project, record


def video_paths(project, key):
    # Shared voices/music and the user's original upload location are not owned
    # by this video. Only its UUID directories and assembled MP4 are removed.
    return [media_jobs.directory(key), timeline.directory(key), pacing.directory(key),
            editing.directory(key), speech_store.video_dir(key), audio_mix_jobs.ROOT / key,
            assembled_path(key), OUTPUT_DIR / slugify(project['name']) / 'audio_mix' / f'{key}.json']


def size(paths):
    total = 0
    for path in paths:
        if path.is_symlink():
            continue
        files = path.rglob('*') if path.is_dir() else [path]
        for file in files:
            try:
                if file.is_file() and not file.is_symlink():
                    total += file.stat().st_size
            except FileNotFoundError:
                pass  # A preview/cache may finish or be removed while listing.
    return total


def usages(project, entry, videos):
    used = []
    for row in videos:
        key = row['id']
        if entry['kind'] == 'video' and key == entry['id']:
            continue  # Its own draft is archived/restored along with the video.
        saved = read_json(timeline.directory(key) / 'document.json', {})
        found = False
        assets = {clip['asset_id'] for clip in saved.get('document', {}).get('clips', []) if clip.get('asset_id')}
        for aid in assets:
            data = read_json(timeline.asset_file(key, aid), {})
            found = (data.get('owner') == entry['id'] and data.get('category') != 'music' if entry['kind'] == 'video'
                     else data.get('path') == entry['path'])
            if found:
                break
        if entry['kind'] == 'audio' and not found:
            if entry['category'] == 'music':
                old = read_json(OUTPUT_DIR / slugify(project['name']) / 'audio_mix' / f'{key}.json', {})
                found = (old.get('music') or {}).get('filename') == Path(entry['path']).name
            elif entry['owner'] == key:
                old = read_json(pacing.directory(key) / 'draft.json', {})
                found = any(str(effect.get('asset_id')) == Path(entry['path']).stem
                            for effect in old.get('draft', {}).get('effects', []))
        if found:
            used.append(dict(id=key, name=row['title'], trashed=bool(row['trashed_at'])))
    if entry['kind'] == 'audio' and entry['category'] == 'music':
        from agent.services import story_schedule
        used.extend(dict(id=item['id'], name=f"Kịch bản đã lưu · {item['title']}", trashed=False)
                    for item in story_schedule.entries(project['id'])
                    if item['status'] != 'COMPLETED' and item['settings'].get('music_filename') == Path(entry['path']).name)
    return used


def purge_marker(key):
    return ROOT / 'purging' / f'{identifier(key)}.json'


def summary(project, entry, videos):
    reason = None
    if purge_marker(entry['id']).exists():
        reason = 'Đã bắt đầu xóa file; chỉ có thể tiếp tục xóa vĩnh viễn.'
    elif entry['kind'] == 'audio':
        if not Path(entry['path']).is_file():
            reason = 'File âm thanh đã mất trên máy.'
        elif entry['category'] != 'music' and not any(v['id'] == entry['owner'] and not v['trashed_at'] for v in videos):
            reason = 'Khôi phục video sở hữu tư liệu trước.'
    paths = video_paths(project, entry['id']) if entry['kind'] == 'video' else [Path(entry['path'])]
    return {k: v for k, v in entry.items() if k != 'path'} | dict(
        size_bytes=size(paths), restore_available=reason is None, restore_reason=reason)


async def describe(pid, key):
    project, entry = await item(pid, key)
    videos = await crud.list_videos(pid, include_trashed=True)
    usage, info = await asyncio.gather(asyncio.to_thread(usages, project, entry, videos),
                                     asyncio.to_thread(summary, project, entry, videos))
    return {**info, 'uses': usage}


async def trash_list(pid):
    project = await crud.get_project(pid)
    if not project:
        raise HTTPException(404, 'Không tìm thấy dự án.')
    videos = await crud.list_videos(pid, include_trashed=True)
    entries = [dict(id=v['id'], kind='video', name=v['title'], trashed_at=v['trashed_at']) for v in videos if v['trashed_at']]
    entries.extend(v for v in audio_records(pid).values() if v.get('trashed_at'))
    # Usage scans run when opening an item's confirmation, not once per trash row.
    return await asyncio.to_thread(lambda: [summary(project, entry, videos) for entry in entries])


async def change(pid, key, action, name=''):
    async with lock:
        project, entry = await item(pid, key)
        if action == 'rename':
            name = name.strip()
            if not name or len(name) > 120 or any(ord(c) < 32 for c in name):
                raise HTTPException(422, 'Tên phải có 1–120 ký tự, không chứa ký tự điều khiển.')
            entry['name'] = name
        elif action == 'restore':
            if purge_marker(key).exists():
                raise HTTPException(409, 'Đã bắt đầu xóa file; không thể khôi phục. Tiếp tục xóa vĩnh viễn để hoàn tất.')
            if entry['kind'] == 'audio' and not Path(entry['path']).is_file():
                raise HTTPException(409, 'File âm thanh đã mất trên máy, không thể khôi phục.')
            if entry['kind'] == 'audio' and entry['category'] != 'music' and not await crud.get_video(entry['owner']):
                raise HTTPException(409, 'Khôi phục video sở hữu tư liệu trước.')
            entry['trashed_at'] = None
        else:
            if action == 'purge' and (not entry['trashed_at'] or name != entry['name']):
                raise HTTPException(409, 'Chuyển vào thùng rác và nhập đúng tên trước khi xóa vĩnh viễn.')
            videos = await crud.list_videos(pid, include_trashed=True)
            used = await asyncio.to_thread(usages, project, entry, videos)
            if used:
                raise HTTPException(409, 'Tư liệu đang được dùng: ' + ', '.join(v['name'] for v in used) + '. Gỡ khỏi bản dựng hoặc kịch bản đã lưu trước khi xóa.')
            if action == 'purge' and entry['kind'] == 'audio' and entry['category'] == 'music':
                # Legacy project folders use names, which are not unique.
                shared = [p['name'] for p in await crud.list_projects()
                          if p['id'] != pid and slugify(p['name']) == slugify(project['name'])]
                if shared:
                    raise HTTPException(409, 'Thư mục nhạc còn dùng chung với dự án: ' + ', '.join(shared) + '. Chưa xóa file để giữ tư liệu của dự án đó.')
            requests = await crud.list_requests(project_id=pid)
            if any(r['status'] in {'PENDING', 'PROCESSING'} for r in requests):
                raise HTTPException(409, 'Dự án đang tạo nội dung. Đợi hoàn tất trước khi xóa tư liệu.')
            from agent.api.speech import ensure_idle
            for row in videos:
                ensure_idle(row['id'])
            if video_export_lock.locked():
                raise HTTPException(409, 'Đang ghép hoặc xuất video. Đợi hoàn tất trước khi xóa tư liệu.')
            entry['trashed_at'] = entry['trashed_at'] or datetime.now(timezone.utc).isoformat()
            if action == 'purge':
                records = audio_records(pid)  # Check metadata before removing any file.
                paths = video_paths(project, key) if entry['kind'] == 'video' else [Path(entry['path'])]
                def remove():
                    # Validate every target before deleting any of them.
                    if any(p.is_symlink() or not p.resolve().is_relative_to(OUTPUT_DIR.resolve()) for p in paths):
                        raise OSError('Không xóa thư mục/file liên kết tới bên ngoài.')
                    atomic_json(purge_marker(key), {'project_id': pid})
                    for path in paths:
                        if path.is_dir():
                            shutil.rmtree(path)
                        else:
                            path.unlink(missing_ok=True)
                    if entry['kind'] == 'audio' and entry['category'] == 'sfx':
                        Path(entry['path']).with_suffix('.json').unlink(missing_ok=True)
                try:
                    task = asyncio.create_task(asyncio.to_thread(remove))
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        # A worker thread cannot be cancelled; keep the lock until
                        # it finishes so a retry cannot delete the same files twice.
                        await task
                        raise
                    if entry['kind'] == 'video':
                        records = {k: v for k, v in records.items() if v['category'] == 'music' or v['owner'] != key}
                    else:
                        records.pop(key, None)
                    atomic_json(manifest(pid), records)
                except OSError as exc:
                    raise HTTPException(409, 'Chưa xóa hết file. Tư liệu vẫn ở thùng rác; thử lại.') from exc
                if entry['kind'] == 'video':
                    await crud.delete_video(key)
                with suppress(OSError):
                    purge_marker(key).unlink(missing_ok=True)
                return dict(id=key, kind=entry['kind'], action=action)
        if entry['kind'] == 'video':
            await crud.update_video(key, title=entry['name'], trashed_at=entry['trashed_at'])
        else:
            records = audio_records(pid)
            records[key] = entry
            atomic_json(manifest(pid), records)
        return dict(id=key, kind=entry['kind'], action=action)
