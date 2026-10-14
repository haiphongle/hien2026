"""Draft, proof and publish a style through the existing image batch worker."""
import asyncio
import shutil
from uuid import UUID

from PIL import Image
from agent.db import crud
from agent.models.story_style import StyleSettings
from agent.models.stickman import CreateRequest
from agent.services import media_jobs, speech_store, stickman, stickman_direction as direction

_lock = asyncio.Lock()
BASE_STYLE = {'2d': 'cartoon_story', '3d': 'claymation', 'drawing': 'stickman_pencil', 'stick': 'stickman_chalk'}


def proof_path(pid, sid):
    return direction.profiles(pid)/str(UUID(str(sid)))/'proof.json'


def signature(data, *, character_only=False):
    fields = ['style_instruction', 'negative_prompt', 'character_design']
    if not character_only:
        fields += ['category', 'default_character_id', 'scene_action', 'orientation']
    return stickman.digest([data.get(k) for k in fields])


async def character(pid, ident):
    row = next((c for c in await crud.get_project_characters(pid) if c['id'] == str(ident)), None)
    if not row or row.get('entity_type') != 'character' or not row.get('media_id'):
        raise ValueError('Chọn nhân vật có ảnh tham chiếu và thuộc dự án này.')
    UUID(row['media_id'])
    return row


async def editable(pid, sid):
    data = direction.profile(pid, sid)
    if data.get('status') != 'DRAFT':
        raise ValueError('Mẫu đã lưu được giữ ổn định cho video cũ. Thêm mẫu mới để đổi phong cách.')
    job = media_jobs.latest(str(sid))
    if job and job['status'] in media_jobs.ACTIVE:
        raise ValueError('Đợi ảnh mẫu hoàn tất hoặc bấm Dừng trước khi sửa.')
    if data.get('preview_video_id'):
        requests = await crud.list_requests(video_id=data['preview_video_id'])
        if any(r['status'] in {'PENDING', 'PROCESSING'} for r in requests):
            raise ValueError('Google Flow còn đang xử lý ảnh mẫu. Đợi lượt này hoàn tất rồi thử lại.')
    return data


async def update(pid, sid, settings: StyleSettings):
    async with _lock:
        previous = await editable(pid, sid)
        if settings.default_character_id:
            await character(pid, settings.default_character_id)
        data = {**previous, **settings.model_dump(mode='json')}
        data['revision'] = previous.get('revision', 1) + (signature(previous, character_only=True) != signature(data, character_only=True))
        if signature(previous) != signature(data) and data.get('preview_video_id'):
            await crud.delete_video(data.pop('preview_video_id'))
        speech_store.atomic_json(direction.profiles(pid)/f'{sid}.json', data)
        return data


def proof(pid, sid):
    data = direction.profile(pid, sid)
    result = speech_store.read_json(proof_path(pid, sid))
    if not result or result.get('signature') != signature(data):
        return None
    if not all((stickman.directory(str(sid))/f'{result[k]}.image').is_file() for k in ['character_media_id', 'scene_media_id']):
        return None
    return {**result, **{f'{kind}_url': f'/api/projects/{pid}/stickman/styles/{sid}/proof/{kind}?v={result["signature"]}' for kind in ['character', 'scene']}}


