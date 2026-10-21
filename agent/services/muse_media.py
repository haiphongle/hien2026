"""Muse requests use the existing queue, UUIDs and scene result handlers."""
import asyncio
import base64
import io
import json
import hashlib
import re
from uuid import UUID, uuid4

from PIL import Image
from agent.config import SHARED_OUTPUT_DIR
from agent.db import crud
from agent.services.muse_client import MuseClient, MuseError, MuseTaskFailed, image_data

ROOT = SHARED_OUTPUT_DIR / 'muse'
SUPPORTED = {'GENERATE_CHARACTER_IMAGE', 'REGENERATE_CHARACTER_IMAGE', 'EDIT_CHARACTER_IMAGE',
             'GENERATE_IMAGE', 'REGENERATE_IMAGE', 'EDIT_IMAGE', 'GENERATE_VIDEO', 'REGENERATE_VIDEO'}


def local_file(media_id, extension='.png'):
    try:
        path = ROOT / f'{UUID(str(media_id))}{extension}'
        return path if path.is_file() else None
    except (ValueError, TypeError):
        return None


def local_image(media_id):
    return local_file(media_id)


async def reference(media_id):
    local = local_image(media_id)
    if local:
        return image_data(await asyncio.to_thread(local.read_bytes))
    # User uploads remain usable even when the Flow extension is disconnected.
    from agent.api.characters import character_image
    from fastapi import HTTPException
    from pathlib import Path
    for character in await crud.list_characters_by_media_id(media_id):
        try:
            cached = await character_image(character['id'])
            return image_data(await asyncio.to_thread(Path(cached.path).read_bytes))
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
    # This route already knows how to download and re-sign existing Flow images.
    from agent.api.flow import download_image
    response = await download_image(str(media_id))
    return image_data(response.body)


async def flow_id(media_id, project_id):
    """Only upload local Muse references when the user explicitly chooses Flow."""
    path = local_image(media_id)
    if not path:
        return media_id
    from agent.services.flow_client import get_flow_client
    content = await asyncio.to_thread(path.read_bytes)
    result = await get_flow_client().upload_image(base64.b64encode(content).decode(), mime_type='image/png', project_id=project_id)
    mid = result.get('_mediaId')
    if not mid:
        from agent.worker._parsing import _extract_media_id
        mid = _extract_media_id(result, 'GENERATE_IMAGE')
    try:
        return str(UUID(mid))
    except (ValueError, TypeError, AttributeError) as exc:
        raise MuseError('Không chuyển được ảnh Muse sang Google Flow. Kiểm tra extension rồi thử lại.') from exc


async def scene_references(scene, pid):
    from agent.sdk.services.operations import _char_matches
    names = scene.get('character_names') or []
    names = json.loads(names) if isinstance(names, str) else names
    characters = await crud.get_project_characters(pid)
    refs, labels = [], []
    for name in names:
        char = next((c for c in characters if _char_matches(c, {name})), None)
        if not char or not char.get('media_id'):
            raise MuseError(f'Chưa có ảnh tham chiếu cho {name}. Tạo ảnh tham chiếu trước.')
        if char['media_id'] not in refs:
            refs.append(char['media_id'])
            labels.append(f"Reference image {len(refs)} is {char['name']}.")
    return refs, ' '.join(labels)


def save_image(content, target):
    image_data(content)  # Size/type validation before decode and commit.
    with Image.open(io.BytesIO(content)) as image:
        image.convert('RGB').save(target, format='PNG')


