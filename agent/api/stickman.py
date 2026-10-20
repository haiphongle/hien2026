"""An isolated Studio entry point; generation and editing stay in shared services."""
from uuid import UUID
import uuid
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from PIL import Image

from agent.db import crud
from agent.materials import get_material, story_category
from agent.models.story_style import StyleUpload, StyleSettings, StylePublish, StylePreviewUpload
from agent.models.stickman import ActorChange, CreateRequest, PlanRequest, ProductionTemplate, RetryRequest, SceneSuggestionRequest, PromptPreviewRequest
from agent.config import SHARED_OUTPUT_DIR
from agent.services import media_jobs, media_library, speech_store, stickman, stickman_models, timeline
from agent.services import stickman_direction as direction
from agent.services import stickman_styles
from agent.services.image_effects import uses_ai
from agent.services import story_schedule
from agent.services import story_style_previews
from agent.services.stickman_production import estimate, image_signature, preview_versions, record_preview, storyboard_images
from agent.services.flow_client import get_flow_client
from agent.api.speech import ensure_idle

router = APIRouter(tags=['stickman'])


async def project(pid):
    item = await crud.get_project(str(pid))
    if not item:
        raise HTTPException(404, 'Không tìm thấy dự án.')
    if item.get('status') != 'ACTIVE':
        raise HTTPException(409, 'Mở lại dự án trước khi tạo video.')
    return item


async def saved_video(vid):
    item = await crud.get_video(str(vid))
    data = speech_store.read_json(stickman.directory(str(vid))/'plan.json')
    if not item or not data:
        raise HTTPException(404, 'Không tìm thấy video người que.')
    return item, CreateRequest.model_validate(data)


def flow_ready():
    if not get_flow_client().connected:
        raise HTTPException(503, 'Mở Google Flow và kết nối extension trước khi tạo ảnh.')


async def media_ready(body):
    if body.motion == 'talk':
        return
    if body.image_provider == 'flow' or uses_ai(body) and body.video_provider == 'flow':
        flow_ready()
    if body.image_provider == 'muse2api' or uses_ai(body) and body.video_provider == 'muse2api':
        from agent.services.muse_client import MuseClient, MuseError
        try:
            await MuseClient().readiness()
        except MuseError as exc:
            raise HTTPException(503, str(exc)) from exc


@router.get('/projects/{pid}/stickman/schedule')
async def scheduled_stories(pid: UUID):
    await project(pid)
    return [story_schedule.summary(item) for item in story_schedule.entries(str(pid)) if not item.get('content')]


@router.get('/projects/{pid}/stickman/schedule/{sid}')
async def scheduled_story(pid: UUID, sid: UUID):
    await project(pid)
    try:
        return story_schedule.read(str(pid), str(sid))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/projects/{pid}/stickman/schedule', status_code=201)
async def save_scheduled_story(pid: UUID, body: story_schedule.SavedStory):
    await project(pid)
    try:
        return story_schedule.summary(await story_schedule.save(str(pid), body))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch('/projects/{pid}/stickman/schedule/{sid}')
async def reschedule_story(pid: UUID, sid: UUID, body: story_schedule.ScheduleTime):
    await project(pid)
    try:
        return story_schedule.summary(await story_schedule.reschedule(str(pid), str(sid), body))
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete('/projects/{pid}/stickman/schedule/{sid}')
async def delete_scheduled_draft(pid: UUID, sid: UUID):
    await project(pid)
    try:
        await story_schedule.remove(str(pid), str(sid))
        return {'deleted': True}
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('/projects/{pid}/stickman/options')
async def options(pid: UUID):
    item = await project(pid)
    from agent.services import image_effects
    return {'presets': [{**{k: v for k, v in p.items() if k not in {'character_design', 'reference_version', 'material_override'}},
                         'name': p.get('name') or get_material(p['id'])['name'],
                         'category': story_category(p['id']),
                         **(story_style_previews.presentation(p['id'], str(pid)) if p.get('collection') == 'html' else {}),
                         'local_actor': p.get('local_actor', True)} for p in stickman.PRESETS],
            'video_models': stickman_models.options(), 'image_effects': image_effects.catalog(),
            'default_video_models': {orientation: stickman_models.default_model(item, orientation) for orientation in ('VERTICAL', 'HORIZONTAL')}}


