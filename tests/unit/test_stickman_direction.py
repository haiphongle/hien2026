"""Regression gates for SRT direction, reusable references and the preview checkpoint."""
import json
import uuid
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from agent.api import stickman as api
from agent.models.stickman import CreateRequest, PlanRequest, RetryRequest, Storyboard, SceneSuggestionRequest, PromptPreviewRequest, ProductionTemplate
from agent.services import media_jobs, speech_store, stickman, stickman_direction as direction, stickman_production
from agent.db import crud


def body(**changes):
    return CreateRequest(title='Giữ hơi ấm', style='anime_monochrome', motion='ai', preset_id='v3_test',
        characters=[{'role': 'A', 'name': 'Người chồng', 'description': 'An adult with a plain shirt.'},
                    {'role': 'B', 'name': 'Người vợ', 'description': 'An adult with tied hair and a plain cardigan.'}],
        scenes=[{'narration': 'Hơn một câu nói, mất một tấm lòng.', 'action': 'Character A and Character B sit across a table.',
                 'cast': ['A', 'B'], 'motion_beats': ['Both set their cups down.', 'Both turn away from each other.', 'The cups cool in silence.'],
                 'camera': 'Slow push-in, vertical composition.'}], **changes)


def test_two_visual_characters_do_not_require_two_voices():
    settings = body()
    assert direction.roles(settings) == ['A', 'B']
    assert not settings.dialogue and settings.second_preset_id is None
    assert direction.cast(settings, settings.scenes[0]) == ['A', 'B']
    assert '0-3s: Both set' in stickman_production.video_prompt(settings.scenes[0])
    assert '3-6s: Both turn' in stickman_production.video_prompt(settings.scenes[0])


def test_old_character_reference_and_image_cache_keys_survive_optional_fields():
    settings = body()
    # Reproduce the pre-upgrade key, without the new optional identity/style fields.
    design = settings.characters[0].model_dump(include={'role', 'name', 'description'})
    old_reference = stickman.reference_tag(settings.style, 'A') + ':' + stickman.digest(['None', design])
    assert direction.reference_tag(settings, 'A') == old_reference
    old_image = stickman.digest([settings.style, settings.orientation, 'None', 'None', settings.dialogue,
        settings.scenes[0].action, 'None', [c.model_dump(include={'role', 'name', 'description'}) for c in settings.characters],
        settings.scenes[0].cast, settings.scenes[0].camera])
    assert stickman_production.image_signature(settings, settings.scenes[0]) == old_image
    settings.characters[0].clothing = 'Plain navy shirt'
    assert direction.reference_tag(settings, 'A') != old_reference
    assert stickman_production.image_signature(settings, settings.scenes[0]) != old_image


def test_identity_and_costume_only_enter_reference_prompt_and_context_does_not_force_regeneration():
    settings = body(visual_locks={'anatomy': 'human', 'background': 'scene', 'costume': 'vietnamese', 'context': 'A modern Vietnamese home.'})
    settings.characters[1].gender = 'female'
    settings.characters[1].age_group = 'adult'
    settings.characters[1].clothing = 'Plain white cardigan'
    name, tag, portrait = direction.reference_recipe(settings, 'B', 'project')
    image, _ = direction.scene_prompt(settings, settings.scenes[0], 'project', {'A': {'name': 'Husband'}, 'B': {'name': name}})
    assert 'Gender: female' in portrait and 'Fixed clothing: Plain white cardigan' in portrait
    assert 'Vietnamese everyday clothing' in portrait
    assert 'full-body character using the authoritative anatomy' in portrait
    assert 'Coherent illustrated story environments' in image
    assert 'cardigan' not in image and 'Gender:' not in image and 'Vietnamese everyday clothing' not in image
    before = stickman_production.image_signature(settings, settings.scenes[0])
    settings.visual_locks.context = 'A different planning note.'
    assert direction.reference_tag(settings, 'B') == tag
    assert stickman_production.image_signature(settings, settings.scenes[0]) == before
    settings.visual_locks.background = 'white'
    assert direction.reference_tag(settings, 'B') != tag
    assert stickman_production.image_signature(settings, settings.scenes[0]) != before


