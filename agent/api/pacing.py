"""Analyze first, review cuts, then export an immutable pacing version."""
import asyncio
from pathlib import Path
import tempfile
import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

from agent.api.editing import local_source, video
from agent.api.speech import ensure_idle
from agent.models.pacing import AnalyzeRequest, PacingDraft, RenderRequest, SaveRequest
from agent.services import editing, media_jobs, media_library, pacing
from agent.services.speech_store import atomic_json, load_document
from agent.services.video_assembly import render_video_to_file, video_export_lock

router = APIRouter(prefix='/videos', tags=['pacing'])
_library_lock = asyncio.Lock()


def original_path(vid):
    path = local_source(vid)
    return path if path.is_file() else pacing.directory(vid) / 'source.mp4'


def source_path(vid, source):
    if source == 'original':
        path = original_path(vid)
    else:
        state = editing.load(vid)
        if not any(e.get('export_id') == source for e in state['exports']):
            raise ValueError('Bản xuất đã chọn không còn trong danh sách.')
        path = editing.directory(vid) / 'exports' / f'{UUID(source)}.mp4'
    if not path.is_file():
        raise ValueError('Ghép video nguồn trước, hoặc chọn một bản xuất đã hoàn tất.')
    return path


@router.get('/{vid}/pacing')
async def state(vid: UUID):
    item = await video(vid)
    key = str(vid)
    data = pacing.load(key)
    async with _library_lock:
        assets = await asyncio.to_thread(pacing.library, key)
    records = media_library.audio_records(item['project_id'])
    assets = [a for a in assets if not media_library.audio_record(item['project_id'], pacing.asset_path(key, a['id']), records).get('trashed_at')]
    exports = [dict(id=e['export_id'], name=e.get('name') or f"Bản {e.get('width')} × {e.get('height')}",
                    url=e['url'], duration=e.get('duration')) for e in editing.load(key)['exports'] if e.get('export_id') and (editing.directory(key) / 'exports' / f"{e['export_id']}.mp4").is_file()]
    return {**data, 'plan': pacing.plan(key, data['draft'].get('plan_id')), 'assets': assets,
            'sources': [{'id': 'original', 'name': 'Video nguồn đang lưu', 'url': f'/api/videos/{vid}/pacing/source'}] + exports,
            'source_available': original_path(key).is_file(), 'speech_available': bool(load_document(key).cues)}


@router.get('/{vid}/pacing/source')
async def source_file(vid: UUID):
    await video(vid)
    path = original_path(str(vid))
    if not path.is_file():
        raise HTTPException(404, 'Nạp video nguồn trước khi xem.')
    return FileResponse(path, media_type='video/mp4')


@router.post('/{vid}/pacing/prepare', status_code=202)
async def prepare(vid: UUID):
    item = await video(vid)
    key = str(vid)
    ensure_idle(key)
    async def run(update):
        async with video_export_lock:
            if original_path(key).is_file():
                return
            directory = pacing.directory(key)
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f'.source-{uuid.uuid4()}.mp4'
            try:
                await render_video_to_file(key, target, item.get('orientation'),
                    progress_callback=lambda stage, value: update(stage='Nạp và ghép video nguồn', progress=round(5 + value * 85)))
                await pacing.probe(target)
                target.replace(directory / 'source.mp4')
            finally:
                target.unlink(missing_ok=True)
    return media_jobs.start(key, 'pacing-prepare', run)


def check_effects(item, effects):
    records = media_library.audio_records(item['project_id'])
    for event in effects:
        path = pacing.asset_path(item['id'], str(event.asset_id))
        if media_library.audio_record(item['project_id'], path, records).get('trashed_at'):
            raise ValueError('Hiệu ứng đang ở thùng rác. Khôi phục trước khi sử dụng.')
        pacing.asset_info(item['id'], str(event.asset_id))


