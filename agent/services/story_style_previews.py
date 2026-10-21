"""Local illustrations for HTML presets; never change production recipes or references."""
import asyncio
import base64
import binascii
import hashlib
import io
import os
import tempfile

from PIL import Image, ImageOps

from agent.config import SHARED_OUTPUT_DIR
from agent.story_presets import HTML_PRESETS, style_lock_sections
from agent.services import reference_images, speech_store

_lock = asyncio.Lock()


def preset(style_id):
    item = next((p for p in HTML_PRESETS if p['id'] == style_id), None)
    if not item:
        raise ValueError('Chỉ thay ảnh minh họa cho 12 mẫu từ HTML.')
    return item


def directory(style_id):
    preset(style_id)  # IDs come from the registry, never from a filesystem path.
    return SHARED_OUTPUT_DIR/'story_style_previews'/style_id


def image_path(style_id):
    return directory(style_id)/'preview.webp'


def sample_prompt(style_id):
    p = preset(style_id)
    scene = ('One character stands beside a small potted plant, holding a cup. '
             'Only essential foreground props; no room or scenery.' if p['background_type'] != 'scene' else
             'One character gently waters a potted plant beside a window. Show a coherent modest room, '
             'a chair, a shelf and a few relevant objects with layered depth. '
             'For traditional Vietnamese clothing, use a period-appropriate rural Vietnamese home.')
    return '\n\n'.join(f'{heading}:\n{text}' for heading, text in (
        ('STYLE PREVIEW IMAGE PROMPT', 'Create one finished illustration, not a UI, collage or character sheet.'),
        ('ASPECT RATIO AND COMPOSITION', '3:2 landscape thumbnail. One fictional character, full body visible including feet, '
         'centered with generous margins. Clear readable silhouette at small size.'),
        *style_lock_sections(p),
        ('ACTION AND PROPS', scene),
        ('CONSISTENCY', 'Keep the selected anatomy, background and rendering throughout. Natural connected limbs, '
         'readable face and relaxed posture. Do not copy a real person.'),
        ('NEGATIVE PROMPT', p['material_override']['negative_prompt']),
    ))


def presentation(style_id, pid):
    preset(style_id)
    result = {'sample_prompt': sample_prompt(style_id), 'preview_override': False}
    data = speech_store.read_json(directory(style_id)/'preview.json', {})
    if data.get('version') and image_path(style_id).is_file():
        result.update(preview=f'/api/projects/{pid}/stickman/presets/{style_id}/preview?v={data["version"]}',
                      preview_override=True)
    return result


def encode(content):
    try:
        if not content or len(content) > reference_images.LIMIT:
            raise ValueError('Ảnh minh họa tối đa 10 MB.')
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width*image.height > 16_000_000:
                raise ValueError('Chọn PNG/JPEG/WebP tối đa 16 megapixel.')
            if getattr(image, 'is_animated', False):
                raise ValueError('Dùng ảnh tĩnh PNG/JPEG/WebP.')
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            illustration = ImageOps.exif_transpose(image).convert('RGBA' if 'A' in image.getbands() or 'transparency' in image.info else 'RGB')
            illustration.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
            output = io.BytesIO()
            illustration.save(output, format='WEBP', quality=92)
        return output.getvalue()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('Ảnh minh họa không hợp lệ. Chọn PNG/JPEG/WebP.') from exc


async def save(style_id, pid, body):
    preset(style_id)
    try:
        content = (await reference_images.download(body.url) if body.url else
                   base64.b64decode(body.image_base64, validate=True))
    except binascii.Error as exc:
        raise ValueError('Ảnh minh họa không hợp lệ. Chọn PNG/JPEG/WebP.') from exc
    # Large photos must not block WebSocket heartbeats or other API requests.
    encoded = await asyncio.to_thread(encode, content)
    async with _lock:
        root = directory(style_id)
        root.mkdir(parents=True, exist_ok=True)
        descriptor, staged = tempfile.mkstemp(prefix='.preview-', dir=root)
        try:
            with os.fdopen(descriptor, 'wb') as file:
                file.write(encoded)
            os.replace(staged, image_path(style_id))
            speech_store.atomic_json(root/'preview.json', {'version': hashlib.sha256(encoded).hexdigest()[:16]})
        finally:
            if os.path.exists(staged):
                os.unlink(staged)
        return presentation(style_id, pid)


async def reset(style_id, pid):
    async with _lock:
        (directory(style_id)/'preview.json').unlink(missing_ok=True)
        image_path(style_id).unlink(missing_ok=True)
        return presentation(style_id, pid)