@pytest.mark.asyncio
async def test_prompt_board_is_readonly_and_matches_real_production_for_cast_and_object_shots(monkeypatch, tmp_path):
    monkeypatch.setattr(direction, 'SHARED_OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path/'jobs')
    pid, vid, profile_id = map(str, [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
    refs = {role: {'id': str(uuid.uuid4()), 'name': f'Reference {role}', 'entity_type': 'character',
                   'media_id': str(uuid.uuid4()), 'image_prompt': f'Existing portrait {role}'} for role in ['A', 'B', 'style']}
    speech_store.atomic_json(direction.profiles(pid)/f'{profile_id}.json', {
        'name': 'Tranh màu', 'style_instruction': 'Soft watercolor illustration.', 'negative_prompt': 'No text.',
        'character_design': 'A consistent illustrated character.', 'reference_id': refs['style']['id']})
    planned = body(character_id=refs['A']['id'], second_character_id=refs['B']['id'], style_profile_id=profile_id, preview_first=True)
    planned.scenes.append(planned.scenes[0].model_copy(update={'cast': [], 'action': 'Two cups cool on a wooden table.'}))
    request = PromptPreviewRequest(source='Hãy giữ hơi ấm.', **planned.model_dump(include={
        'scenes', 'characters', 'style', 'orientation', 'character_id', 'second_character_id', 'style_profile_id', 'motion'}))
    monkeypatch.setattr(api, 'project', AsyncMock(return_value={'id': pid}))
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=list(refs.values())))
    create, generate, update = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(crud, 'create_character', create)
    monkeypatch.setattr(stickman, 'generate_images', generate)
    monkeypatch.setattr(crud, 'update_scene', update)
    bundle = await api.preview_prompts(uuid.UUID(pid), request)
    create.assert_not_called(); generate.assert_not_called(); update.assert_not_called()
    assert [(c['role'], c['media_id']) for c in bundle['characters']] == [(r, refs[r]['media_id']) for r in ['A', 'B']]
    assert [r['role'] for r in bundle['scenes'][0]['references']] == ['A', 'B', 'style']
    assert [r['role'] for r in bundle['scenes'][1]['references']] == ['style']
    assert 'Character A is' not in bundle['scenes'][1]['image_prompt']
    rows = [{'id': str(uuid.uuid4()), 'vertical_image_media_id': str(uuid.uuid4()), 'vertical_image_status': 'COMPLETED'} for _ in planned.scenes]
    monkeypatch.setattr(crud, 'get_character', AsyncMock(side_effect=lambda ident: next(c for c in refs.values() if c['id'] == ident)))
    monkeypatch.setattr(crud, 'list_scenes', AsyncMock(return_value=rows))
    monkeypatch.setattr(stickman, 'image_file', AsyncMock())
    await stickman_production.prepare(vid, planned, {'id': pid}, rows, [], lambda **_: None)
    for call, scene in zip(update.call_args_list, bundle['scenes']):
        assert call.kwargs['image_prompt'] == scene['image_prompt']
        assert call.kwargs['video_prompt'] == scene['video_prompt']
        assert json.loads(call.kwargs['character_names']) == [r['name'] for r in scene['references']]
    create.assert_not_called(); generate.assert_not_called()


@pytest.mark.asyncio
async def test_pending_prompt_board_and_foreign_references_do_not_create_portraits(monkeypatch):
    planned = body()
    request = PromptPreviewRequest(source='Hãy giữ hơi ấm.', scenes=planned.scenes, characters=planned.characters)
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=[]))
    create = AsyncMock()
    monkeypatch.setattr(crud, 'create_character', create)
    bundle = await direction.prompt_bundle('project', request)
    assert all(c['media_id'] is None and c['reference_id'] is None for c in bundle['characters'])
    for c in bundle['characters']:
        assert c['name'] in bundle['scenes'][0]['image_prompt']
    request.character_id = uuid.uuid4()
    with pytest.raises(ValueError, match='không thuộc dự án'):
        await direction.prompt_bundle('project', request)
    create.assert_not_called()