@router.get('/projects/{pid}/stickman/presets/{style_id}/preview')
async def preset_preview_image(pid: UUID, style_id: str):
    await project(pid)
    try:
        path = story_style_previews.image_path(style_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, 'Mẫu này chưa có ảnh minh họa riêng.')
    return FileResponse(path, media_type='image/webp', headers={'Cache-Control': 'no-cache'})


@router.put('/projects/{pid}/stickman/presets/{style_id}/preview')
async def save_preset_preview(pid: UUID, style_id: str, body: StylePreviewUpload):
    await project(pid)
    try:
        return await story_style_previews.save(style_id, str(pid), body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.delete('/projects/{pid}/stickman/presets/{style_id}/preview')
async def reset_preset_preview(pid: UUID, style_id: str):
    await project(pid)
    try:
        return await story_style_previews.reset(style_id, str(pid))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/projects/{pid}/stickman/plan')
async def plan(pid: UUID, body: PlanRequest):
    await project(pid)
    try:
        return await stickman.plan(body, str(pid))
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, 'AI chưa trả được kịch bản. Kiểm tra kết nối AI trong Cài đặt rồi thử lại.') from exc


@router.post('/projects/{pid}/stickman/suggest-scene')
async def suggest_scene(pid: UUID, body: SceneSuggestionRequest):
    await project(pid)
    try:
        return await direction.suggest_scene(str(pid), body)
    except TimeoutError as exc:
        raise HTTPException(504, 'AI quá thời gian chờ. Cảnh hiện tại được giữ nguyên.') from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, 'AI chưa cải thiện được cảnh. Kiểm tra kết nối AI trong Cài đặt rồi thử lại.') from exc


