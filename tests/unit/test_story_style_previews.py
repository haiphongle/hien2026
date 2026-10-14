"""Illustrations persist independently of recipes, AI references and production cache keys."""
import base64
import copy
import io
import uuid
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from PIL import Image
from pydantic import ValidationError

from agent.api import stickman as api
from agent.materials import MATERIALS
from agent.models.stickman import CreateRequest
from agent.models.story_style import StylePreviewUpload
from agent.services import story_style_previews as previews
from agent.services import stickman_direction as direction, stickman_production as production
from agent.story_presets import HTML_PRESETS, ANATOMY, BACKGROUNDS, RENDERING, COSTUMES


def picture(size=(96, 64), mode='RGB', color='white'):
    output = io.BytesIO()
    Image.new(mode, size, color).save(output, 'PNG')
    return output.getvalue()


def upload(content=None):
    return StylePreviewUpload(image_base64=base64.b64encode(content or picture()).decode())


@pytest.fixture
def storage(monkeypatch, tmp_path):
    monkeypatch.setattr(previews, 'SHARED_OUTPUT_DIR', tmp_path)
    return str(uuid.uuid4())


def test_all_twelve_prompts_use_the_actual_recipe_and_have_different_anatomy_and_backgrounds():
    assert len(HTML_PRESETS) == 12
    for preset in HTML_PRESETS:
        text = previews.sample_prompt(preset['id'])
        for source in (ANATOMY[preset['anatomy']], BACKGROUNDS[preset['background_type']],
                       RENDERING[preset['rendering']], COSTUMES[preset['costume']],
                       preset['material_override']['negative_prompt']):
            assert source in text
        assert '3:2' in text and 'full body visible including feet' in text
    white = previews.sample_prompt('srt_stick_white')
    assert 'thin single-line arms and legs' in white and 'Pure white background' in white
    assert 'Full-body 2D cartoon humans' in previews.sample_prompt('srt_human_white')


@pytest.mark.asyncio
async def test_save_reload_and_reset_do_not_touch_generation_prompts_or_shared_registry(storage):
    pid, style_id = storage, 'srt_stick_white'
    materials, presets = copy.deepcopy(MATERIALS), copy.deepcopy(HTML_PRESETS)
    body = CreateRequest(style=style_id, title='Một ngày yên bình', preset_id='test_voice',
                         scenes=[{'narration': 'Một chút bình yên.', 'action': 'Character A holds a cup.', 'cast': ['A']}])
    def production_state():
        return (direction.image_prompt(body, body.scenes[0], pid), production.video_prompt(body.scenes[0], body, pid),
                production.image_signature(body, body.scenes[0]), direction.reference_tag(body, 'A'))
    before = production_state()
    assert not previews.presentation(style_id, pid)['preview_override']
    saved = await previews.save(style_id, pid, upload())
    assert saved['preview_override'] and '?v=' in saved['preview']
    assert previews.presentation(style_id, pid) == saved
    with Image.open(previews.image_path(style_id)) as image:
        assert image.format == 'WEBP' and image.size == (96, 64)
    other_pid = str(uuid.uuid4())
    assert f'/projects/{other_pid}/' in previews.presentation(style_id, other_pid)['preview']
    assert production_state() == before and MATERIALS == materials and HTML_PRESETS == presets
    await previews.reset(style_id, pid)
    assert not previews.image_path(style_id).exists()
    assert not previews.presentation(style_id, pid)['preview_override']
    assert production_state() == before


@pytest.mark.asyncio
async def test_url_reuses_bounded_importer_and_stores_pixels_instead_of_remote_link(storage, monkeypatch):
    download = AsyncMock(return_value=picture((2400, 1200), 'RGBA', '#ffffff80'))
    monkeypatch.setattr(previews.reference_images, 'download', download)
    saved = await previews.save('srt_human_white', storage, StylePreviewUpload(url=' https://example.com/photo.png '))
    download.assert_awaited_once_with('https://example.com/photo.png')
    assert saved['preview'].startswith('/api/') and 'example.com' not in saved['preview']
    with Image.open(previews.image_path('srt_human_white')) as image:
        assert image.size == (1280, 640) and 'A' in image.getbands()


@pytest.mark.asyncio
async def test_invalid_replacement_keeps_the_previous_image_and_repeated_saves_do_not_accumulate_files(storage):
    style_id = 'srt_stick_black'
    await previews.save(style_id, storage, upload())
    before = previews.image_path(style_id).read_bytes()
    for bad in ('not-base64', base64.b64encode(b'<html>not an image</html>').decode()):
        with pytest.raises(ValueError, match='không hợp lệ'):
            await previews.save(style_id, storage, StylePreviewUpload(image_base64=bad))
        assert previews.image_path(style_id).read_bytes() == before
    changed = await previews.save(style_id, storage, upload(picture(color='black')))
    assert changed['preview'] != previews.presentation('srt_stick_white', storage).get('preview')
    assert len(list(previews.directory(style_id).iterdir())) == 2


@pytest.mark.parametrize('data', [{}, {'image_base64': 'x', 'url': 'https://example.com/a.png'}, {'url': '  '}])
def test_upload_requires_exactly_one_source(data):
    with pytest.raises(ValidationError):
        StylePreviewUpload(**data)
    assert StylePreviewUpload(image_base64='x', url=' ').url is None


@pytest.mark.asyncio
@pytest.mark.parametrize('style_id', ['../escape', 'anime', 'srt_unknown'])
async def test_only_html_ids_are_accepted_before_any_download(storage, monkeypatch, style_id):
    download = AsyncMock()
    monkeypatch.setattr(previews.reference_images, 'download', download)
    with pytest.raises(ValueError, match='12 mẫu'):
        await previews.save(style_id, storage, StylePreviewUpload(url='https://example.com/a.png'))
    download.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_upload_works_without_flow_and_project_validation_is_enforced(storage, monkeypatch):
    monkeypatch.setattr(api, 'project', AsyncMock(return_value={'id': storage}))
    # Illustration upload must never call the AI/Flow readiness gate.
    def forbidden():
        raise AssertionError('Illustrations must not use Flow')
    monkeypatch.setattr(api, 'flow_ready', forbidden)
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    route = f'/api/projects/{storage}/stickman/presets/srt_stick_white/preview'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.get(route)).status_code == 404
        assert (await client.put(route, json=upload().model_dump(exclude_none=True))).status_code == 200
        image = await client.get(route)
        assert image.status_code == 200 and image.headers['content-type'] == 'image/webp'
        assert (await client.put(route, json={'url': 'https://example.com/a.png', 'image_base64': 'x'})).status_code == 422
        assert (await client.delete(route)).status_code == 200
        assert (await client.get(route)).status_code == 404
    api.project.assert_awaited()