@pytest.mark.asyncio
async def test_production_template_persists_locks_and_identity_without_scene_or_source_duplication(monkeypatch, tmp_path):
    monkeypatch.setattr(api, 'SHARED_OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(api, 'project', AsyncMock(return_value={}))
    planned = body(visual_locks={'rendering': 'hand', 'context': 'A modern home.'})
    planned.characters[1].clothing = 'Plain cardigan'
    result = await api.save_template(uuid.uuid4(), ProductionTemplate(name='Mẫu gia đình', settings=planned))
    assert result['settings']['visual_locks']['rendering'] == 'hand'
    assert result['settings']['characters'][1]['clothing'] == 'Plain cardigan'
    assert 'scenes' not in result['settings'] and 'source' not in result['settings']
    assert CreateRequest.model_validate({**planned.model_dump(), **result['settings']}) == planned


def test_camera_preset_applies_to_legacy_clips_without_explicit_beats():
    scene = body().scenes[0].model_copy(update={'motion_beats': [], 'camera': 'Camera pulls back smoothly.'})
    prompt = stickman_production.video_prompt(scene)
    assert 'Camera pulls back smoothly.' in prompt
    assert 'Steady camera' not in prompt
    assert all(mark in prompt for mark in ['0-3s:', '3-6s:', '6-8s:'])


def test_ai_timestamp_prefixes_do_not_duplicate_production_timing():
    proposed = direction.ShotSuggestion(action='Both set cups on a table.', camera='Slow push-in.',
        motion_beats=['0–3s: Both sit down.', '3-6s: They reach for the cups.', '6—8s: The cups are set down.'])
    scene = body().scenes[0].model_copy(update=proposed.model_dump())
    prompt = stickman_production.video_prompt(scene)
    assert proposed.motion_beats[0] == 'Both sit down.'
    assert prompt.count('0-3s:') == prompt.count('3-6s:') == prompt.count('6-8s:') == 1


def test_empty_cast_and_old_dialogue_remain_distinct_after_serialization():
    settings = body()
    settings.scenes[0].cast = []
    assert direction.cast(settings, settings.scenes[0]) == []
    assert direction.roles(settings) == []  # An offscreen narrator needs no portrait generation.
    data = settings.model_dump()
    data.update(dialogue=True, second_preset_id='v3_second')
    data['scenes'][0]['cast'] = None
    old = CreateRequest.model_validate(data)
    assert direction.cast(old, old.scenes[0]) == ['A', 'B']
    assert direction.cast(CreateRequest.model_validate(old.model_dump()), old.scenes[0]) == ['A', 'B']


@pytest.mark.parametrize('updates', [{'cast': ['B', 'B']}, {'motion_beats': ['Too few']}])
def test_bad_director_output_is_rejected(updates):
    data = body().model_dump()
    data['scenes'][0].update(updates)
    with pytest.raises(ValidationError):
        CreateRequest.model_validate(data)


def test_long_narration_splits_without_losing_words_or_style():
    settings = body()
    settings.scenes[0].narration = ' '.join(f'từ{i}' for i in range(67))
    result = direction.fit_shots(settings)
    assert len(result.scenes) == 3
    assert ' '.join(s.narration for s in result.scenes) == settings.scenes[0].narration
    assert all(len(s.narration.split()) <= 28 for s in result.scenes)
    assert result.scenes[1].camera != settings.scenes[0].camera
    assert result.characters == settings.characters
    assert [s.action for s in result.scenes] == settings.scenes[0].motion_beats
    assert len({tuple(s.motion_beats) for s in result.scenes}) == 3


def test_detailed_direction_roundtrips_and_invalidates_only_visual_cache():
    planned = body(analysis={'summary': 'A couple learns to reconcile.', 'environment': 'The same small Vietnamese home.',
                             'era': 'Present day', 'continuity': 'Keep the same table and tea cups.'})
    scene = planned.scenes[0]
    scene.environment = 'Living room at dusk.'
    scene.staging = 'A sits left; B sits right; two tea cups remain on the table.'
    scene.lighting = 'Soft light from the window on the left.'
    scene.continuity = 'The cups and seating match the previous shot.'
    planned.characters[1].clothing = 'White cardigan'
    loaded = CreateRequest.model_validate_json(planned.model_dump_json())
    image, names = direction.scene_prompt(loaded, loaded.scenes[0], 'project', {'A': {'name': 'Husband'}, 'B': {'name': 'Wife'}})
    video = stickman_production.video_prompt(loaded.scenes[0], loaded, 'project')
    assert names == ['Husband', 'Wife'] and '2 visible characters' in image
    for detail in (scene.environment, scene.staging, scene.lighting, scene.continuity, planned.analysis.environment):
        # A scene-specific location takes precedence over the general story location.
        if detail != planned.analysis.environment:
            assert detail in image and detail in video
    assert 'White cardigan' not in image and 'White cardigan' not in video
    assert scene.narration not in image and '0-3s:' in video and '6-8s:' in video
    before = stickman_production.image_signature(loaded, loaded.scenes[0])
    reference = direction.reference_tag(loaded, 'B')
    loaded.scenes[0].staging = 'A and B sit beside each other.'
    assert stickman_production.image_signature(loaded, loaded.scenes[0]) != before
    assert direction.reference_tag(loaded, 'B') == reference


def test_explicit_visual_locks_replace_conflicting_sample_and_portrait_has_no_scene():
    planned = body(visual_locks={'anatomy': 'human', 'background': 'white', 'rendering': 'folk'})
    image = direction.image_prompt(planned, planned.scenes[0], 'project')
    positive = image.split('NEGATIVE PROMPT:')[0]
    assert 'Pure white backdrop' in positive and 'dark charcoal' not in positive
    assert 'Full-bodied cartoon people' in positive and 'folk-inspired' in positive
    assert 'No stick figures' in image
    portrait = direction.reference_recipe(planned, 'A', 'project')[2]
    assert 'Full-bodied cartoon people' in portrait
    assert 'story environments' not in portrait and 'no rooms' not in portrait.split('NEGATIVE PROMPT:')[0].lower()
    assert 'plain empty background' in portrait


@pytest.mark.parametrize('style, technique, anatomy', [
    ('stickman_light', 'Minimal flat black line illustration', 'thin line arms and legs'),
    ('stickman_dark', 'Minimal flat white line illustration', 'thin line arms and legs'),
    ('stickman_chalk', 'Hand-drawn luminous chalk illustration', 'thin line arms and legs'),
    ('stickman_pencil', 'soft graphite hatching', 'thin curved line arms and legs'),
    ('cartoon_light', 'Minimal expressive 2D monochrome cartoon', 'rounded solid torsos'),
])
def test_story_identity_preserves_preset_anatomy_and_drawing_technique(style, technique, anatomy):
    planned = body()
    planned.style = style
    prompt = direction.reference_recipe(planned, 'A', 'project')[2]
    positive = prompt.split('NEGATIVE PROMPT:')[0]
    assert technique in positive and anatomy in positive
    assert 'Story character identity: Người chồng' in positive
    assert 'richly detailed landscapes' not in positive
    assert 'plain empty background' in positive


@pytest.mark.asyncio
async def test_selected_wrong_reference_cannot_override_wife_even_if_ai_claims_compatible(monkeypatch):
    ref_id = str(uuid.uuid4())
    row = {'id': ref_id, 'name': 'Phú ông', 'entity_type': 'character', 'media_id': str(uuid.uuid4()),
           'image_prompt': 'Adult man, neatly styled hair, traditional silk clothing.'}
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=[row]))
    reply = body()
    reply.characters[1].reference_status = 'compatible'
    complete = AsyncMock(return_value=reply.model_dump_json(include={'title', 'characters', 'scenes', 'analysis'}))
    monkeypatch.setattr(stickman.ai_tasks, 'complete', complete)
    with pytest.raises(ValueError, match='không phù hợp vai Người vợ'):
        await stickman.plan(PlanRequest(source='Vợ chồng hãy nhường nhau.', second_character_id=ref_id), str(uuid.uuid4()))
    assert 'ENTIRE source as ONE story' in complete.call_args.args[1]
    assert 'analysis' in complete.call_args.kwargs['schema']['required']


