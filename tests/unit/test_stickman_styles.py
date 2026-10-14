"""Style drafts cannot publish stale proofs or change existing productions."""
import base64
import io
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from PIL import Image
from agent.db import crud, schema
from agent.materials import MATERIALS, STORY_PRESETS, story_material
from agent.models.story_style import StyleSettings, StylePublish, StyleUpload
from agent.services import media_jobs, speech_store, stickman, stickman_direction as direction, stickman_styles as styles


def uid():
    return str(uuid.uuid4())


def settings(data, **changes):
    return StyleSettings.model_validate({**{k: v for k, v in data.items() if k in StyleSettings.model_fields}, **changes})


@pytest_asyncio.fixture
async def library(monkeypatch, tmp_path):
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path/'styles.db')
    monkeypatch.setattr(direction, 'SHARED_OUTPUT_DIR', tmp_path/'shared')
    monkeypatch.setattr(direction, 'OUTPUT_DIR', tmp_path/'output')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path/'workbench')
    await schema.init_db()
    pid, sid = uid(), uid()
    project = await crud.create_project(id=pid, name='Style library test', material='realistic')
    ref = await crud.create_character('Ảnh phong cách', entity_type='visual_asset', media_id=uid())
    await crud.link_character_to_project(pid, ref['id'])
    data = {**StyleSettings(name='Tranh chì ấm áp', style_instruction='Graphite hatching on warm paper with detailed scenery.',
        negative_prompt='No letters or watermarks.', character_design='A full-body soft cartoon person.').model_dump(mode='json'),
        'id': sid, 'reference_id': ref['id'], 'image_url': f'/api/characters/{ref["id"]}/image', 'status': 'DRAFT', 'revision': 1}
    speech_store.atomic_json(direction.profiles(pid)/f'{sid}.json', data)
    try:
        yield pid, sid, project, data
    finally:
        await media_jobs.cancel(sid)
        await schema.close_db()


def fake_worker(monkeypatch):
    calls = []
    async def generate(vid, pid, ids, kind, orientation, report, begin, span):
        calls.append((kind, orientation))
        for ident in ids:
            if kind == 'GENERATE_CHARACTER_IMAGE':
                await crud.update_character(ident, media_id=uid())
            else:
                scene = await crud.get_scene(ident)
                names = json.loads(scene['character_names'])
                refs = await crud.get_project_characters(pid)
                assert len(names) == 2 and all(next(c for c in refs if c['name'] == name)['media_id'] for name in names)
                assert 'Character A places a cup' in scene['image_prompt']
                prefix = 'vertical' if orientation == 'VERTICAL' else 'horizontal'
                await crud.update_scene(ident, **{f'{prefix}_image_media_id': uid(), f'{prefix}_image_status': 'COMPLETED'})
        report(progress=begin+span, stage=kind)
    async def image_file(sid, mid):
        target = stickman.directory(sid)/f'{mid}.image'
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (32, 48), 'ivory').save(target, format='PNG')
        return target
    monkeypatch.setattr(stickman, 'generate_images', generate)
    monkeypatch.setattr(stickman, 'image_file', image_file)
    return calls


async def finish_preview(pid, sid, project):
    job = await styles.start_preview(pid, sid, project)
    if job['status'] != 'COMPLETED':
        await media_jobs._tasks[sid]
    return media_jobs.latest(sid)


def test_new_presets_use_isolated_prompt_overrides():
    assert {'comic_book', 'claymation'} <= {p['id'] for p in STORY_PRESETS}
    before = json.loads(json.dumps(MATERIALS))
    assert 'everyday' in story_material('comic_book')['style_instruction']
    assert 'fingerprint' in story_material('claymation')['style_instruction']
    assert MATERIALS == before  # Other material consumers retain their existing prompts.


@pytest.mark.asyncio
async def test_publish_requires_current_proof_and_keeps_saved_styles_immutable(library, monkeypatch):
    pid, sid, project, data = library
    calls = fake_worker(monkeypatch)
    with pytest.raises(ValueError, match='thiếu ảnh mẫu'):
        await styles.publish(pid, sid, StylePublish(preview_signature='missing-proof'))
    job = await finish_preview(pid, sid, project)
    assert job['status'] == 'COMPLETED', job.get('error')
    assert calls == [('GENERATE_CHARACTER_IMAGE', 'VERTICAL'), ('GENERATE_IMAGE', 'VERTICAL')]
    assert await crud.list_videos(pid) == []  # Temporary sample rows do not clutter Projects.
    result = styles.proof(pid, sid)
    assert result and result['character_url'].startswith('/api/projects/')
    old_ref = result['character_id']
    saved = await styles.publish(pid, sid, StylePublish(preview_signature=result['signature'], use_preview_character=True))
    assert saved['status'] == 'PUBLISHED' and saved['default_character_id'] == old_ref
    assert styles.proof(pid, sid)
    assert (direction.OUTPUT_DIR/'character_refs'/f'{old_ref}.png').is_file()
    with pytest.raises(ValueError, match='video cũ'):
        await styles.update(pid, sid, settings(saved, style_instruction='A different full-color appearance.'))
    assert direction.profile(pid, sid) == saved