async def dispatch(req, orientation):
    kind, pid, rid = req['type'], req['project_id'], req['id']
    if kind not in SUPPORTED:
        raise MuseError('Muse hỗ trợ ảnh tham chiếu, ảnh cảnh và video từ ảnh đầu; chọn Google Flow cho thao tác này.')
    client = MuseClient()
    await client.readiness()
    video = 'VIDEO' in kind
    prefix = 'vertical' if orientation == 'VERTICAL' else 'horizontal'
    refs = []
    if 'CHARACTER' in kind:
        char = await crud.get_character(req.get('character_id'))
        if not char:
            raise MuseError('Không tìm thấy nhân vật.')
        orientation = 'HORIZONTAL' if char.get('entity_type') == 'location' else 'VERTICAL'
        prompt = char.get('image_prompt') or char.get('description') or char['name']
        if kind == 'EDIT_CHARACTER_IMAGE':
            refs = [req.get('source_media_id') or char.get('media_id')]
    else:
        scene = await crud.get_scene(req.get('scene_id'))
        if not scene:
            raise MuseError('Không tìm thấy cảnh.')
        if video:
            if scene.get(f'{prefix}_end_scene_media_id'):
                raise MuseError('Muse chưa hỗ trợ ảnh kết thúc. Chọn Google Flow cho cảnh nối bằng hai ảnh.')
            prompt = scene.get('video_prompt') or scene.get('prompt', '')
            refs = [scene.get(f'{prefix}_image_media_id')]
        else:
            refs, labels = await scene_references(scene, pid)
            prompt = labels + ' Keep referenced identities and style consistent. ' + (scene.get('image_prompt') or scene.get('prompt', ''))
            if kind == 'EDIT_IMAGE':
                parent = await crud.get_scene(scene['parent_scene_id']) if scene.get('parent_scene_id') else None
                source = req.get('source_media_id') or (parent or scene).get(f'{prefix}_image_media_id')
                old_refs = refs[:]
                refs = [source] + [mid for mid in refs if mid != source]
                new_labels = re.sub(r'Reference image (\d+)', lambda m: f'Reference image {refs.index(old_refs[int(m[1])-1])+1}', labels)
                prompt = prompt.replace(labels, new_labels, 1) if labels else prompt
                prompt = 'Edit reference image 1. ' + prompt
    if len(refs) > 4:
        raise MuseError('Muse nhận tối đa 4 ảnh tham chiếu mỗi cảnh. Giảm số tham chiếu hoặc chọn Google Flow; chưa bỏ ảnh nào.')
    if any(not mid for mid in refs):
        raise MuseError('Thiếu ảnh đầu hoặc ảnh cần sửa. Tạo ảnh trước.')
    path = '/v1/videos' if video else '/v1/images/generations'
    payload = {'prompt': prompt, 'model': req.get('provider_model') or (client.settings.video_model if video else client.settings.image_model),
               'size': '9:16' if orientation == 'VERTICAL' else '16:9'}
    signature = hashlib.sha256(json.dumps([client.settings.base_url, kind, payload, refs], sort_keys=True).encode()).hexdigest()
    task_id = req.get('request_id')
    if task_id and req.get('provider_signature') != signature:
        await crud.update_request(rid, request_id=None, provider_signature=None)
        raise MuseError('Nội dung hoặc gateway đã đổi từ lần gửi trước. Chưa áp dụng kết quả cũ. Bấm thử lại để tạo theo nội dung mới.')
    if not task_id:
        await client.validate_model(payload['model'], 'video' if video else 'image')
        inputs = [await reference(mid) for mid in refs]
        if inputs:
            payload['image'] = inputs[0] if video else inputs
        payload.update({'seconds': 8} if video else {'n': 1, 'async': True, 'response_format': 'url'})
        result = await client.request('POST', path, payload=payload, timeout=90)
        task_id = result.get('id')
        if not isinstance(task_id, str) or not task_id:
            raise MuseError('Muse chưa trả mã tác vụ. Kiểm tra gateway trước khi gửi lại.')
        await crud.update_request(rid, request_id=task_id, provider_signature=signature)
    async def progress(status):
        current = await crud.get_request(rid)
        if not current or current['status'] != 'PROCESSING':
            raise asyncio.CancelledError()
    try:
        result = await client.wait(path, task_id, progress=progress)
    except MuseTaskFailed:
        await crud.update_request(rid, request_id=None)
        raise
    try:
        url = result['url'] if video else result['data'][0]['url']
    except (KeyError, IndexError, TypeError) as exc:
        raise MuseError('Muse báo hoàn tất nhưng thiếu URL tư liệu. Mã tác vụ được giữ để kiểm tra lại.') from exc
    content = await client.download(url, limit=(256 if video else 32) * 1024 * 1024)
    mid = str(uuid4())
    ROOT.mkdir(parents=True, exist_ok=True)
    target = ROOT / (mid + ('.mp4' if video else '.png'))
    temporary = target.with_suffix('.part')
    try:
        if video:
            await asyncio.to_thread(temporary.write_bytes, content)
            from agent.services import imported_video
            await imported_video.probe(temporary)
        else:
            await asyncio.to_thread(save_image, content, temporary)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    if video:
        return {'data': {'operations': [{'status': 'MEDIA_GENERATION_STATUS_SUCCESSFUL',
            'operation': {'name': mid, 'metadata': {'video': {'mediaId': mid, 'fifeUrl': target.as_uri()}}}}]}}
    return {'data': {'media': [{'name': mid, 'image': {'generatedImage': {'mediaId': mid, 'fifeUrl': target.as_uri()}}}]}}