def test_unknown_reference_is_not_guessed_and_non_spouse_roles_are_checked():
    planned = body()
    wife = planned.characters[1]
    wife.story_role, wife.gender = 'mother', 'unknown'
    direction.check_reference(wife, {'name': 'Ảnh tải lên', 'image_prompt': ''})
    with pytest.raises(ValueError, match='không phù hợp'):
        direction.check_reference(wife, {'name': 'Ảnh tải lên', 'image_prompt': 'A male character.'})
    wife.gender = 'male'
    with pytest.raises(ValueError, match='sai giới tính'):
        direction.bind_story_cast(planned, {})


@pytest.mark.asyncio
async def test_planning_receives_style_and_retains_real_scene_direction(monkeypatch):
    reply = Storyboard.model_validate(body().model_dump(include={'title', 'scenes', 'characters'}))
    complete = AsyncMock(return_value=reply.model_dump_json())
    monkeypatch.setattr(stickman.ai_tasks, 'complete', complete)
    result = await stickman.plan(PlanRequest(source='Vợ chồng hãy nhường nhịn nhau.', style='anime_monochrome', orientation='VERTICAL'))
    prompt = complete.call_args.args[1]
    assert 'monochrome anime/webtoon' in prompt
    assert 'motion_beats' in prompt and 'Visual cast is independent' in prompt
    assert [c.name for c in result.characters] == ['Người chồng', 'Người vợ']
    assert result.characters[0].description == 'Adult man, the husband. ' + reply.characters[0].description
    assert result.characters[1].description == 'Adult woman, the wife. ' + reply.characters[1].description
    assert result.scenes[0].motion_beats == reply.scenes[0].motion_beats
    assert 'Style reference images guide drawing technique only' in prompt


@pytest.mark.asyncio
async def test_spouse_srt_binds_identities_without_changing_spoken_words(monkeypatch):
    source = '1\n00:00:00,000 --> 00:00:02,000\nVợ chồng sống với nhau, thắng thua để làm gì?\n\n2\n00:00:02,000 --> 00:00:04,000\nHơn một câu nói, mất một tấm lòng.\n```[cite: 5]'
    reply = {'title': 'Giữ hơi ấm', 'characters': [
        {'role': 'A', 'name': 'Protagonist', 'description': 'A chalk figure wearing a sweater.'},
        {'role': 'B', 'name': 'Partner', 'description': 'A chalk figure wearing a cardigan.'}],
        'scenes': [{'cue_start': 1, 'cue_end': 2, 'action': 'Character A and Character B sit at a table.', 'cast': ['A', 'B']}]}
    complete = AsyncMock(return_value=json.dumps(reply))
    monkeypatch.setattr(stickman.ai_tasks, 'complete', complete)
    result = await stickman.plan(PlanRequest(source=source))
    assert [c.name for c in result.characters] == ['Người chồng', 'Người vợ']
    assert result.characters[1].description.startswith('Adult woman, the wife.')
    assert result.scenes[0].narration == 'Vợ chồng sống với nhau, thắng thua để làm gì? Hơn một câu nói, mất một tấm lòng.'
    assert result.scenes[0].cast == ['A', 'B'] and result.scenes[0].speaker == 'A'
    assert 'cast_requirements' in complete.call_args.args[1]


