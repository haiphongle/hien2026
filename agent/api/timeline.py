"""Timeline edits and exports never replace imported sources or scene scripts."""
import asyncio
import json
from io import BytesIO
from pathlib import Path
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from agent.api.editing import video
from agent.api.pacing import original_path
from agent.api.speech import ensure_idle
from agent.config import OUTPUT_DIR
from agent.db import crud
from agent.models.timeline import CaptionPreview, ImportResults, RenderTimeline, SaveTimeline, TimelineDocument, VisualizeAssets, WaveformAssets
from agent.models.editing import TextStyle
from agent.services import editing, media_jobs, media_library, pacing, speech, speech_store, timeline, timeline_bridge, timeline_layers
from agent.services.music_library import MUSIC_FILENAME, resolve_music_file
from agent.services.speech_store import read_json
from agent.services.video_assembly import video_export_lock
from agent.services.video_assembly import assembled_path
from agent.utils.slugify import slugify

router = APIRouter(prefix='/videos', tags=['timeline'])
_library_lock = asyncio.Lock()


async def catalog(item):
    key = item['id']
    project = await crud.get_project(item['project_id'])
    records = []
    from agent.services.stickman import editor_records
    records.extend(editor_records(key))
    for row in await crud.list_videos(item['project_id']):
        records.append((row['id'], 'original', row['title'], original_path(row['id']), 'source'))
    mixed = assembled_path(key)
    if mixed.is_file() and mixed != original_path(key):
        records.append((key, 'mixed', 'Video đã ghép giọng / nhạc', mixed, 'export'))
    for result in editing.load(key)['exports']:
        if result.get('export_id'):
            records.append((key, result['export_id'], result.get('name') or 'Bản xuất',
                            editing.directory(key) / 'exports' / f"{result['export_id']}.mp4", 'export'))
    from agent.services.propainter_trial import results as restoration_results
    for result in restoration_results(key):
        rid = result['trial_id']
        records.append((key, f'propainter:{rid}', f"Xóa vật thể AI · {result['start']:.1f}s · {rid[:8]}",
                        editing.directory(key) / 'propainter' / f'{rid}.mp4', 'export'))
    async with _library_lock:
        sounds = await asyncio.to_thread(pacing.library, key)
    library_records = media_library.audio_records(item['project_id'])
    library_count = len(library_records)
    for sound in sounds:
        if media_library.audio_record(item['project_id'], pacing.asset_path(key, sound['id']), library_records).get('trashed_at'):
            continue
        records.append((key, sound['id'], sound['name'], pacing.asset_path(key, sound['id']), 'sfx'))
    for cue in speech_store.load_document(key).cues:
        if speech.cue_status(key, cue)['status'] == 'READY':
            path, _, _, _ = speech.cue_audio(key, cue)
            records.append((key, f'voice:{cue.id}', cue.text[:60], path, 'voice'))
    music_dir = OUTPUT_DIR / slugify(project['name']) / 'music'
    # The timeline library exposes all project music, not just the track selected
    # in the older mixing panel. Imports stay available after changing/reloading.
    for entry in sorted(music_dir.glob('*'), key=lambda p: p.name):
        if not MUSIC_FILENAME.fullmatch(entry.name):
            continue
        try:
            path = resolve_music_file(music_dir, entry.name)
            records.append((key, f'music:{entry.name}', entry.name, path, 'music'))
        except (ValueError, FileNotFoundError):
            continue
    managed = {}
    named_records = []
    for owner, ref, name, path, category in records:
        if category == 'music' or category == 'sfx' and not read_json(path.with_suffix('.json'), {}).get('builtin'):
            record = media_library.remember_audio(project, owner, path, name, category, library_records)
            if record.get('trashed_at'):
                continue
            name = record['name']
            managed[(owner, ref)] = record['id']
        named_records.append((owner, ref, name, path, category))
    if len(library_records) != library_count:
        speech_store.atomic_json(media_library.manifest(item['project_id']), library_records)
    results = await asyncio.gather(*(timeline.register(key, *record) for record in named_records), return_exceptions=True)
    assets, warnings = [], []
    for result in results:
        if isinstance(result, Exception):
            warnings.append(str(result))
        elif result:
            result['management_id'] = result['owner'] if result['ref'] == 'original' else managed.get((result['owner'], result['ref']))
            assets.append(result)
    assets.sort(key=lambda a: (0 if a['owner'] == key and a['ref'] == 'original' else 1 if a['category'] == 'export' else 2 if a['kind'] == 'video' else 3))
    return assets, warnings