@router.put('/{vid}/pacing', dependencies=[Depends(media_library.guard)])
async def save(vid: UUID, body: SaveRequest):
    item = await video(vid)
    key = str(vid)
    ensure_idle(key)
    current = pacing.load(key)
    if current['revision'] != body.revision:
        raise HTTPException(409, 'Lựa chọn đã đổi ở tab khác. Nạp lại rồi thử tiếp.')
    try:
        path = source_path(key, body.draft.source)
        pacing.selected_plan(key, body.draft, path)
        check_effects(item, body.draft.effects)
        result = {'revision': current['revision'] + 1, 'draft': body.draft.model_dump(mode='json')}
        atomic_json(pacing.directory(key) / 'draft.json', result)
        return result
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/{vid}/pacing/analyze', status_code=202)
async def analyze(vid: UUID, body: AnalyzeRequest):
    await video(vid)
    key = str(vid)
    ensure_idle(key)
    try:
        path = source_path(key, body.source)
        before = pacing.fingerprint(path)
        revision = pacing.load(key)['revision']
        doc = load_document(key)
        if body.method == 'speech' and (body.source != 'original' or not doc.cues):
            raise ValueError('Cách này cần lời thoại có mốc thời gian đã lưu và video nguồn gốc.')

        async def run(update):
            info = await pacing.probe(path)
            update(progress=5, stage='Đọc âm thanh và tìm khoảng nghỉ')
            if body.method == 'speech':
                if max(c.end for c in doc.cues) > info['duration'] + .1:
                    raise ValueError('Mốc lời thoại vượt video nguồn. Kiểm tra lại bản SRT.')
                intervals = pacing.speech_gaps(doc.cues, info['duration'])
            else:
                if not info['has_audio']:
                    raise ValueError('Video không có âm thanh để phân tích khoảng lặng.')
                line, finish = pacing.silence_reader(info['duration'])
                def on_line(text):
                    line(text)
                    if text.startswith('out_time_us='):
                        try:
                            update(progress=round(5 + 85 * min(1, float(text.split('=')[1]) / 1e6 / info['duration'])))
                        except ValueError:
                            pass
                await media_jobs.process(['ffmpeg', '-hide_banner', '-nostdin', '-i', str(path), '-vn', '-map', '0:a:0',
                    '-af', f'silencedetect=noise={body.threshold}dB:d={body.minimum}', '-progress', 'pipe:1', '-f', 'null', '-'],
                    timeout=max(180, info['duration'] * 2), on_line=on_line)
                intervals = finish()
            cuts = pacing.candidates(intervals, info['duration'], body.minimum, body.padding)
            warnings = []
            if sum(c['end'] - c['start'] for c in cuts) >= info['duration'] * .95:
                warnings.append('Gần toàn bộ video nằm dưới ngưỡng. Chưa chọn cắt tự động; hãy giảm độ nhạy hoặc kiểm tra âm thanh.')
            if not cuts:
                warnings.append('Không tìm thấy khoảng nghỉ phù hợp. Nhạc nền có thể che khoảng lặng; thử mốc lời thoại đã lưu hoặc giảm độ dài nghỉ tối thiểu.')
            if pacing.fingerprint(path) != before or pacing.load(key)['revision'] != revision:
                raise ValueError('Video hoặc lựa chọn đã đổi trong lúc phân tích. Hãy chạy lại.')
            plan_id = str(uuid.uuid4())
            plan = {**body.model_dump(), **info, 'id': plan_id, 'fingerprint': before, 'cuts': cuts, 'warnings': warnings,
                    'speech_revision': doc.revision if body.method == 'speech' else None}
            atomic_json(pacing.directory(key) / 'plans' / f'{plan_id}.json', plan)
            draft = PacingDraft(source=body.source, plan_id=plan_id, cut_ids=[] if warnings else [c['id'] for c in cuts])
            atomic_json(pacing.directory(key) / 'draft.json', {'revision': revision + 1, 'draft': draft.model_dump(mode='json')})
            update(progress=96, stage='Lưu các đoạn đề xuất', result={'plan_id': plan_id})
        return media_jobs.start(key, 'pacing-analysis', run)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/{vid}/pacing/render', status_code=202, dependencies=[Depends(media_library.guard)])