@pytest.mark.parametrize('description', ['Adult man with short hair.', 'A male figure wearing a scarf.'])
def test_spouse_planning_rejects_explicitly_wrong_identity(description):
    planned = body()
    planned.characters[1].description = description
    with pytest.raises(ValueError, match='sai vai người vợ'):
        direction.bind_story_cast(planned, direction.story_cast('Vợ chồng nhường nhau.', PlanRequest(source='Câu chuyện.')))


def test_explicit_story_roles_remain_required_when_references_are_selected():
    expected = direction.story_cast('Vợ chồng nhường nhau.', PlanRequest(source='Câu chuyện.'))
    assert direction.story_cast('Vợ chồng nhường nhau.', PlanRequest(source='Câu chuyện.', character_id=uuid.uuid4())) == expected
    assert direction.story_cast('Vợ chồng nhường nhau.', PlanRequest(source='Câu chuyện.', second_character_id=uuid.uuid4())) == expected
    assert direction.story_cast('Hai người bạn cùng học bài.', PlanRequest(source='Câu chuyện.')) == {}
    planned = body()
    planned.characters = planned.characters[:1]
    with pytest.raises(ValueError, match='chưa phân đủ vai'):
        direction.bind_story_cast(planned, direction.story_cast('A husband and wife.', PlanRequest(source='A story.')))


@pytest.mark.asyncio
async def test_spouse_reference_and_estimate_do_not_reuse_genderless_portraits(monkeypatch):
    settings = body()
    legacy = [{'id': str(uuid.uuid4()), 'description': direction.reference_tag(settings, role), 'media_id': str(uuid.uuid4())} for role in ['A', 'B']]
    direction.bind_story_cast(settings, direction.story_cast('Vợ chồng.', PlanRequest(source='Câu chuyện.')))
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=legacy))
    create = AsyncMock(return_value={'id': str(uuid.uuid4())})
    monkeypatch.setattr(crud, 'create_character', create)
    monkeypatch.setattr(crud, 'link_character_to_project', AsyncMock(return_value=True))
    pid = str(uuid.uuid4())
    assert (await stickman_production.estimate(pid, settings))['reference_images'] == 2
    await stickman.reference({'id': pid}, settings, 'B')
    prompt = create.call_args.kwargs['image_prompt']
    assert 'Story character identity: Người vợ. Adult woman, the wife.' in prompt
    assert create.call_args.kwargs['description'] != legacy[1]['description']
    assert 'Adult woman' not in direction.image_prompt(settings, settings.scenes[0], pid)


@pytest.mark.asyncio
async def test_style_sample_man_does_not_override_story_wife(monkeypatch, tmp_path):
    monkeypatch.setattr(direction, 'SHARED_OUTPUT_DIR', tmp_path)
    pid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    speech_store.atomic_json(direction.profiles(pid)/f'{sid}.json', {
        'name': 'Tranh màu', 'style_instruction': 'Soft watercolor illustration.', 'negative_prompt': 'No text.',
        'character_design': 'A young masculine figure with short hair.'})
    planned = body(style_profile_id=sid)
    direction.bind_story_cast(planned, direction.story_cast('Vợ chồng.', PlanRequest(source='Câu chuyện.')))
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=[]))
    create = AsyncMock(return_value={'id': str(uuid.uuid4())})
    monkeypatch.setattr(crud, 'create_character', create)
    monkeypatch.setattr(crud, 'link_character_to_project', AsyncMock(return_value=True))
    await stickman.reference({'id': pid}, planned, 'B')
    prompt = create.call_args.kwargs['image_prompt']
    assert 'Soft watercolor illustration' in prompt and 'Adult woman, the wife.' in prompt
    assert 'masculine' not in prompt and 'mustard yellow' not in prompt


@pytest.mark.asyncio
async def test_scene_suggestion_preserves_narration_cast_and_speaker(monkeypatch):
    scene = body().scenes[0]
    before = scene.model_dump()
    suggestion = {'action': 'Character A and Character B set two cups on the table.',
                  'motion_beats': ['Both set down their cups.', 'Both move closer to the table.', 'Both hold hands beside the cups.'],
                  'camera': 'Slow push-in.'}
    complete = AsyncMock(return_value=json.dumps(suggestion))
    monkeypatch.setattr(direction.ai_tasks, 'complete', complete)
    request = SceneSuggestionRequest(source=scene.narration, scene=scene, style='anime_monochrome')
    result = await direction.suggest_scene('project', request)
    assert result['scene'].narration == scene.narration
    assert result['scene'].cast == scene.cast and result['scene'].speaker == scene.speaker
    assert result['scene'].emotion == scene.emotion and result['scene'].gesture == scene.gesture
    assert result['scene'].action == suggestion['action']
    assert scene.model_dump() == before
    assert 'monochrome anime' in complete.call_args.args[1]
    assert set(complete.call_args.kwargs['schema']['properties']) == {'action', 'motion_beats', 'camera', 'environment', 'staging', 'lighting', 'continuity'}
    # Output from AI cannot change the spoken words or add unreviewed people.
    for extra in [{'narration': 'Changed words'}, {'cast': ['B']}, {'motion_beats': ['Only one beat']}]:
        complete.return_value = json.dumps({**suggestion, **extra})
        with pytest.raises(ValidationError):
            await direction.suggest_scene('project', request)
    assert scene.model_dump() == before