@router.get('/{vid}/timeline', dependencies=[Depends(media_library.guard)])
async def state(vid: UUID):
    item = await video(vid)
    key = str(vid)
    assets, warnings = await catalog(item)
    source = next((a for a in assets if a['owner'] == key and a['ref'] == 'original'), None)
    saved = timeline.initialize(key, source)
    # Keep missing/replaced sources visible, rather than silently deleting decisions.
    known = {a['id'] for a in assets}
    for clip in saved['document']['clips']:
        if clip['asset_id'] and clip['asset_id'] not in known:
            data = read_json(timeline.asset_file(key, clip['asset_id']))
            if data:
                try:
                    await checked_asset(vid, UUID(data['id']))
                    missing = False
                except HTTPException:
                    missing = True
                assets.append({**timeline.public_asset(key, data), 'missing': missing})
                known.add(data['id'])
    return {**saved, 'assets': assets, 'warnings': warnings, 'source_available': source is not None,
            'fonts': editing.fonts(), 'legacy_available': await timeline_bridge.has_legacy(key),
            'exports': [e for e in editing.load(key)['exports'] if e.get('operation') == 'timeline']}


async def checked_asset(vid, aid):
    item = await video(vid)
    try:
        data = timeline.asset(str(vid), str(aid))
        if data['category'] == 'music':
            # Music belongs to the project, even if the video that originally
            # registered it has since been deleted.
            project = await crud.get_project(item['project_id'])
            media_library.allowed_path(project, data['owner'], Path(data['path']), 'music')
        else:
            owner = await crud.get_video(data['owner'])
            if not owner or owner['project_id'] != item['project_id']:
                raise ValueError('Tư liệu không thuộc dự án này.')
        if data['category'] in {'music', 'sfx'} and media_library.audio_record(item['project_id'], Path(data['path'])).get('trashed_at'):
            raise ValueError('Tư liệu đang ở thùng rác. Khôi phục trước khi sử dụng.')
        return data
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/{vid}/timeline/assets/{aid}/file')
async def asset_file(vid: UUID, aid: UUID):
    data = await checked_asset(vid, aid)
    return FileResponse(data['path'], media_type='video/mp4' if data['kind'] == 'video' else None)


@router.get('/{vid}/timeline/assets/{aid}/visuals')
async def asset_visuals(vid: UUID, aid: UUID):
    await checked_asset(vid, aid)
    return read_json(timeline.directory(str(vid)) / 'visuals' / str(aid) / 'summary.json')


@router.get('/{vid}/timeline/assets/{aid}/sprite')
async def asset_sprite(vid: UUID, aid: UUID):
    await checked_asset(vid, aid)
    path = timeline.directory(str(vid)) / 'visuals' / str(aid) / 'sprite.jpg'
    if not path.is_file():
        raise HTTPException(404, 'Chưa tạo dải ảnh video.')
    return FileResponse(path, media_type='image/jpeg')


@router.put('/{vid}/timeline')
async def save(vid: UUID, body: SaveTimeline):
    async with media_library.lock:
        await video(vid)
        for aid in {c.asset_id for c in body.document.clips if c.asset_id}:
            await checked_asset(vid, aid)
        try:
            return timeline.save(str(vid), body.revision, body.document)
        except ValueError as exc:
            raise HTTPException(409 if 'tab khác' in str(exc) else 422, str(exc)) from exc


@router.post('/{vid}/timeline/visuals', status_code=202, dependencies=[Depends(media_library.guard)])
async def visualize(vid: UUID, body: WaveformAssets):
    await video(vid)
    key = str(vid)
    ensure_idle(key)
    ids = list(dict.fromkeys(str(a) for a in body.asset_ids))
    for aid in ids:
        await checked_asset(vid, UUID(aid))
    async def run(update):
        for index, aid in enumerate(ids):
            def progress(index=index, **changes):
                changes['progress'] = round((index * 100 + changes.get('progress', 0)) / len(ids))
                update(**changes)
            await timeline.visualize(key, aid, progress)
        update(result={'asset_ids': ids}, progress=99)
    return media_jobs.start(key, 'timeline-visuals', run)


@router.post('/{vid}/timeline/render', status_code=202, dependencies=[Depends(media_library.guard)])
async def render(vid: UUID, body: RenderTimeline):
    await video(vid)
    key = str(vid)
    ensure_idle(key)
    saved = timeline.load(key)
    if not saved or saved['revision'] != body.revision:
        raise HTTPException(409, 'Lưu bản dựng trước khi xuất. Nạp lại nếu bản đã đổi ở tab khác.')
    document = TimelineDocument.model_validate(saved['document'])
    source_document = document
    for aid in {c.asset_id for c in document.clips if c.asset_id}:
        await checked_asset(vid, aid)
    try:
        timeline.validate_assets(key, document)
        document = timeline_layers.range_document(document, body.start, body.end)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    eid = str(uuid.uuid4())
    target = (timeline.directory(key) / 'previews' if body.preview else editing.directory(key) / 'exports') / f'{eid}.mp4'
    url = f'/api/videos/{vid}/timeline/previews/{eid}' if body.preview else f'/api/videos/{vid}/editing/exports/{eid}'
    async def run(update):
        committed = False
        try:
            async with video_export_lock:
                result = await timeline.render(key, document, target, update, preview=body.preview,
                    source_document=source_document, range_start=round(body.start*document.fps)/document.fps)
                result.update(export_id=eid, url=url, bytes=target.stat().st_size, revision=saved['revision'],
                              operation='timeline', timeline=saved['document'],
                              range_start=body.start, range_end=body.end,
                              name=f"Timeline · {result['clip_count']} đoạn · bản {saved['revision']}")
                if not body.preview:
                    editing.record_export(key, result)
                committed = True
                update(progress=99, stage='Đã lưu phiên bản', result=result)
        finally:
            if not committed:
                target.unlink(missing_ok=True)
    return media_jobs.start(key, 'timeline-preview' if body.preview else 'timeline-export', run)


