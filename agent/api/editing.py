"""Saved output settings, exact layout stills and non-destructive video exports."""
import asyncio
import json
from io import BytesIO
import tempfile
import uuid
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from agent.config import OUTPUT_DIR
from agent.db import crud
from agent.models.editing import CaptionPreviewRequest, DeleteExportsRequest, EditingClipRequest, EditingExportRequest, EditingPreviewRequest, EditingSettings, ExportNameRequest, ProPainterClipsRequest, ProPainterTrialRequest, SaveEditingRequest
from agent.services import editing, imported_video
from agent.services.audio_mix import AudioMixError
from agent.services.audio_mix_jobs import ACTIVE, cancel_job, latest_job, start_job
from agent.services import media_jobs, media_library
from agent.services.speech_store import load_document, timeline_key
from agent.services.video_assembly import assembled_path
from agent.utils.slugify import slugify

router = APIRouter(prefix="/videos", tags=["editing"])
_preview_lock = asyncio.Lock()
_caption_slots = asyncio.Semaphore(2)


async def video(vid: UUID) -> dict:
    item = await crud.get_video(str(vid))
    if not item:
        raise HTTPException(404, "Video not found")
    return item


def local_source(vid: str) -> Path:
    source = imported_video.source_path(vid)
    return source if source.is_file() else assembled_path(vid)


async def audio_settings(item: dict) -> dict:
    project = await crud.get_project(item["project_id"])
    path = OUTPUT_DIR / slugify(project.get("name") or "unnamed_project") / "audio_mix" / f"{item['id']}.json"
    data = json.loads(path.read_text()) if path.is_file() else {}
    if not isinstance(data, dict):
        raise ValueError("Cấu hình âm thanh không hợp lệ.")
    return data


@router.get("/{vid}/editing")
async def get_settings(vid: UUID):
    item = await video(vid)
    try:
        state = editing.load(str(vid))
        layers = await audio_settings(item)
        doc = load_document(str(vid))
        source = local_source(str(vid))
        info = await asyncio.to_thread(editing.probe, source) if source.is_file() else None
        scenes = await imported_video.timeline(str(vid))
        return {**state, "fonts": editing.fonts(), "source": info, "preview_available": source.is_file(),
                "speech_cues": len(doc.cues), "scene_texts": sum(bool((s.get("narrator_text") or "").strip()) for s in scenes),
                "music": layers.get("music")}
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.put("/{vid}/editing")
async def save_settings(vid: UUID, body: SaveEditingRequest):
    await video(vid)
    try:
        return editing.save(str(vid), body.revision, body.settings)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{vid}/editing/preview")