@pytest.mark.asyncio
async def test_scene_suggestion_refuses_foreign_references_before_ai(monkeypatch):
    complete = AsyncMock()
    monkeypatch.setattr(direction.ai_tasks, 'complete', complete)
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=[]))
    with pytest.raises(ValueError, match='không thuộc dự án'):
        await direction.suggest_scene('project', SceneSuggestionRequest(source='Một ngày bình yên.', scene=body().scenes[0], character_id=uuid.uuid4()))
    complete.assert_not_called()


def test_preview_versions_are_bounded_scoped_and_survive_reload(monkeypatch, tmp_path):
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path)
    settings = body(preview_first=True)
    vid, sid = str(uuid.uuid4()), str(uuid.uuid4())
    row = {'id': sid, 'image_prompt': 'Two cups on a table.'}
    for _ in range(10):
        mid = str(uuid.uuid4())
        row.update(vertical_image_media_id=mid, vertical_image_url=f'https://example.test/{mid}')
        path = stickman.directory(vid)/f'{mid}.image'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'image placeholder')
        stickman_production.record_preview(vid, settings, row)
    versions = stickman_production.preview_versions(vid, settings, [row])
    assert len(versions) == 8 and versions[-1]['selected']
    assert versions[-1]['url'].endswith(mid)
    assert versions[-1]['prompt'] == row['image_prompt']
    assert stickman_production.preview_versions(str(uuid.uuid4()), settings, [row]) == []
    changed = settings.model_copy(deep=True)
    changed.scenes[0].action = 'Character A walks away from the table.'
    assert stickman_production.preview_versions(vid, changed, [row]) == []
    assert stickman_production.preview_versions(vid, settings, [{**row, 'id': str(uuid.uuid4())}]) == []


@pytest.mark.asyncio
async def test_preview_regeneration_is_explicit_and_uses_existing_batch(monkeypatch, tmp_path):
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path)
    pid, vid, sid, mid = map(str, [uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
    settings = body(preview_first=True)
    rows = [{'id': sid, 'vertical_image_status': 'COMPLETED', 'vertical_image_media_id': mid}]
    refs = {role: {'id': str(uuid.uuid4()), 'name': role, 'media_id': str(uuid.uuid4())} for role in ['A', 'B']}
    monkeypatch.setattr(stickman, 'reference', AsyncMock(side_effect=lambda project, planned, role: refs[role]))
    monkeypatch.setattr(crud, 'get_character', AsyncMock(side_effect=lambda ident: next(c for c in refs.values() if c['id'] == ident)))
    monkeypatch.setattr(crud, 'update_scene', AsyncMock())
    monkeypatch.setattr(crud, 'list_scenes', AsyncMock(return_value=rows))
    monkeypatch.setattr(stickman, 'image_file', AsyncMock())
    generate = AsyncMock()
    monkeypatch.setattr(stickman, 'generate_images', generate)
    root = stickman.directory(vid)
    speech_store.atomic_json(root/'preview.json', {'approved': False, 'regenerate': True})
    result = await stickman_production.prepare(vid, settings, {'id': pid}, rows, [], lambda **_: None)
    assert result[1] is None
    assert generate.await_count == 1
    assert generate.call_args.args[2:4] == ([sid], 'REGENERATE_IMAGE')
    assert speech_store.read_json(root/'preview.json') == {'approved': False}


@pytest.mark.asyncio
async def test_selected_preview_is_verified_before_approval(monkeypatch, tmp_path):
    from fastapi import HTTPException
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path)
    vid, pid, jid, sid, first, second = map(str, [uuid.uuid4() for _ in range(6)])
    settings = body(preview_first=True)
    rows = [{'id': sid, 'vertical_image_media_id': first, 'vertical_image_status': 'COMPLETED'}]
    root = stickman.directory(vid)
    for mid in [first, second]:
        path = root/f'{mid}.image'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'image placeholder')
        stickman_production.record_preview(vid, settings, {**rows[0], 'vertical_image_media_id': mid})
    monkeypatch.setattr(api, 'saved_video', AsyncMock(return_value=({'project_id': pid}, settings)))
    monkeypatch.setattr(api, 'project', AsyncMock(return_value={'id': pid}))
    monkeypatch.setattr(api, 'ensure_idle', lambda _: None)
    monkeypatch.setattr(api, 'flow_ready', lambda: None)
    monkeypatch.setattr(stickman, 'validate', AsyncMock())
    monkeypatch.setattr(api.timeline, 'load', lambda _: None)
    monkeypatch.setattr(media_jobs, 'latest', lambda _: {'id': jid, 'kind': 'stickman', 'status': 'COMPLETED', 'result': {'preview_url': '/image'}})
    monkeypatch.setattr(crud, 'list_scenes', AsyncMock(return_value=rows))
    update = AsyncMock()
    monkeypatch.setattr(crud, 'update_scene', update)
    start = lambda *args, **kwargs: {'status': 'QUEUED'}
    monkeypatch.setattr(stickman, 'start', start)
    with pytest.raises(HTTPException) as exc:
        await api.retry(uuid.UUID(vid), RetryRequest(job_id=jid, approve_preview=True, preview_media_id=uuid.uuid4()))
    assert exc.value.status_code == 422
    assert not speech_store.read_json(root/'preview.json', {}).get('approved')
    assert update.await_count == 0
    result = await api.retry(uuid.UUID(vid), RetryRequest(job_id=jid, approve_preview=True, preview_media_id=second))
    assert result['status'] == 'QUEUED'
    assert update.call_args.kwargs['vertical_image_media_id'] == second
    assert update.call_args.kwargs['vertical_video_media_id'] is None
    assert speech_store.read_json(root/'preview.json')['approved']