@router.post('/{vid}/timeline/import-results', dependencies=[Depends(media_library.guard)])
async def import_results(vid: UUID, body: ImportResults):
    await video(vid)
    ensure_idle(str(vid))
    saved = timeline.load(str(vid))
    if not saved or saved['revision'] != body.revision:
        raise HTTPException(409, 'Lưu bản dựng trước khi nạp kết quả.')
    try:
        report = {}
        document, warnings, added = await timeline_bridge.import_results(str(vid), TimelineDocument.model_validate(saved['document']),
            captions=body.captions, voices=body.voices, effects=body.effects, legacy=body.legacy, source=body.source, report=report)
        assets = [timeline.public_asset(str(vid), await checked_asset(vid, UUID(aid))) for aid in
                  {c['asset_id'] for c in document['clips'] if c['asset_id']}]
        return {'document': document, 'assets': assets, 'warnings': warnings, 'added': added, 'report': report}
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/{vid}/timeline/caption-preview')
async def caption_preview(vid: UUID, body: CaptionPreview):
    await video(vid)
    try:
        stream = BytesIO()
        bounds = await asyncio.to_thread(timeline_layers.caption_image, body.text, body.width, body.height, body.caption, stream)
        return Response(stream.getvalue(), media_type='image/png', headers={
            'X-Text-Bounds': json.dumps(bounds), 'Access-Control-Expose-Headers': 'X-Text-Bounds', 'Cache-Control': 'no-store'})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/{vid}/timeline/fonts/{font_id}/preview')
async def font_preview(vid: UUID, font_id: str):
    await video(vid)
    try:
        stream = BytesIO()
        style = TextStyle(font_id=font_id, size=30, position='middle', background_opacity=0, outline=0)
        await asyncio.to_thread(editing.caption_image, '中文 · 日本語 · 한국어' if font_id == 'cjk' else 'Tiếng Việt', 192, 72, style, stream)
        return Response(stream.getvalue(), media_type='image/png', headers={'Cache-Control': 'private, max-age=86400'})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/{vid}/timeline/proxies', status_code=202, dependencies=[Depends(media_library.guard)])
async def proxies(vid: UUID, body: VisualizeAssets):
    await video(vid)
    key = str(vid); ensure_idle(key)
    ids = list(dict.fromkeys(str(a) for a in body.asset_ids))
    for aid in ids:
        await checked_asset(vid, UUID(aid))
    async def run(update):
        for index, aid in enumerate(ids):
            def progress(index=index, **changes):
                changes['progress'] = round((index*100+changes.get('progress', 0))/len(ids))
                update(**changes)
            await timeline.proxy(key, aid, progress)
        update(result={'asset_ids': ids}, progress=99)
    return media_jobs.start(key, 'timeline-proxies', run)


@router.get('/{vid}/timeline/assets/{aid}/proxy')
async def proxy_file(vid: UUID, aid: UUID):
    await checked_asset(vid, aid)
    path = timeline.directory(str(vid)) / 'proxies' / f'{aid}.mp4'
    if not path.is_file():
        raise HTTPException(404, 'Bản xem nhẹ chưa được tạo.')
    return FileResponse(path, media_type='video/mp4')


@router.post('/{vid}/timeline/exports/{eid}/handoff', dependencies=[Depends(media_library.guard)])
async def handoff(vid: UUID, eid: UUID):
    await video(vid)
    ensure_idle(str(vid))
    try:
        return await timeline_bridge.handoff(str(vid), str(eid))
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/{vid}/timeline/job')
async def job(vid: UUID):
    await video(vid)
    return media_jobs.latest(str(vid))


@router.post('/{vid}/timeline/jobs/{jid}/cancel')
async def cancel(vid: UUID, jid: UUID):
    await video(vid)
    current = media_jobs.latest(str(vid))
    if not current or current['id'] != str(jid) or not current['kind'].startswith('timeline-'):
        raise HTTPException(409, 'Tác vụ đang chạy đã thay đổi.')
    return await media_jobs.cancel(str(vid))


@router.get('/{vid}/timeline/previews/{eid}')
async def preview(vid: UUID, eid: UUID):
    await video(vid)
    path = timeline.directory(str(vid)) / 'previews' / f'{eid}.mp4'
    if not path.is_file():
        raise HTTPException(404, 'Bản xem thử không còn trên máy.')
    return FileResponse(path, media_type='video/mp4')
