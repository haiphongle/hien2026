"""Story-level reference inventory and the full storyboard approval boundary."""
import json
import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from agent.api import stickman as api
from agent.db import crud
from agent.models.stickman import CreateRequest, PlanRequest, RetryRequest, PromptPreviewRequest
from agent.services import media_jobs, speech_store, stickman, stickman_direction as direction, stickman_production as production


def story(**changes):
    return CreateRequest.model_validate({
        'title': 'Một ngày trở về', 'preset_id': 'v3_test', 'motion': 'ai', 'preview_first': True, 'preview_all': True,
        'visual_locks': {'background': 'scene'},
        'characters': [{'role': role, 'name': name, 'description': f'Consistent illustrated {name}.'}
                       for role, name in [('A', 'Mother'), ('B', 'Father'), ('C', 'Child')]],
        'story_assets': [{'key': 'L1', 'kind': 'location', 'name': 'Home', 'description': 'A small tiled family home.'},
                         {'key': 'P1', 'kind': 'prop', 'name': 'Bicycle', 'description': 'A blue bicycle with a wicker basket.'}],
        'scenes': [
            {'narration': 'Đứa trẻ trở về bên mẹ.', 'action': 'Character C approaches Character A.', 'cast': ['A', 'C'], 'assets': ['L1'], 'extras': 'Two neighbors walk along the distant street.'},
            {'narration': 'Căn nhà vẫn ở đó.', 'action': 'Morning light falls on the empty porch.', 'cast': [], 'shot_kind': 'environment', 'assets': ['L1']},
            {'narration': 'Chiếc xe gợi nhớ tuổi thơ.', 'action': 'The bicycle basket sways gently in the breeze.', 'cast': [], 'shot_kind': 'detail', 'assets': ['P1']},
        ], **changes})


def test_many_visual_roles_share_one_offscreen_voice_and_reference_inventory():
    planned = story()
    assert not planned.dialogue and {s.speaker for s in planned.scenes} == {'A'}
    assert direction.roles(planned) == ['A', 'C']  # Father is not visible; no paid portrait needed.
    specs = direction.reference_specs(planned, 'project')
    assert set(specs) == {'A', 'C', 'L1', 'P1'}
    assert specs['A'][-1] == 'character' and specs['L1'][-1] == 'location' and specs['P1'][-1] == 'visual_asset'
    prompt, names = direction.scene_prompt(planned, planned.scenes[0], 'project', {key: {'name': value[1]} for key, value in specs.items()})
    assert len(names) == 3 and specs['P1'][1] not in names
    assert 'background people' in prompt and 'Two neighbors' in prompt
    assert 'Mother.' not in prompt and 'Consistent illustrated' not in prompt  # Identity is attached separately.
    motion = production.video_prompt(planned.scenes[0], planned, 'project')
    assert 'Animate the approved storyboard' in motion and 'Character C' in motion
    assert 'No spoken dialogue' in motion


@pytest.mark.parametrize('change', [
    {'cast': ['D']}, {'assets': ['P4']}, {'assets': ['L1', 'L1']},
    {'shot_kind': 'environment'}, {'cast': [], 'shot_kind': 'detail', 'extras': 'A crowd.'},
])
def test_invalid_assignments_fail_before_generation(change):
    data = story().model_dump(mode='json')
    data['scenes'][0].update(change)
    with pytest.raises(ValidationError):
        CreateRequest.model_validate(data)


def test_only_relevant_reference_changes_invalidate_a_scene():
    planned = story()
    before = [production.image_signature(planned, s) for s in planned.scenes]
    planned.story_assets[1].description = 'A red bicycle with a wicker basket.'
    after = [production.image_signature(planned, s) for s in planned.scenes]
    assert before[:2] == after[:2] and before[2] != after[2]
    planned.characters[2].reference_id = uuid.uuid4()
    assert production.image_signature(planned, planned.scenes[0]) != after[0]
    assert production.image_signature(planned, planned.scenes[1]) == after[1]