async def start_preview(pid, sid, project):
    async with _lock:
        data = await editable(pid, sid)
        cached = proof(pid, sid)
        if cached:
            return {'id': '', 'status': 'COMPLETED', 'progress': 100, 'stage': 'Ảnh mẫu đã lưu', 'result': cached}
        if data.get('default_character_id'):
            await character(pid, data['default_character_id'])
        style_ref = await crud.get_character(data['reference_id'])
        if not style_ref or not style_ref.get('media_id'):
            raise ValueError('Ảnh phong cách chưa sẵn sàng. Tải lại ảnh mẫu.')
        UUID(style_ref['media_id'])
        settings = StyleSettings.model_validate({k: v for k, v in data.items() if k in StyleSettings.model_fields})
        body = CreateRequest(title=settings.name, style=BASE_STYLE[settings.category], style_profile_id=sid,
            character_id=settings.default_character_id, preset_id='style_preview', motion='still', orientation=settings.orientation,
            scenes=[{'narration': 'Ảnh mẫu phong cách.', 'action': settings.scene_action, 'cast': ['A']}])

        async def run(report):
            vid = data.get('preview_video_id')
            if not vid or not await crud.get_video(vid):
                row = await crud.create_video(pid, f'Ảnh thử phong cách · {data["name"]}', orientation=settings.orientation)
                vid = row['id']
                data['preview_video_id'] = vid
                speech_store.atomic_json(direction.profiles(pid)/f'{sid}.json', data)
            try:
                reference = await stickman.reference(project, body)
                if not reference.get('media_id'):
                    await stickman.generate_images(vid, pid, [reference['id']], 'GENERATE_CHARACTER_IMAGE', 'VERTICAL', report, 5, 40)
                    reference = await crud.get_character(reference['id'])
                UUID(reference['media_id'])
                char_file = await stickman.image_file(str(sid), reference['media_id'])
                # Keep the approved character playable through the normal reference URL.
                with Image.open(char_file) as image:
                    suffix = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}.get(image.format)
                if not suffix:
                    raise ValueError('Ảnh nhân vật không đúng định dạng.')
                target = direction.OUTPUT_DIR/'character_refs'/f'{reference["id"]}{suffix}'
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(char_file, target)
                reference = await crud.update_character(reference['id'], reference_image_url=f'/api/characters/{reference["id"]}/image')
                rows = await crud.list_scenes(vid)
                if rows:
                    scene = rows[0]
                else:
                    scene = await crud.create_scene(vid, 1, settings.scene_action,
                        image_prompt=(f'Character A is {reference["name"]}. Use {style_ref["name"]} only as a visual style reference. '
                                      + direction.image_prompt(body, body.scenes[0], pid)),
                        character_names=[reference['name'], style_ref['name']])
                await stickman.generate_images(vid, pid, [scene['id']], 'GENERATE_IMAGE', settings.orientation, report, 45, 45)
                scene = await crud.get_scene(scene['id'])
                prefix = 'vertical' if settings.orientation == 'VERTICAL' else 'horizontal'
                mid = str(UUID(scene[f'{prefix}_image_media_id']))
                await stickman.image_file(str(sid), mid)
                speech_store.atomic_json(proof_path(pid, sid), {'signature': signature(data),
                    'character_id': reference['id'], 'character_media_id': reference['media_id'], 'scene_media_id': mid})
                report(result=proof(pid, sid), stage='Duyệt nhân vật và cảnh mẫu')
            finally:
                # A stopped Flow request may still be processing; keep its rows until retry.
                requests = await crud.list_requests(video_id=vid)
                if not any(r['status'] in {'PENDING', 'PROCESSING'} for r in requests):
                    await crud.delete_video(vid)
                    data.pop('preview_video_id', None)
                    speech_store.atomic_json(direction.profiles(pid)/f'{sid}.json', data)

        return media_jobs.start(str(sid), 'style_preview', run)


async def publish(pid, sid, request):
    async with _lock:
        data = await editable(pid, sid)
        result = proof(pid, sid)
        if not result or result['signature'] != request.preview_signature:
            raise ValueError('Mô tả đã đổi hoặc thiếu ảnh mẫu. Tạo và duyệt lại trước khi lưu.')
        selected = result['character_id'] if request.use_preview_character else None
        if selected:
            await character(pid, selected)
        data.update(status='PUBLISHED', default_character_id=selected)
        # Default selection is a preference; preserve the approved proof after publishing.
        result['signature'] = signature(data)
        speech_store.atomic_json(proof_path(pid, sid), {k: v for k, v in result.items() if not k.endswith('_url')})
        speech_store.atomic_json(direction.profiles(pid)/f'{sid}.json', data)
        return data