@pytest.mark.asyncio
async def test_reopening_draft_reuses_proof_and_changed_description_invalidates_it(library, monkeypatch):
    pid, sid, project, data = library
    calls = fake_worker(monkeypatch)
    await finish_preview(pid, sid, project)
    before = styles.proof(pid, sid)
    cached = await styles.start_preview(pid, sid, project)
    assert cached['status'] == 'COMPLETED' and len(calls) == 2
    updated = await styles.update(pid, sid, settings(data, name='Đổi tên mẫu'))
    assert styles.proof(pid, sid)['signature'] == before['signature']
    assert updated['revision'] == 1
    updated = await styles.update(pid, sid, settings(updated, style_instruction='Bold ink outlines on printed cream paper.'))
    assert updated['revision'] == 2 and styles.proof(pid, sid) is None
    with pytest.raises(ValueError, match='đổi hoặc thiếu'):
        await styles.publish(pid, sid, StylePublish(preview_signature=before['signature']))
    assert direction.profile(pid, sid)['status'] == 'DRAFT'


@pytest.mark.asyncio
async def test_scoping_and_default_character_readiness_are_checked(library):
    pid, sid, project, data = library
    foreign = await crud.create_character('Other project', media_id=uid())
    with pytest.raises(ValueError, match='thuộc dự án'):
        await styles.update(pid, sid, settings(data, default_character_id=foreign['id']))
    with pytest.raises(ValueError, match='không thuộc dự án'):
        styles.proof(uid(), sid)
    assert direction.profile(pid, sid) == data


@pytest.mark.asyncio
async def test_missing_style_endpoints_return_404_without_stopping_other_jobs(library):
    from fastapi import HTTPException
    from agent.api import stickman as api
    pid, _, _, _ = library
    missing = uuid.uuid4()
    for endpoint, args in [
        (api.style_proof_image, (uuid.UUID(pid), missing, 'character')),
        (api.cancel_style_preview, (uuid.UUID(pid), missing, uuid.uuid4())),
    ]:
        with pytest.raises(HTTPException) as error:
            await endpoint(*args)
        assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_edit_cleans_failed_scene_but_preserves_character_cache(library):
    pid, sid, project, data = library
    video = await crud.create_video(pid, 'Failed style sample')
    await crud.create_scene(video['id'], 1, 'Old sample action')
    data['preview_video_id'] = video['id']
    speech_store.atomic_json(direction.profiles(pid)/f'{sid}.json', data)
    result = await styles.update(pid, sid, settings(data, scene_action='Character A waters a plant beside a window.'))
    assert not await crud.get_video(video['id'])
    assert 'preview_video_id' not in result and result['revision'] == 1


@pytest.mark.asyncio
async def test_active_flow_request_blocks_edits_even_if_local_job_has_stopped(library):
    pid, sid, project, data = library
    video = await crud.create_video(pid, 'Pending sample')
    scene = await crud.create_scene(video['id'], 1, 'A cup on a table')
    await crud.create_request(req_type='GENERATE_IMAGE', video_id=video['id'], scene_id=scene['id'], project_id=pid)
    data['preview_video_id'] = video['id']
    speech_store.atomic_json(direction.profiles(pid)/f'{sid}.json', data)
    with pytest.raises(ValueError, match='còn đang xử lý'):
        await styles.update(pid, sid, settings(data, name='Do not change pending sample'))
    assert direction.profile(pid, sid) == data


@pytest.mark.asyncio
async def test_failed_preview_can_retry_and_does_not_publish(library, monkeypatch):
    pid, sid, project, data = library
    monkeypatch.setattr(stickman, 'generate_images', AsyncMock(side_effect=ValueError('Flow rejected the model')))
    job = await finish_preview(pid, sid, project)
    assert job['status'] == 'FAILED' and 'Flow rejected' in job['error']
    assert styles.proof(pid, sid) is None and direction.profile(pid, sid)['status'] == 'DRAFT'
    calls = fake_worker(monkeypatch)
    assert (await finish_preview(pid, sid, project))['status'] == 'COMPLETED'
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_same_uploaded_image_is_analyzed_once_across_saved_drafts(library, monkeypatch):
    from agent.api import projects
    pid, sid, project, data = library
    image = io.BytesIO()
    Image.new('RGB', (32, 32), 'white').save(image, format='PNG')
    complete = AsyncMock(return_value=direction.VisualStyle.model_validate({k: data[k] for k in direction.VisualStyle.model_fields}).model_dump_json())
    monkeypatch.setattr(direction.ai_tasks, 'complete', complete)
    monkeypatch.setattr(projects, '_REFERENCE_DIR', direction.OUTPUT_DIR/'character_refs')
    client = SimpleNamespace(connected=True, upload_image=AsyncMock(return_value={'_mediaId': uid()}))
    monkeypatch.setattr(projects, 'get_flow_client', lambda: client)
    upload = StyleUpload(name='Mẫu riêng', image_base64=base64.b64encode(image.getvalue()).decode(), draft=True)
    first = await direction.save_profile(pid, upload)
    second = await direction.save_profile(pid, upload)
    assert first['id'] != second['id'] and first['status'] == second['status'] == 'DRAFT'
    complete.assert_awaited_once()