@pytest.mark.asyncio
async def test_ai_preserves_srt_and_cannot_invent_reference_assignments(monkeypatch):
    planned = story()
    data = planned.model_dump(mode='json', include={'title', 'characters', 'story_assets', 'scenes', 'analysis'})
    selected = uuid.uuid4()
    for design in data['characters']:
        design['reference_id'] = str(uuid.uuid4())
    for asset in data['story_assets']:
        asset['reference_id'] = str(uuid.uuid4())
    for i, scene in enumerate(data['scenes'], 1):
        scene.update(cue_start=i, cue_end=i)
        scene.pop('narration')
    source = '\n\n'.join(f'{i}\n00:00:0{i-1},000 --> 00:00:0{i},000\n{s.narration}' for i, s in enumerate(planned.scenes, 1))
    complete = AsyncMock(return_value=json.dumps(data))
    monkeypatch.setattr(stickman.ai_tasks, 'complete', complete)
    request = PlanRequest(source=source, characters=[{**planned.characters[2].model_dump(), 'reference_id': selected}])
    result = await stickman.plan(request)
    assert [s.narration for s in result.scenes] == [s.narration for s in planned.scenes]
    assert result.characters[0].reference_id is None and result.characters[2].reference_id == selected
    assert all(a.reference_id is None for a in result.story_assets)
    prompt = complete.call_args.args[1]
    assert 'ENTIRE source' in prompt and 'OFFSCREEN' in prompt and 'Character A through F' in prompt


@pytest.mark.asyncio
async def test_reference_preview_is_readonly_and_rejects_foreign_location(monkeypatch):
    planned = story()
    request = PromptPreviewRequest(source='Một câu chuyện.', **planned.model_dump(mode='json', include=set(PromptPreviewRequest.model_fields)))
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=[]))
    create = AsyncMock()
    monkeypatch.setattr(crud, 'create_character', create)
    bundle = await direction.prompt_bundle('project', request)
    assert {r['role'] for r in bundle['characters']} == {'A', 'C', 'L1', 'P1'}
    assert [r['role'] for r in bundle['scenes'][1]['references']] == ['L1']
    create.assert_not_called()
    request.story_assets[0].reference_id = uuid.uuid4()
    with pytest.raises(ValueError, match='không thuộc dự án'):
        await direction.prompt_bundle('project', request)
    create.assert_not_called()


@pytest.mark.asyncio
async def test_full_board_batches_references_before_images_and_regenerates_only_one(monkeypatch, tmp_path):
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path)
    planned = story()
    pid, vid = str(uuid.uuid4()), str(uuid.uuid4())
    specs = direction.reference_specs(planned, pid)
    refs = {key: {'id': str(uuid.uuid4()), 'name': spec[1], 'entity_type': spec[-1]} for key, spec in specs.items()}
    rows = [{'id': str(uuid.uuid4()), 'vertical_image_status': 'PENDING'} for _ in planned.scenes]
    monkeypatch.setattr(stickman, 'reference', AsyncMock(side_effect=lambda project, body, key: refs[key]))
    monkeypatch.setattr(crud, 'get_character', AsyncMock(side_effect=lambda ident: next(r for r in refs.values() if r['id'] == ident)))
    monkeypatch.setattr(crud, 'update_scene', AsyncMock())
    monkeypatch.setattr(crud, 'list_scenes', AsyncMock(return_value=rows))
    calls = []
    async def generate(vid, pid, ids, kind, orientation, update, begin, span, on_completed=None, **kwargs):
        calls.append((kind, orientation, ids))
        if kind == 'GENERATE_CHARACTER_IMAGE':
            for ref in refs.values():
                if ref['id'] in ids:
                    ref['media_id'] = str(uuid.uuid4())
        else:
            assert all(r.get('media_id') for r in refs.values())
            for row in rows:
                if row['id'] in ids:
                    row.update(vertical_image_media_id=str(uuid.uuid4()), vertical_image_status='COMPLETED')
                    if on_completed:
                        await on_completed({'scene_id': row['id']})
    monkeypatch.setattr(stickman, 'generate_images', generate)
    async def local_image(vid, mid):
        target = stickman.directory(vid)/f'{mid}.image'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'persisted image')
        return target
    monkeypatch.setattr(stickman, 'image_file', local_image)
    assert (await production.prepare(vid, planned, {'id': pid}, rows, [], lambda **_: None))[1] is None
    assert [(kind, orientation) for kind, orientation, _ in calls] == [
        ('GENERATE_CHARACTER_IMAGE', 'VERTICAL'), ('GENERATE_CHARACTER_IMAGE', 'HORIZONTAL'), ('GENERATE_IMAGE', 'VERTICAL')]
    assert len(calls[-1][2]) == 3 and len(await production.storyboard_images(vid, planned)) == 3
    first_ids = [r['vertical_image_media_id'] for r in rows]
    calls.clear()
    speech_store.atomic_json(stickman.directory(vid)/'preview.json', {'approved': False, 'regenerate': True, 'scene_id': rows[1]['id']})
    await production.prepare(vid, planned, {'id': pid}, rows, [], lambda **_: None)
    assert calls == [('REGENERATE_IMAGE', 'VERTICAL', [rows[1]['id']])]
    assert rows[0]['vertical_image_media_id'] == first_ids[0] and rows[2]['vertical_image_media_id'] == first_ids[2]
    assert rows[1]['vertical_image_media_id'] != first_ids[1]
    calls.clear()
    await production.prepare(vid, planned, {'id': pid}, rows, [], lambda **_: None)
    assert not calls  # Reload/retry uses all completed images.


