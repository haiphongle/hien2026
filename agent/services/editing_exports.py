"""Delete completed exports without touching sources or edit decisions."""
import asyncio
from contextlib import suppress
from pathlib import Path
import shutil

from fastapi import HTTPException

from agent.db import crud
from agent.services import editing, media_library, timeline
from agent.services.speech_store import atomic_json, identifier, read_json
from agent.services.video_assembly import video_export_lock


def export_id(entry):
    return entry.get('export_id') or entry.get('url', '').rsplit('/', 1)[-1]


def files(vid, ids):
    paths = {editing.directory(vid) / 'exports' / f'{identifier(eid)}.mp4' for eid in ids}
    caches = []
    for manifest in timeline.ROOT.glob('*/assets/*.json'):
        data = read_json(manifest, {})
        if Path(data.get('path', '')) not in paths:
            continue
        aid = identifier(manifest.stem)
        root = timeline.directory(identifier(manifest.parent.parent.name))
        caches.extend([root / 'proxies' / f'{aid}.mp4', root / 'visuals' / aid, manifest])
    targets = [*paths, *caches]
    # Validate every target before removing any file; never follow symlinks.
    for path in targets:
        root = editing.ROOT if path in paths else timeline.ROOT
        if path.is_symlink() or path.resolve() != root.resolve() / path.relative_to(root):
            raise ValueError('Không xóa file liên kết tới bên ngoài thư mục FrameWeaver.')
    return paths, targets


def finish_delete(vid, state):
    """Resume a committed deletion after an I/O failure or server restart."""
    marker = editing.directory(vid) / 'deleting-exports.json'
    ids = {identifier(eid) for eid in read_json(marker)['export_ids']}
    _, targets = files(vid, ids)
    for path in targets:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
    state['exports'] = [e for e in state['exports'] if export_id(e) not in ids]
    if state.get('last_export') and export_id(state['last_export']) in ids:
        state['last_export'] = next(iter(state['exports']), None)
    atomic_json(editing.directory(vid) / 'settings.json', state)
    with suppress(OSError):
        marker.unlink(missing_ok=True)  # Repeating this cleanup is safe.


def inspect(vid, ids, project, videos, state):
    entries = {export_id(e): e for e in state['exports']}
    if any(eid not in entries for eid in ids):
        raise HTTPException(404, 'Bản xuất đã bị xóa hoặc không thuộc video này. Nạp lại lịch sử.')
    files(vid, ids)
    items = []
    for eid in ids:
        path = editing.directory(vid) / 'exports' / f'{eid}.mp4'
        uses = media_library.usages(project, dict(kind='export', path=str(path.absolute())), videos)
        items.append(dict(export_id=eid, name=entries[eid].get('name') or 'Bản xuất',
                          size_bytes=path.stat().st_size if path.is_file() else 0, uses=uses))
    return dict(items=items, size_bytes=sum(e['size_bytes'] for e in items))


def busy_reason(videos):
    from agent.api.speech import ensure_idle
    try:
        for row in videos:
            ensure_idle(row['id'])
    except HTTPException:
        return 'Dự án đang xử lý tư liệu. Đợi hoàn tất hoặc dừng tác vụ trước khi xóa bản xuất.'
    if video_export_lock.locked():
        return 'Đang ghép hoặc xuất video. Đợi hoàn tất trước khi xóa bản xuất.'
    return None


async def manage(item, ids, *, delete=False):
    # Shared with timeline save/catalog/render setup and media deletion.
    async with media_library.lock:
        project = await crud.get_project(item['project_id'])
        videos = await crud.list_videos(item['project_id'], include_trashed=True)
        ids = list(dict.fromkeys(identifier(eid) for eid in ids))
        try:
            state = editing.load(item['id'])
            report = await asyncio.to_thread(inspect, item['id'], ids, project, videos, state)
            report['blocked_reason'] = busy_reason(videos)
            if not delete:
                return report
            if report['blocked_reason']:
                raise HTTPException(409, report['blocked_reason'])
            used = [e for e in report['items'] if e['uses']]
            if used:
                raise HTTPException(409, 'Bản xuất đang được dùng: ' + '; '.join(
                    f"{e['name']} → {', '.join(u['name'] for u in e['uses'])}" for e in used)
                    + '. Gỡ khỏi timeline và lưu trước khi xóa.')
            # No await between validation, commit marker and cleanup. A crash
            # resumes through editing.load, preserving settings/revision.
            state = editing.load(item['id'])
            atomic_json(editing.directory(item['id']) / 'deleting-exports.json', {'export_ids': ids})
            finish_delete(item['id'], state)
            return dict(deleted=ids, freed_bytes=report['size_bytes'])
        except (OSError, ValueError) as exc:
            raise HTTPException(409, 'Chưa hoàn tất xóa bản xuất. Giữ nguyên video nguồn; thử lại hoặc nạp lại lịch sử. ' + str(exc)) from exc