async def render(vid: UUID, body: RenderRequest):
    item = await video(vid)
    key = str(vid)
    ensure_idle(key)
    current = pacing.load(key)
    if current['revision'] != body.revision:
        raise HTTPException(409, 'Lựa chọn chưa khớp bản đã lưu. Nạp lại rồi thử tiếp.')
    try:
        draft = PacingDraft.model_validate(current['draft'])
        check_effects(item, draft.effects)
        path = source_path(key, draft.source)
        pacing.selected_plan(key, draft, path)
        export_id = str(uuid.uuid4())
        target = ((pacing.directory(key) / 'previews') if body.preview else (editing.directory(key) / 'exports')) / f'{export_id}.mp4'
        url = f'/api/videos/{vid}/pacing/previews/{export_id}' if body.preview else f'/api/videos/{vid}/editing/exports/{export_id}'
        async def run(update):
            committed = False
            try:
                async with video_export_lock:
                    update(progress=4, stage='Kiểm tra đoạn cắt và mốc hiệu ứng')
                    result = await pacing.render(key, path, draft, target, update, preview=body.preview)
                    result.update(export_id=export_id, url=url, bytes=target.stat().st_size,
                                  revision=current['revision'], operation='pacing', pacing=current['draft'],
                                  name=f"Nhịp dựng · {result['cut_count']} đoạn cắt · {result['effects_count']} hiệu ứng")
                    if not body.preview:
                        editing.record_export(key, result)
                    committed = True
                    update(progress=99, stage='Đã lưu bản dựng', result=result)
            finally:
                if not committed:
                    target.unlink(missing_ok=True)
        return media_jobs.start(key, 'pacing-preview' if body.preview else 'pacing-export', run)
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/{vid}/pacing/job')
async def job(vid: UUID):
    await video(vid)
    return media_jobs.latest(str(vid))


@router.post('/{vid}/pacing/jobs/{job_id}/cancel')
async def cancel(vid: UUID, job_id: UUID):
    await video(vid)
    current = media_jobs.latest(str(vid))
    if not current or current['id'] != str(job_id) or not current['kind'].startswith('pacing-'):
        raise HTTPException(409, 'Tác vụ đã đổi. Nạp lại trước khi dừng.')
    return await media_jobs.cancel(str(vid))


@router.get('/{vid}/pacing/previews/{preview_id}')
async def preview_file(vid: UUID, preview_id: UUID):
    await video(vid)
    path = pacing.directory(str(vid)) / 'previews' / f'{preview_id}.mp4'
    if not path.is_file():
        raise HTTPException(404, 'Đoạn xem thử không còn trên máy.')
    return FileResponse(path, media_type='video/mp4')


@router.post('/{vid}/pacing/assets', status_code=201)
async def upload(vid: UUID, file: UploadFile):
    await video(vid)
    key = str(vid)
    asset_id = str(uuid.uuid4())
    target = pacing.asset_path(key, asset_id)
    committed = False
    try:
        if Path(file.filename or '').suffix.lower() not in {'.mp3', '.wav', '.m4a', '.ogg', '.flac', '.aac'}:
            raise ValueError('Chọn MP3, WAV, M4A, OGG, FLAC hoặc AAC.')
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='sfx-', dir=pacing.directory(key)) as work:
            source = Path(work) / 'audio'
            count = 0
            with source.open('wb') as stream:
                while raw := await file.read(1024 * 1024):
                    count += len(raw)
                    if count > 16 * 1024 * 1024:
                        raise HTTPException(413, 'Hiệu ứng tối đa 16 MB / 30 giây.')
                    stream.write(raw)
            info = await pacing.probe(source)
            if not info['has_audio'] or info['width'] or info['duration'] > 30:
                raise ValueError('Hiệu ứng cần là file âm thanh có độ dài tối đa 30 giây.')
            await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-vn', '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le', str(target)], timeout=60)
            result = {'id': asset_id, 'name': Path(file.filename).name[:120], 'duration': info['duration'], 'builtin': False,
                      'url': f'/api/videos/{vid}/pacing/assets/{asset_id}'}
            atomic_json(target.with_suffix('.json'), result)
            committed = True
            return result
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        if not committed:
            target.unlink(missing_ok=True)
            target.with_suffix('.json').unlink(missing_ok=True)
        await file.close()


@router.get('/{vid}/pacing/assets/{asset_id}')
async def audio_file(vid: UUID, asset_id: UUID):
    item = await video(vid)
    from agent.services.media_library import audio_record
    if audio_record(item['project_id'], pacing.asset_path(str(vid), str(asset_id))).get('trashed_at'):
        raise HTTPException(404, 'Hiệu ứng đang ở thùng rác.')
    try:
        pacing.asset_info(str(vid), str(asset_id))
        return FileResponse(pacing.asset_path(str(vid), str(asset_id)), media_type='audio/wav')
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