async def preview(vid: UUID, body: EditingPreviewRequest):
    await video(vid)
    source = local_source(str(vid))
    if not source.is_file():
        raise HTTPException(409, "Ghép video nguồn trước để có ảnh xem thử bố cục.")
    try:
        editing.font_path(body.settings.captions.font_id)
        async with _preview_lock:
            with tempfile.TemporaryDirectory(prefix="frameweaver-layout-") as work:
                target = Path(work) / "preview.png"
                await asyncio.to_thread(editing.still_preview, source, target, body.settings, body.time, body.text, video_id=str(vid))
                return Response(target.read_bytes(), media_type="image/png", headers={"Cache-Control": "no-store"})
    except (ValueError, OSError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{vid}/editing/source")
async def preview_source(vid: UUID):
    await video(vid)
    source = local_source(str(vid))
    if not source.is_file():
        raise HTTPException(404, "Chưa có video nguồn để xem trước.")
    # Use the imported original / assembled source, never an editing export.
    return FileResponse(source, media_type="video/mp4")


@router.post("/{vid}/editing/caption-preview")
async def caption_preview(vid: UUID, body: CaptionPreviewRequest):
    await video(vid)
    try:
        async with _caption_slots:
            image = BytesIO()
            await asyncio.to_thread(editing.caption_image, body.text, body.width, body.height, body.style, image)
            return Response(image.getvalue(), media_type="image/png", headers={"Cache-Control": "no-store"})
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{vid}/editing/jobs", status_code=202)
async def export(vid: UUID, body: EditingExportRequest):
    item = await video(vid)
    key = str(vid)
    try:
        state = editing.load(key)
        if state["revision"] != body.revision:
            raise HTTPException(409, "Thiết lập đã đổi. Nạp lại trước khi xuất.")
        settings = state["settings"]
        editing.font_path(settings["captions"]["font_id"])
        layers = await audio_settings(item)
        scenes = await imported_video.timeline(key)
        narration = None
        if settings["mode"] != "original":
            narration = {"source_volume": settings["source_volume"], "narration_volume": settings["narration_volume"],
                         "burn_subtitles": settings["mode"] != "voice", "captions_only": settings["mode"] == "subtitles",
                         "speech_revision": None}
            if settings["voice_source"] == "speech":
                doc = load_document(key)
                if not doc.cues or doc.revision < 1:
                    raise HTTPException(409, "Lưu kịch bản ở Lời thoại / SRT trước, hoặc chọn lời thuyết minh theo cảnh.")
                narration["speech_revision"] = doc.revision
            elif not any((s.get("narrator_text") or "").strip() for s in scenes):
                raise HTTPException(409, "Chưa có lời thuyết minh theo cảnh. Thêm lời ở mục Giọng đọc trước.")
        presets = body.presets or [settings['frame']['preset']]
        if len(set(presets)) != len(presets):
            raise HTTPException(422, 'Chọn mỗi tỷ lệ một lần.')
        if local_source(key).is_file():
            info = await asyncio.to_thread(editing.probe, local_source(key))
            editing.validate_effects(key, EditingSettings.model_validate(settings), info['duration'])
        variants = []
        for preset in presets:
            export_id = str(uuid.uuid4())
            variant_settings = {**settings, 'frame': {**settings['frame'], 'preset': preset}}
            snapshot = {"settings": variant_settings, "revision": state["revision"],
                    "audio_mix": {"narration": narration, "music": layers.get("music") if settings["use_music"] else None},
                    "output": str(editing.directory(key) / "exports" / f"{export_id}.mp4"),
                    "url": f"/api/videos/{key}/editing/exports/{export_id}",
                    'export_id': export_id, "timeline": timeline_key(scenes), "scene_texts": [s.get("narrator_text") for s in scenes]}
            variants.append(snapshot)
        return start_job(key, "edit", item.get("orientation"), {}, edit_export={'variants': variants})
    except AudioMixError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/{vid}/editing/exports/{export_id}")
async def export_file(vid: UUID, export_id: UUID, download: bool = False):
    await video(vid)
    if (editing.directory(str(vid)) / 'deleting-exports.json').is_file():
        editing.load(str(vid))
    path = editing.directory(str(vid)) / "exports" / f"{export_id}.mp4"
    if not path.is_file():
        raise HTTPException(404, "Bản xuất không còn trên máy.")
    return FileResponse(path, media_type="video/mp4", filename=f"frameweaver-{str(vid)[:8]}-{str(export_id)[:8]}.mp4", content_disposition_type='attachment' if download else 'inline')


@router.post('/{vid}/editing/exports/check-delete')
async def check_delete_exports(vid: UUID, body: DeleteExportsRequest):
    from agent.services import editing_exports
    return await editing_exports.manage(await video(vid), body.export_ids)


@router.delete('/{vid}/editing/exports')
async def delete_exports(vid: UUID, body: DeleteExportsRequest):
    from agent.services import editing_exports
    return await editing_exports.manage(await video(vid), body.export_ids, delete=True)


@router.post('/{vid}/editing/jobs/{job_id}/cancel')
async def cancel_export(vid: UUID, job_id: UUID):
    await video(vid)
    try:
        return cancel_job(str(vid), str(job_id))
    except AudioMixError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.patch('/{vid}/editing/exports/{export_id}')
async def rename_export(vid: UUID, export_id: UUID, body: ExportNameRequest):
    await video(vid)
    state = editing.load(str(vid))
    entry = next((e for e in state['exports'] if e.get('export_id') == str(export_id) or e.get('url', '').endswith(f'/{export_id}')), None)
    if not entry:
        raise HTTPException(404, 'Không tìm thấy bản xuất.')
    entry['name'] = body.name.strip() or 'Bản xuất'
    if (state.get('last_export') or {}).get('url') == entry.get('url'):
        state['last_export']['name'] = entry['name']
    editing.atomic_json(editing.directory(str(vid)) / 'settings.json', state)
    return entry


@router.post('/{vid}/editing/assets', status_code=201)
async def upload_asset(vid: UUID, file: UploadFile):
    await video(vid)
    from PIL import Image, UnidentifiedImageError
    try:
        raw = await file.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            raise HTTPException(413, 'Ảnh/logo tối đa 5 MB.')
        with Image.open(BytesIO(raw)) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width * image.height > 16_000_000:
                raise ValueError('Chọn ảnh PNG, JPEG hoặc WebP tối đa 16 triệu pixel.')
            image = image.convert('RGBA')
            image.thumbnail((2048, 2048))
            asset_id = str(uuid.uuid4())
            path = editing.asset_path(str(vid), asset_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            image.save(path, 'PNG')
        return {'id': asset_id, 'url': f'/api/videos/{vid}/editing/assets/{asset_id}'}
    except (ValueError, OSError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()


@router.get('/{vid}/editing/assets/{asset_id}')
async def get_asset(vid: UUID, asset_id: UUID):
    await video(vid)
    path = editing.asset_path(str(vid), str(asset_id))
    if not path.is_file():
        raise HTTPException(404, 'Ảnh/logo không còn trên máy.')
    return FileResponse(path, media_type='image/png')


@router.post('/{vid}/editing/preview-clips', status_code=202)
async def preview_clip(vid: UUID, body: EditingClipRequest):
    await video(vid)
    key = str(vid)
    mix = latest_job(key)
    if mix and mix['status'] in ACTIVE:
        raise HTTPException(409, 'Đợi xuất xong hoặc hủy bản xuất trước khi dựng đoạn xem thử.')
    source = local_source(key)
    if not source.is_file():
        raise HTTPException(409, 'Ghép video nguồn trước để dựng đoạn xem thử.')
    info = await asyncio.to_thread(editing.probe, source)
    if body.start >= info['duration']:
        raise HTTPException(422, 'Mốc bắt đầu vượt cuối video.')
    try:
        editing.validate_effects(key, body.settings, info['duration'])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    duration = min(body.duration, info['duration'] - body.start)
    clip_id = str(uuid.uuid4())
    path = editing.directory(key) / 'previews' / f'{clip_id}.mp4'
    async def run(update):
        path.parent.mkdir(parents=True, exist_ok=True)
        graph, images, size = editing.processing_filter(body.settings, info['width'], info['height'], video_id=key, time_offset=body.start)
        def progress(line):
            if line.startswith('out_time_us='):
                try:
                    update(progress=min(99, round(int(line.split('=', 1)[1]) / 1e6 / duration * 100)))
                except ValueError:
                    pass
        update(stage='Dựng đoạn xem thử bằng FFmpeg', progress=0)
        try:
            await media_jobs.process(['ffmpeg', '-nostdin', '-y', '-v', 'error', '-ss', str(body.start), '-t', str(duration), '-i', str(source),
                *editing.image_inputs(images), '-filter_complex', graph, '-map', '[vout]', '-map', '0:a?', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-af', f'volume={body.settings.source_volume}', '-movflags', '+faststart', '-progress', 'pipe:1', str(path)], timeout=180, on_line=progress)
            update(result={'url': f'/api/videos/{vid}/editing/preview-clips/{clip_id}', 'width': size[0], 'height': size[1], 'duration': duration})
        except BaseException:
            path.unlink(missing_ok=True)
            raise
    try:
        return media_jobs.start(key, 'editing-preview', run)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/{vid}/editing/preview-job')
async def preview_job(vid: UUID):
    await video(vid)
    job = media_jobs.latest(str(vid))
    return job if job and job.get('kind') == 'editing-preview' else None


@router.post('/{vid}/editing/preview-job/cancel')
async def cancel_preview_job(vid: UUID):
    await video(vid)
    job = media_jobs.latest(str(vid))
    if not job or job.get('kind') != 'editing-preview':
        raise HTTPException(409, 'Không có đoạn xem thử đang chạy.')
    return await media_jobs.cancel(str(vid))


@router.get('/{vid}/editing/preview-clips/{clip_id}')
async def preview_clip_file(vid: UUID, clip_id: UUID):
    await video(vid)
    path = editing.directory(str(vid)) / 'previews' / f'{clip_id}.mp4'
    if not path.is_file():
        raise HTTPException(404, 'Đoạn xem thử không còn trên máy.')
    return FileResponse(path, media_type='video/mp4')


@router.get('/{vid}/editing/propainter')
async def propainter_status(vid: UUID, asset_id: UUID | None = None):
    await video(vid)
    from agent.services import propainter_trial as trial, pacing
    if asset_id:
        from agent.api.timeline import checked_asset
        data = await checked_asset(vid, asset_id)
        source = Path(data['path'])
    else:
        source = local_source(str(vid))
    info, error = None, None
    if source.is_file():
        try:
            info = {**await trial.source_info(source), 'fingerprint': pacing.fingerprint(source)}
        except (ValueError, RuntimeError, OSError, KeyError, StopIteration) as exc:
            error = str(exc)
    return {**trial.availability(), 'source': info, 'source_error': error,
            'results': trial.results(str(vid))}


@router.post('/{vid}/editing/propainter/clips', status_code=202, dependencies=[Depends(media_library.guard)])
async def propainter_clips(vid: UUID, body: ProPainterClipsRequest):
    await video(vid)
    from agent.api.speech import ensure_idle
    from agent.api.timeline import checked_asset
    from agent.services import propainter_restore as restore, propainter_trial as trial, timeline
    key = str(vid)
    ensure_idle(key)
    environment = trial.availability()
    if not environment['available']:
        raise HTTPException(409, environment.get('unavailable_reason') or 'Môi trường ProPainter chưa sẵn sàng.')
    saved = timeline.load(key)
    if not saved or saved['revision'] != body.revision:
        raise HTTPException(409, 'Lưu bản dựng trước khi xử lý. Nạp lại nếu bản đã đổi ở tab khác.')
    document = saved['document']
    ids = set(map(str, body.clip_ids))
    tracks = {t['id']: t for t in document['tracks']}
    clips = [c for c in document['clips'] if c['id'] in ids]
    if len(clips) != len(ids) or any(tracks[c['track_id']]['kind'] != 'video' for c in clips):
        raise HTTPException(422, 'Chọn các cảnh trên lớp hình ảnh chính của timeline.')
    if body.region.width * body.region.height > .35:
        raise HTTPException(422, 'Vùng xóa tối đa 35% khung hình.')
    if any(tracks[c['track_id']]['locked'] for c in clips):
        raise HTTPException(409, 'Mở khóa lớp hình ảnh trước khi xử lý.')
    records = []
    for clip in sorted(clips, key=lambda c: c['start']):
        data = await checked_asset(vid, UUID(clip['asset_id']))
        duration = clip['duration'] * clip['speed']
        if not 1 <= duration <= restore.MAX_CLIP_SECONDS:
            raise HTTPException(422, 'Chọn cảnh dài từ 1–30 giây nguồn; có thể tách cảnh dài trên timeline.')
        if clip['source_in'] + duration > data['duration'] + .001:
            raise HTTPException(422, 'Mốc cắt vượt nguồn. Nạp lại bản dựng trước khi xử lý.')
        binding = {k: clip[k] for k in ('asset_id', 'source_in', 'duration', 'speed')}
        records.append((Path(data['path']), clip['source_in'], duration, {'clip_id': clip['id'], **binding}))
    # Catalog checks await I/O; revalidate before claiming the shared workbench slot.
    ensure_idle(key)
    if timeline.load(key)['revision'] != body.revision:
        raise HTTPException(409, 'Bản dựng vừa thay đổi. Lưu rồi thử lại.')
    async def run(update):
        completed = []
        update(stage="Đang chờ worker ProPainter", progress=0)
        async with trial.inference_session():
            for index, (source, start, duration, binding) in enumerate(records):
                def progress(index=index, **changes):
                    changes.pop('result', None)
                    update(stage=f'Cảnh {index+1}/{len(records)} · {changes.get("stage", "Đang xử lý")}',
                           progress=min(99, round((index*100+changes.get('progress', 0))/len(records))),
                           result={'trial_ids': completed.copy()})
                result = await restore.run_clip(key, source, start, duration, body.region, body.dilation, body.max_side, [binding], progress, mode=body.processing_mode)
                if result['trial_id'] not in completed:
                    completed.append(result['trial_id'])
                update(result={'trial_ids': completed.copy()})
    return media_jobs.start(key, 'propainter-clips', run)


@router.post('/{vid}/editing/propainter', status_code=202)
async def propainter_start(vid: UUID, body: ProPainterTrialRequest):
    await video(vid)
    from agent.api.speech import ensure_idle
    from agent.services import propainter_trial as trial, pacing
    ensure_idle(str(vid))
    if not trial.availability()['available']:
        raise HTTPException(409, 'Môi trường ProPainter chưa đạt thử nghiệm hoặc thiếu model.')
    source = local_source(str(vid))
    if not source.is_file():
        raise HTTPException(409, 'Cần video nguồn trên máy trước khi thử ProPainter.')
    if body.source_fingerprint and body.source_fingerprint != pacing.fingerprint(source):
        raise HTTPException(409, 'Video nguồn đã thay đổi. Đóng rồi mở lại công cụ để chọn lại vùng.')
    try:
        info = await trial.source_info(source)
        region = trial.validate(body.settings, body.region_id, body.start, body.duration, info)
        async def run(update):
            await trial.run_trial(str(vid), source, body.settings, region, body.start, body.duration, body.dilation, info, update, body.max_side)
        return media_jobs.start(str(vid), 'propainter-trial', run)
    except (ValueError, RuntimeError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/{vid}/editing/propainter/job')
async def propainter_job(vid: UUID):
    await video(vid)
    job = media_jobs.latest(str(vid))
    return job if job and job.get('kind') in {'propainter-trial', 'propainter-clips'} else None


@router.post('/{vid}/editing/propainter/cancel')
async def propainter_cancel(vid: UUID, job_id: UUID | None = None):
    await video(vid)
    job = media_jobs.latest(str(vid))
    if not job or job.get('kind') not in {'propainter-trial', 'propainter-clips'}:
        raise HTTPException(409, 'Không có thử nghiệm ProPainter đang chạy.')
    if job_id is not None and job['id'] != str(job_id):
        raise HTTPException(409, 'Tác vụ đã thay đổi. Nạp lại công cụ để xem tiến trình hiện tại.')
    return await media_jobs.cancel(str(vid))


@router.get('/{vid}/editing/propainter/results/{trial_id}')
async def propainter_result(vid: UUID, trial_id: UUID, download: bool = False):
    await video(vid)
    path = editing.directory(str(vid)) / 'propainter' / f'{trial_id}.mp4'
    if not path.is_file():
        raise HTTPException(404, 'Không tìm thấy đoạn thử.')
    return FileResponse(path, media_type='video/mp4', filename=f'frameweaver-propainter-{trial_id}.mp4', content_disposition_type='attachment' if download else 'inline')


@router.get('/{vid}/editing/propainter/results/{trial_id}/before')
async def propainter_before(vid: UUID, trial_id: UUID):
    await video(vid)
    path = editing.directory(str(vid)) / 'propainter' / f'{trial_id}-before.mp4'
    if not path.is_file():
        raise HTTPException(404, 'Bản thử cũ chưa lưu đoạn đối chiếu.')
    return FileResponse(path, media_type='video/mp4')