def test_style_profile_is_scoped_and_replaces_conflicting_material(tmp_path, monkeypatch):
    monkeypatch.setattr(direction, 'SHARED_OUTPUT_DIR', tmp_path)
    pid, other, sid = map(str, [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()])
    speech_store.atomic_json(direction.profiles(pid)/f'{sid}.json', {
        'name': 'Tranh chì', 'style_instruction': 'Graphite drawing on ivory paper.', 'negative_prompt': 'No neon.', 'reference_id': sid})
    settings = body(style_profile_id=sid)
    prompt = direction.image_prompt(settings, settings.scenes[0], pid)
    assert 'Graphite drawing' in prompt and 'monochrome anime' not in prompt
    with pytest.raises(ValueError, match='không thuộc dự án'):
        direction.material(settings, other)
    assert stickman_production.image_signature(settings, settings.scenes[0]) != stickman_production.image_signature(body(), body().scenes[0])


@pytest.mark.asyncio
async def test_estimate_and_reference_use_identical_design_cache(monkeypatch):
    settings = body()
    pid = str(uuid.uuid4())
    characters = [{'id': str(uuid.uuid4()), 'name': role, 'description': direction.reference_tag(settings, role), 'media_id': str(uuid.uuid4())} for role in ['A', 'B']]
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=characters))
    assert (await stickman_production.estimate(pid, settings))['reference_images'] == 0
    assert await stickman.reference({'id': pid}, settings, 'B') == characters[1]


@pytest.mark.asyncio
async def test_preview_generates_one_image_and_no_paid_clips(monkeypatch, tmp_path):
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path)
    pid, vid = str(uuid.uuid4()), str(uuid.uuid4())
    planned = body(preview_first=True)
    planned.scenes.append(planned.scenes[0].model_copy())
    refs = {role: {'id': str(uuid.uuid4()), 'name': role, 'media_id': str(uuid.uuid4())} for role in ['A', 'B']}
    rows = [{'id': str(uuid.uuid4()), 'vertical_image_status': 'PENDING'} for _ in planned.scenes]
    monkeypatch.setattr(stickman, 'reference', AsyncMock(side_effect=lambda project, settings, role: refs[role]))
    monkeypatch.setattr(crud, 'get_character', AsyncMock(side_effect=lambda ident: next(c for c in refs.values() if c['id'] == ident)))
    monkeypatch.setattr(crud, 'update_scene', AsyncMock())
    monkeypatch.setattr(crud, 'list_scenes', AsyncMock(return_value=rows))
    monkeypatch.setattr(stickman, 'image_file', AsyncMock())
    calls = []
    async def generate(vid, pid, ids, kind, *args):
        calls.append((kind, ids))
        for row in rows:
            if row['id'] in ids:
                row.update(vertical_image_media_id=str(uuid.uuid4()), vertical_image_status='COMPLETED')
    monkeypatch.setattr(stickman, 'generate_images', generate)
    updates = []
    result = await stickman_production.prepare(vid, planned, {'id': pid}, rows, [], lambda **changes: updates.append(changes))
    assert calls == [('GENERATE_IMAGE', [rows[0]['id']])]
    assert result[1] is None and updates[-1]['result']['preview_url'].endswith('/image')