@router.post('/projects/{pid}/stickman/prompts')
async def preview_prompts(pid: UUID, body: PromptPreviewRequest):
    await project(pid)
    try:
        return await direction.prompt_bundle(str(pid), body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/projects/{pid}/stickman/styles')
async def visual_styles(pid: UUID):
    await project(pid)
    return [speech_store.read_json(path) for path in sorted(direction.profiles(pid).glob('*.json'))]


@router.post('/projects/{pid}/stickman/styles', status_code=201)
async def save_visual_style(pid: UUID, body: StyleUpload):
    await project(pid)
    flow_ready()
    try:
        return await direction.save_profile(str(pid), body)
    except TimeoutError as exc:
        raise HTTPException(504, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, 'AI chưa phân tích được ảnh phong cách. Kiểm tra kết nối AI rồi thử lại.') from exc


@router.patch('/projects/{pid}/stickman/styles/{sid}')
async def edit_visual_style(pid: UUID, sid: UUID, body: StyleSettings):
    await project(pid)
    try:
        return await stickman_styles.update(str(pid), str(sid), body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/projects/{pid}/stickman/styles/{sid}/preview')
async def style_preview_status(pid: UUID, sid: UUID):
    await project(pid)
    try:
        direction.profile(pid, sid)
        return {'job': media_jobs.latest(str(sid)), 'proof': stickman_styles.proof(pid, sid)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('/projects/{pid}/stickman/styles/{sid}/preview', status_code=202, dependencies=[Depends(media_library.guard)])
async def generate_style_preview(pid: UUID, sid: UUID):
    item = await project(pid)
    flow_ready()
    try:
        return await stickman_styles.start_preview(str(pid), str(sid), item)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/projects/{pid}/stickman/styles/{sid}/cancel/{jid}')
async def cancel_style_preview(pid: UUID, sid: UUID, jid: UUID):
    await project(pid)
    try:
        data = direction.profile(pid, sid)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    job = media_jobs.latest(str(sid))
    if not job or job['id'] != str(jid) or job['kind'] != 'style_preview':
        raise HTTPException(409, 'Tác vụ ảnh mẫu đã đổi. Nạp lại mẫu.')
    if data.get('preview_video_id'):
        for request in await crud.list_requests(video_id=data['preview_video_id']):
            if request['status'] == 'PENDING':
                await crud.update_request(request['id'], status='FAILED', error_message='Đã dừng tạo ảnh mẫu.')
    return await media_jobs.cancel(str(sid))


@router.post('/projects/{pid}/stickman/styles/{sid}/publish')
async def publish_visual_style(pid: UUID, sid: UUID, body: StylePublish):
    await project(pid)
    try:
        return await stickman_styles.publish(str(pid), str(sid), body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/projects/{pid}/stickman/styles/{sid}/proof/{kind}')
async def style_proof_image(pid: UUID, sid: UUID, kind: str):
    await project(pid)
    if kind not in {'character', 'scene'}:
        raise HTTPException(404, 'Ảnh mẫu chưa sẵn sàng.')
    try:
        result = stickman_styles.proof(pid, sid)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not result:
        raise HTTPException(404, 'Ảnh mẫu chưa sẵn sàng.')
    path = stickman.directory(str(sid))/f'{result[f"{kind}_media_id"]}.image'
    with Image.open(path) as image:
        mime = Image.MIME.get(image.format, 'image/jpeg')
    return FileResponse(path, media_type=mime)


@router.post('/projects/{pid}/stickman/videos', status_code=202, dependencies=[Depends(media_library.guard)])
async def create(pid: UUID, body: CreateRequest):
    item = await project(pid)
    await media_ready(body)
    try:
        await stickman.validate(body, item)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return await stickman.create_story(item, body)


@router.get('/videos/{vid}/stickman/job')
async def job(vid: UUID):
    item, body = await saved_video(vid)
    current = media_jobs.latest(str(vid))
    if current and current['kind'] != 'stickman':
        result = speech_store.read_json(stickman.directory(str(vid))/'result.json')
        if result:
            return result
        raise HTTPException(409, 'Video đang dùng một tác vụ hậu kỳ khác. Mở timeline để kiểm tra.')
    if current and current['status'] == 'COMPLETED' and current.get('result', {}).get('preview_url'):
        return {**current, 'stage': 'Ảnh các cảnh · chờ duyệt' if body.preview_all else 'Ảnh mẫu · chờ duyệt', 'progress': 45}
    uses_flow = body.image_provider == 'flow' or uses_ai(body) and body.video_provider == 'flow'
    if uses_flow and current and current['status'] in {'FAILED', 'CANCELLED'} and stickman_models.needs_media(str(vid), body, stickman.directory(str(vid))):
        return {**current, 'model_recovery': stickman_models.recovery(current, body, await project(item['project_id']))}
    return current


@router.post('/videos/{vid}/stickman/retry', status_code=202, dependencies=[Depends(media_library.guard)])
async def retry(vid: UUID, request: RetryRequest | None = None):
    request = request or RetryRequest()
    item, body = await saved_video(vid)
    ensure_idle(str(vid))
    needs_media = stickman_models.needs_media(str(vid), body, stickman.directory(str(vid)))
    try:
        current = media_jobs.latest(str(vid))
        if request.job_id and (not current or str(request.job_id) != current['id']):
            raise HTTPException(409, 'Tác vụ đã thay đổi. Nạp lại trạng thái trước khi thử lại.')
        preview = (current or {}).get('result', {}).get('preview_url')
        if (request.regenerate_preview or request.preview_media_id) and not preview:
            raise HTTPException(409, 'Chỉ đổi ảnh khi tác vụ đang chờ duyệt ảnh mẫu.')
        if preview and not request.approve_preview and not request.regenerate_preview:
            raise HTTPException(422, 'Duyệt ảnh mẫu để tiếp tục tạo các cảnh.')
        if current and current['kind'] == 'stickman' and current['status'] == 'COMPLETED' and not preview:
            return current
        owner = await project(item['project_id'])
        message = None
        if request.media_provider:
            if not needs_media or not current or current['status'] not in {'FAILED', 'CANCELLED'}:
                raise HTTPException(422, 'Chỉ đổi nhà cung cấp cho tác vụ đã dừng còn ảnh hoặc clip cần tạo.')
            if any(r['status'] in {'PENDING', 'PROCESSING'} for r in await crud.list_requests(video_id=str(vid))):
                raise HTTPException(409, 'Còn yêu cầu tạo ảnh/video đang chạy. Đợi hoàn tất trước khi chuyển sang Muse để tránh tạo trùng.')
            body = body.model_copy(update={'image_provider': 'muse2api',
                **({'video_provider': 'muse2api', 'video_model': None} if uses_ai(body) else {})})
            message = 'Đã chuyển sang Muse cho ảnh và clip còn thiếu. Giữ kịch bản, ảnh, giọng và clip đã hoàn tất; không thay đổi nhà cung cấp của video khác.'
        elif body.video_provider == 'flow' and stickman_models.needs_clips(str(vid), body, stickman.directory(str(vid))):
            recovery = stickman_models.recovery(current or {}, body, owner)
            automatic = not request.video_model and request.auto_switch and recovery['auto_switch']
            model = request.video_model or (recovery['retry_model'] if automatic else recovery['current_model'])
            if automatic or model != recovery['current_model']:
                active = [r for r in await crud.list_requests(video_id=str(vid))
                          if r['status'] in {'PENDING', 'PROCESSING'} and 'VIDEO' in r['type']]
                if active:
                    raise HTTPException(409, 'Google Flow còn clip đang chạy. Đợi kết quả trước khi đổi model để tránh tạo trùng.')
            body = body.model_copy(update={'video_model': model})
            message = stickman_models.notice((current or {}).get('video_model'), model, automatic)
        elif request.video_model:
            raise HTTPException(422, 'Không còn clip cần tạo. Model chỉ thay đổi cho phần clip còn thiếu.')
        if needs_media:
            await media_ready(body)
        await stickman.validate(body, owner)
        if request.regenerate_preview:
            active = [r for r in await crud.list_requests(video_id=str(vid)) if r['status'] in {'PENDING', 'PROCESSING'}]
            if active:
                raise HTTPException(409, 'Google Flow còn yêu cầu đang chạy. Đợi hoàn tất trước khi tạo ảnh mẫu khác.')
            scenes = await crud.list_scenes(str(vid))
            if not scenes:
                raise ValueError('Video chưa có cảnh để tạo ảnh mẫu. Nạp lại kịch bản trước.')
            if request.preview_scene_id and (not body.preview_all or not any(s['id'] == str(request.preview_scene_id) for s in scenes)):
                raise ValueError('Ảnh cảnh không thuộc bảng ảnh đang duyệt.')
            record_preview(str(vid), body, scenes[0])
            speech_store.atomic_json(stickman.directory(str(vid))/'preview.json', {'approved': False, 'regenerate': True,
                **({'scene_id': str(request.preview_scene_id)} if request.preview_scene_id else {})})
        elif preview and request.approve_preview:
            if body.preview_all and len(await storyboard_images(str(vid), body)) != len(body.scenes):
                raise ValueError('Chưa đủ ảnh cảnh để duyệt. Tiếp tục tạo phần còn thiếu trước.')
            if request.preview_media_id:
                scenes = await crud.list_scenes(str(vid))
                versions = preview_versions(str(vid), body, scenes)
                choice = next((v for v in versions if v['media_id'] == str(request.preview_media_id)), None)
                if not choice:
                    raise HTTPException(422, 'Ảnh mẫu không thuộc kịch bản hiện tại. Nạp lại danh sách ảnh.')
                prefix = 'vertical' if body.orientation == 'VERTICAL' else 'horizontal'
                await crud.update_scene(scenes[0]['id'], **{f'{prefix}_image_media_id': choice['media_id'],
                    f'{prefix}_image_url': choice.get('source_url'), f'{prefix}_image_status': 'COMPLETED',
                    f'{prefix}_video_media_id': None, f'{prefix}_video_url': None, f'{prefix}_video_status': 'PENDING'})
            speech_store.atomic_json(stickman.directory(str(vid))/'preview.json', {'approved': True})
        if message:
            speech_store.atomic_json(stickman.directory(str(vid))/'plan.json', body.model_dump(mode='json'))
        return stickman.start(str(vid), body, owner, model_notice=message)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get('/videos/{vid}/stickman/preview')
async def previews(vid: UUID):
    _, body = await saved_video(vid)
    return preview_versions(str(vid), body, await crud.list_scenes(str(vid)))


@router.get('/videos/{vid}/stickman/storyboard')
async def storyboard(vid: UUID):
    _, body = await saved_video(vid)
    return {'all_scenes': body.preview_all, 'total': len(body.scenes),
            'scenes': await storyboard_images(str(vid), body)}


@router.get('/videos/{vid}/stickman/preview/{mid}')
async def preview_image(vid: UUID, mid: UUID):
    items = await previews(vid)
    if not any(item['media_id'] == str(mid) for item in items):
        raise HTTPException(404, 'Không tìm thấy ảnh mẫu của kịch bản này.')
    path = stickman.directory(str(vid))/f'{mid}.image'
    try:
        with Image.open(path) as image:
            mime = Image.MIME.get(image.format, 'image/png')
    except OSError as exc:
        raise HTTPException(404, 'Ảnh mẫu chưa lưu trên máy.') from exc
    return FileResponse(path, media_type=mime)


@router.post('/videos/{vid}/stickman/cancel/{jid}')
async def cancel(vid: UUID, jid: UUID):
    await saved_video(vid)
    current = media_jobs.latest(str(vid))
    if not current or current['id'] != str(jid) or current['kind'] != 'stickman':
        raise HTTPException(409, 'Tác vụ đã thay đổi. Nạp lại trạng thái.')
    return await media_jobs.cancel(str(vid))


@router.get('/videos/{vid}/stickman/scenes/{sid}/file')
async def scene_file(vid: UUID, sid: UUID):
    await saved_video(vid)
    scene = await crud.get_scene(str(sid))
    renders = speech_store.read_json(stickman.directory(str(vid))/'renders.json', {})
    path = Path(renders.get(str(sid), {}).get('path') or stickman.directory(str(vid))/f'{sid}.mp4')
    if not scene or scene['video_id'] != str(vid) or not path.is_file():
        raise HTTPException(404, 'Ảnh cảnh chưa được dựng thành video.')
    return FileResponse(path, media_type='video/mp4')


@router.get('/videos/{vid}/stickman/scenes/{sid}/image')
async def scene_image(vid: UUID, sid: UUID):
    _, body = await saved_video(vid)
    scene = await crud.get_scene(str(sid))
    if not scene or scene['video_id'] != str(vid):
        raise HTTPException(404, 'Không tìm thấy ảnh cảnh.')
    prefix = 'vertical' if body.orientation == 'VERTICAL' else 'horizontal'
    media_id = scene.get(f'{prefix}_image_media_id')
    try:
        renders = speech_store.read_json(stickman.directory(str(vid))/'renders.json', {})
        path = Path(renders.get(str(sid), {}).get('thumbnail', '')) if body.motion == 'talk' else stickman.directory(str(vid))/f'{UUID(media_id)}.image'
        with Image.open(path) as image:
            mime = Image.MIME.get(image.format, 'image/png')
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(404, 'Ảnh cảnh chưa lưu trên máy.') from exc
    return FileResponse(path, media_type=mime)


@router.post('/projects/{pid}/stickman/estimate')
async def cost(pid: UUID, body: CreateRequest, video_id: UUID | None = None):
    await project(pid)
    if video_id:
        item, _ = await saved_video(video_id)
        if item['project_id'] != str(pid):
            raise HTTPException(404, 'Video không thuộc dự án.')
    return await estimate(str(pid), body, str(video_id) if video_id else None)


@router.get('/projects/{pid}/stickman/templates')
async def templates(pid: UUID):
    await project(pid)
    root = SHARED_OUTPUT_DIR/'stickman_templates'/str(pid)
    return [speech_store.read_json(path) for path in sorted(root.glob('*.json'))]


@router.post('/projects/{pid}/stickman/templates')
async def save_template(pid: UUID, body: ProductionTemplate):
    await project(pid)
    name = body.name.strip()
    if len(name) < 3:
        raise HTTPException(422, 'Tên mẫu cần ít nhất 3 ký tự.')
    tid = str(uuid.uuid5(pid, name.casefold()))
    settings = body.settings.model_dump(mode='json')
    result = {'id': tid, 'name': name, 'settings': {key: settings[key] for key in stickman.SETTINGS}}
    speech_store.atomic_json(SHARED_OUTPUT_DIR/'stickman_templates'/str(pid)/f'{tid}.json', result)
    return result


@router.patch('/videos/{vid}/stickman/plan', status_code=202, dependencies=[Depends(media_library.guard)])
async def revise(vid: UUID, body: CreateRequest):
    item, old = await saved_video(vid)
    ensure_idle(str(vid))
    if any(getattr(body, key) != getattr(old, key) for key in ('image_provider', 'video_provider', 'video_model')):
        if any(r['status'] in {'PENDING', 'PROCESSING'} for r in await crud.list_requests(video_id=str(vid))):
            raise HTTPException(409, 'Còn yêu cầu tạo ảnh/video đang chạy. Đợi kết quả trước khi đổi nhà cung cấp hoặc model để tránh tạo trùng.')
    scenes = await crud.list_scenes(str(vid))
    if len(body.scenes) != len(scenes):
        raise HTTPException(422, 'Đổi số cảnh: tạo video mới để giữ bản dựng đã có.')
    saved = timeline.load(str(vid))
    result = speech_store.read_json(stickman.directory(str(vid))/'result.json', {})
    if saved and saved['document'] != result.get('result', {}).get('timeline'):
        raise HTTPException(409, 'Timeline đã được chỉnh thủ công. Tạo video mới để giữ các chỉnh sửa; cử chỉ có thể sửa ngay trên đoạn đã chọn.')
    owner = await project(item['project_id'])
    await media_ready(body)
    try:
        await stickman.validate(body, owner)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc
    root = stickman.directory(str(vid))
    generation = speech_store.read_json(root/'generation.json', {})
    for scene, planned in zip(scenes, old.scenes):
        state = generation.setdefault(scene['id'], {})
        # Bind legacy image keys to the original expression before saving the edited plan.
        if not state.get('image') or (state.get('image_version') != 2 and state['image'] == image_signature(old, planned, legacy=True)):
            state['image'] = image_signature(old, planned)
        state['image_version'] = 2
    speech_store.atomic_json(root/'generation.json', generation)
    speech_store.atomic_json(root/'plans'/f'{uuid.uuid4()}.json', old.model_dump(mode='json'))
    speech_store.atomic_json(root/'pending.json', {'rebuild': True, 'revision': (saved or {}).get('revision', 0)})
    speech_store.atomic_json(root/'plan.json', body.model_dump(mode='json'))
    if body.preview_first and any(image_signature(old, before) != image_signature(body, after) for before, after in zip(old.scenes, body.scenes)):
        (root/'preview.json').unlink(missing_ok=True)
    for scene, planned in zip(scenes, body.scenes):
        await crud.update_scene(scene['id'], prompt=planned.action, narrator_text=planned.narration,
            image_prompt=direction.image_prompt(body, planned, item['project_id']))
    return stickman.start(str(vid), body, owner)


@router.get('/videos/{vid}/stickman/quality')
async def quality(vid: UUID):
    await saved_video(vid)
    return speech_store.read_json(stickman.directory(str(vid))/'quality.json')


def actor_metadata(vid, aid):
    data = timeline.asset(str(vid), str(aid))
    path = Path(data['path'])
    root = stickman.directory(str(vid))/'renders'
    if path.parent != root or not data['ref'].startswith('stickman:'):
        raise ValueError('Đoạn này không phải người que nói chuyện.')
    meta = speech_store.read_json(path.with_suffix('.metadata.json'))
    if not meta or meta['settings']['motion'] != 'talk':
        raise ValueError('Chọn đoạn người que nói chuyện để sửa cử chỉ.')
    return data, meta


@router.get('/videos/{vid}/stickman/actors/{aid}')
async def actor(vid: UUID, aid: UUID):
    try:
        _, meta = actor_metadata(vid, aid)
        return {key: meta['settings'][key] for key in ('emotion', 'gesture')}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/videos/{vid}/stickman/actors', status_code=202, dependencies=[Depends(media_library.guard)])
async def change_actor(vid: UUID, body: ActorChange):
    ensure_idle(str(vid))
    try:
        data, meta = actor_metadata(vid, body.asset_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    settings = {**meta['settings'], 'emotion': body.emotion, 'gesture': body.gesture}
    key = stickman.digest([settings, meta['audio']])
    target = stickman.directory(str(vid))/'renders'/f'{key}.mp4'
    async def run(update):
        if not target.is_file():
            spec = target.with_suffix('.json')
            speech_store.atomic_json(spec, settings)
            try:
                await media_jobs.process([sys.executable, '-m', 'agent.services.stickman_actor', str(spec), meta['audio'], str(target), str(target.with_suffix('.png'))], timeout=300)
            finally:
                target.with_name(f'.{key}.rendering.mp4').unlink(missing_ok=True)
        metadata = {**meta, 'key': key, 'path': str(target), 'settings': settings, 'thumbnail': str(target.with_suffix('.png'))}
        speech_store.atomic_json(target.with_suffix('.metadata.json'), metadata)
        asset = await timeline.register(str(vid), str(vid), f"{data['ref'].split(':')[0]}:{data['ref'].split(':')[1]}:{key}", data['name'], target, 'source')
        update(progress=100, result={'asset_id': asset['id'], 'asset': asset})
    return media_jobs.start(str(vid), 'timeline-stickman', run)