@pytest.mark.asyncio
async def test_board_approval_requires_every_scene_and_ownership(monkeypatch, tmp_path):
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path)
    planned = story()
    pid, vid, jid = map(str, [uuid.uuid4() for _ in range(3)])
    monkeypatch.setattr(api, 'saved_video', AsyncMock(return_value=({'project_id': pid}, planned)))
    monkeypatch.setattr(api, 'project', AsyncMock(return_value={'id': pid}))
    monkeypatch.setattr(api, 'ensure_idle', lambda _: None)
    monkeypatch.setattr(api, 'flow_ready', lambda: None)
    monkeypatch.setattr(media_jobs, 'latest', lambda _: {'id': jid, 'kind': 'stickman', 'status': 'COMPLETED', 'result': {'preview_url': '/image'}})
    monkeypatch.setattr(api.timeline, 'load', lambda _: None)
    monkeypatch.setattr(stickman, 'validate', AsyncMock())
    monkeypatch.setattr(crud, 'list_requests', AsyncMock(return_value=[]))
    monkeypatch.setattr(crud, 'list_scenes', AsyncMock(return_value=[{'id': str(uuid.uuid4())}]))
    monkeypatch.setattr(api, 'storyboard_images', AsyncMock(return_value=[{}]))
    start = Mock(return_value={'status': 'QUEUED'})
    monkeypatch.setattr(stickman, 'start', start)
    with pytest.raises(HTTPException) as exc:
        await api.retry(uuid.UUID(vid), RetryRequest(job_id=jid, approve_preview=True))
    assert exc.value.status_code == 422 and not speech_store.read_json(stickman.directory(vid)/'preview.json', {}).get('approved')
    with pytest.raises(HTTPException) as exc:
        await api.retry(uuid.UUID(vid), RetryRequest(job_id=jid, regenerate_preview=True, preview_scene_id=uuid.uuid4()))
    assert exc.value.status_code == 422
    start.assert_not_called()
    monkeypatch.setattr(api, 'storyboard_images', AsyncMock(return_value=[{}, {}, {}]))
    assert (await api.retry(uuid.UUID(vid), RetryRequest(job_id=jid, approve_preview=True)))['status'] == 'QUEUED'
    assert speech_store.read_json(stickman.directory(vid)/'preview.json')['approved']


@pytest.mark.asyncio
async def test_full_board_defers_voice_and_video_until_approval(monkeypatch, tmp_path):
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path)
    planned = story(motion='zoom')
    vid = str(uuid.uuid4())
    monkeypatch.setattr(stickman.timeline, 'load', lambda _: None)
    monkeypatch.setattr(crud, 'update_video', AsyncMock())
    monkeypatch.setattr(crud, 'list_scenes', AsyncMock(return_value=[]))
    voice = AsyncMock()
    monkeypatch.setattr(stickman, 'generate_voices', voice)
    prepare = AsyncMock(return_value=([], None, None))
    monkeypatch.setattr(production, 'prepare', prepare)
    captured = []
    monkeypatch.setattr(media_jobs, 'start', lambda vid, kind, run, **kwargs: captured.append(run))
    stickman.start(vid, planned, {'id': str(uuid.uuid4())})
    await captured[0](lambda **_: None)
    voice.assert_not_called()
    assert prepare.call_args.args[4] == []