@pytest.mark.asyncio
async def test_preview_requires_explicit_approval_before_retry(monkeypatch, tmp_path):
    from fastapi import HTTPException
    vid, pid, jid = uuid.uuid4(), str(uuid.uuid4()), str(uuid.uuid4())
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path)
    monkeypatch.setattr(api, 'saved_video', AsyncMock(return_value=({'project_id': pid}, body(preview_first=True))))
    monkeypatch.setattr(api, 'ensure_idle', lambda _: None)
    monkeypatch.setattr(api, 'flow_ready', lambda: None)
    monkeypatch.setattr(api, 'project', AsyncMock(return_value={'id': pid}))
    monkeypatch.setattr(stickman, 'validate', AsyncMock())
    monkeypatch.setattr(stickman, 'start', lambda *args, **kwargs: {'status': 'QUEUED'})
    monkeypatch.setattr(media_jobs, 'latest', lambda _: {'id': jid, 'kind': 'stickman', 'status': 'COMPLETED', 'result': {'preview_url': '/image'}})
    with pytest.raises(HTTPException) as exc:
        await api.retry(vid, RetryRequest(job_id=jid))
    assert exc.value.status_code == 422
    assert (await api.retry(vid, RetryRequest(job_id=jid, approve_preview=True)))['status'] == 'QUEUED'
    assert speech_store.read_json(stickman.directory(str(vid))/'preview.json')['approved']


def test_pasted_markdown_srt_line_escapes_are_cleaned():
    assert stickman.clean_source('1\\\n00:00:00,000 --> 00:00:02,000\\\nHơi ấm.[cite: 5]\\\n') == '1\n00:00:00,000 --> 00:00:02,000\nHơi ấm.'


@pytest.mark.asyncio
async def test_style_analysis_failure_does_not_upload_or_save_reference(monkeypatch, tmp_path):
    import base64
    import io
    from PIL import Image
    from fastapi import HTTPException
    from agent.api import projects

    pid = str(uuid.uuid4())
    picture = io.BytesIO()
    Image.new('RGB', (32, 32), 'white').save(picture, format='PNG')
    request = projects.CharacterPhotoUpload(name='Phong cách mẫu', image_base64=base64.b64encode(picture.getvalue()).decode())
    monkeypatch.setattr(direction, 'SHARED_OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(api, 'project', AsyncMock(return_value={'id': pid}))
    monkeypatch.setattr(api, 'flow_ready', lambda: None)
    monkeypatch.setattr(direction.ai_tasks, 'complete', AsyncMock(side_effect=RuntimeError('provider unavailable')))
    upload = AsyncMock()
    monkeypatch.setattr(projects, 'upload_character_photo', upload)
    with pytest.raises(HTTPException) as exc:
        await api.save_visual_style(uuid.UUID(pid), request)
    assert exc.value.status_code == 502
    assert 'ảnh phong cách' in exc.value.detail
    upload.assert_not_called()
    assert not direction.profiles(pid).exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('storage_error', [False, True])
async def test_uploaded_style_is_atomic_and_usable_after_reload(monkeypatch, tmp_path, storage_error):
    import base64
    import io
    from types import SimpleNamespace
    from PIL import Image
    from agent.api import projects
    from agent.db import schema

    pid = str(uuid.uuid4())
    picture = io.BytesIO()
    Image.new('RGB', (32, 32), 'white').save(picture, format='PNG')
    request = projects.CharacterPhotoUpload(name='Tranh chì riêng', image_base64=base64.b64encode(picture.getvalue()).decode())
    monkeypatch.setattr(direction, 'SHARED_OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(direction, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(projects, '_REFERENCE_DIR', tmp_path/'character_refs')
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path/'style.db')
    style = direction.VisualStyle(name='Tranh chì mẫu', style_instruction='Graphite hatching on ivory paper with detailed scenery.',
        negative_prompt='No neon.', character_design='Thin curved hand-drawn figure with a round head.')
    complete = AsyncMock(return_value=style.model_dump_json())
    monkeypatch.setattr(direction.ai_tasks, 'complete', complete)
    client = SimpleNamespace(connected=True, upload_image=AsyncMock(return_value={'_mediaId': str(uuid.uuid4())}))
    monkeypatch.setattr(projects, 'get_flow_client', lambda: client)
    await schema.init_db()
    try:
        await crud.create_project(id=pid, name='Style test', material='realistic')
        if storage_error:
            monkeypatch.setattr(direction.speech_store, 'atomic_json', lambda *args: (_ for _ in ()).throw(OSError('disk full')))
            with pytest.raises(OSError, match='disk full'):
                await direction.save_profile(pid, request)
            assert not await crud.get_project_characters(pid)
            assert not list((tmp_path/'character_refs').glob('*'))
            return
        saved = await direction.save_profile(pid, request)
        assert saved['id'] == saved['reference_id']
        assert direction.profile(pid, saved['id']) == saved
        linked = await crud.get_project_characters(pid)
        assert len(linked) == 1 and linked[0]['entity_type'] == 'visual_asset'
        context, images = await direction.planning_context(pid, PlanRequest(source='Một câu chuyện.', style_profile_id=saved['id']))
        assert context['visual_style'] == style.style_instruction
        assert len(images) == 1 and images[0].name == f"{saved['id']}.png"
        complete.assert_awaited_once()
    finally:
        await schema.close_db()
