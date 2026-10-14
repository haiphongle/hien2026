"""Timing, selective rebuilds and editor isolation for the new production modes."""
import asyncio
import array
import math
import shutil
import uuid
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from PIL import Image

from agent.api import stickman as api
from agent.db import crud, schema
from agent.models.stickman import ActorChange, CreateRequest
from agent.models.timeline import TimelineDocument
from agent.services import editing, media_jobs, speech_store, stickman, stickman_actor, stickman_production, timeline, video_assembly
from agent.materials import STORY_PRESETS, get_material


def body(**changes):
    return CreateRequest(title='Bình yên từng chút', preset_id='v3_A', motion='talk', dialogue=True, second_preset_id='v3_B', scenes=[
        {'narration': 'Mệt quá, hôm nay việc nhiều.', 'action': 'A puts a heavy sack down.', 'speaker': 'A', 'emotion': 'sad', 'gesture': 'think'},
        {'narration': 'Nghỉ một chút rồi đi tiếp nhé.', 'action': 'B waves and encourages A.', 'speaker': 'B', 'emotion': 'happy', 'gesture': 'wave'},
    ], **changes)


def wav(path, duration, *, silent=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    rate = 24000
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(rate)
        samples = array.array('h', (0 if i/rate < silent else int(3000*math.sin(i*2*math.pi*440/rate)) for i in range(round(duration*rate))))
        output.writeframes(samples.tobytes())


def test_dialogue_requires_second_voice():
    data = body().model_dump()
    data['second_preset_id'] = None
    with pytest.raises(ValidationError):
        CreateRequest.model_validate(data)
    data['dialogue'] = False
    with pytest.raises(ValidationError):
        CreateRequest.model_validate(data)


def test_mouth_follows_audio_and_silence_without_inserting_frames(tmp_path):
    audio = tmp_path/'voice.wav'
    wav(audio, 1.337, silent=.4)
    levels = stickman_actor.mouth_levels(audio)
    assert len(levels) == math.ceil(1.337*30)
    assert levels[:12] == [0]*12
    assert min(levels[13:-1]) > .5
    spec = {'style': 'stickman_chalk', 'orientation': 'VERTICAL', 'dialogue': True, 'speaker': 'B', 'emotion': 'happy', 'gesture': 'wave'}
    closed = stickman_actor.frame(spec, 0, .5, 360, 640)
    opened = stickman_actor.frame(spec, 1, .5, 360, 640)
    assert closed.crop((0, 0, 180, 640)).tobytes() == opened.crop((0, 0, 180, 640)).tobytes()
    assert closed.crop((180, 0, 360, 640)).tobytes() != opened.crop((180, 0, 360, 640)).tobytes()


@pytest.mark.parametrize('style', ['cartoon_light', 'cartoon_dark', 'cartoon_chalk'])
@pytest.mark.parametrize('size', [(360, 640), (640, 360)])
def test_solid_styles_have_filled_bodies_and_only_speakers_mouth_moves(style, size):
    width, height = size
    spec = {'style': style, 'dialogue': True, 'speaker': 'B', 'emotion': 'happy', 'gesture': 'wave'}
    closed = stickman_actor.frame(spec, 0, .5, width, height)
    opened = stickman_actor.frame(spec, 1, .5, width, height)
    assert closed.crop((0, 0, width//2, height)).tobytes() == opened.crop((0, 0, width//2, height)).tobytes()
    assert closed.crop((width//2, 0, width, height)).tobytes() != opened.crop((width//2, 0, width, height)).tobytes()
    palette = next(p for p in STORY_PRESETS if p['id'] == style)
    scale = min(width/360, height/520)
    # Off-centre torso pixels must be filled, rather than a thin stick line.
    assert closed.getpixel((round(width*.29+15*scale), round(height*.79-140*scale))) == tuple(bytes.fromhex(palette['fill'][1:]))
    assert get_material(style)['id'] == style
    assert 'thin-line stick figures' in get_material(style)['negative_prompt']


@pytest.mark.asyncio
@pytest.mark.parametrize('style', ['cartoon_light', 'cartoon_dark', 'cartoon_chalk'])
async def test_solid_references_do_not_reuse_thin_line_or_coloured_characters(monkeypatch, style):
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=[]))
    create = AsyncMock(return_value={'id': str(uuid.uuid4())})
    monkeypatch.setattr(crud, 'create_character', create)
    monkeypatch.setattr(crud, 'link_character_to_project', AsyncMock(return_value=True))
    await stickman.reference({'id': str(uuid.uuid4())}, body(style=style), 'B')
    prompt = create.call_args.kwargs['image_prompt']
    assert 'solid cartoon person' in prompt and 'filled arms and legs' in prompt
    assert 'thin line arms and legs' not in prompt and 'amber linework' not in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize('style', ['cartoon_studio', 'cartoon_story', '3d_pixar', 'anime', 'ghibli'])
async def test_generated_styles_keep_their_design_and_reject_vector_mode(monkeypatch, style):
    settings = {**body().model_dump(), 'style': style}
    with pytest.raises(ValidationError, match='Phong cách này cần ảnh'):
        CreateRequest.model_validate(settings)
    planned = CreateRequest.model_validate({**settings, 'motion': 'ai'})
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=[]))
    create = AsyncMock(return_value={'id': str(uuid.uuid4())})
    monkeypatch.setattr(crud, 'create_character', create)
    monkeypatch.setattr(crud, 'link_character_to_project', AsyncMock(return_value=True))
    await stickman.reference({'id': str(uuid.uuid4())}, planned, 'B')
    prompt = create.call_args.kwargs['image_prompt']
    assert get_material(style)['style_instruction'].split(',')[0] in prompt
    assert 'plain empty background' in prompt
    assert 'Fully clothed' in prompt or 'fully clothed' in prompt
    assert 'thin line arms and legs' not in prompt and 'amber linework' not in prompt
    assert style not in stickman_actor.PALETTES


@pytest.mark.asyncio
async def test_new_solid_reference_and_estimate_do_not_reuse_rigid_legacy_reference(monkeypatch):
    legacy = {'id': str(uuid.uuid4()), 'name': 'Nhân vật 2D · Nền trắng',
              'description': 'flowkit:stickman:cartoon_light', 'media_id': str(uuid.uuid4())}
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=[legacy]))
    create = AsyncMock(return_value={'id': str(uuid.uuid4())})
    monkeypatch.setattr(crud, 'create_character', create)
    monkeypatch.setattr(crud, 'link_character_to_project', AsyncMock(return_value=True))
    settings = body().model_dump()
    planned = CreateRequest.model_validate({**settings, 'style': 'cartoon_light', 'motion': 'zoom', 'dialogue': False, 'scenes': settings['scenes'][:1]})
    pid = str(uuid.uuid4())
    counts = await stickman_production.estimate(pid, planned)
    assert counts['reference_images'] == 1
    await stickman.reference({'id': pid}, planned)
    assert create.call_args.kwargs['name'] != legacy['name']
    assert create.call_args.kwargs['description'] != legacy['description']
    # The new reference is reused on the next run rather than created again.
    create.reset_mock()
    current = {**legacy, 'description': stickman.reference_tag('cartoon_light')}
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=[legacy, current]))
    assert await stickman.reference({'id': pid}, planned) == current
    create.assert_not_called()
    assert (await stickman_production.estimate(pid, planned))['reference_images'] == 0


@pytest.mark.asyncio
async def test_style_options_have_valid_previews_and_preserve_legacy_palettes(monkeypatch):
    monkeypatch.setattr(api, 'project', AsyncMock(return_value={}))
    presets = (await api.options(uuid.uuid4()))['presets']
    assert len({p['id'] for p in presets}) == len(presets)
    public = Path(__file__).parents[2]/'dashboard'/'public'
    for preset in presets:
        assert get_material(preset['id'])
        if preset.get('collection') == 'html':
            # HTML presets use the vector swatch in StoryStylePicker.
            assert preset['anatomy'] in {'stick', 'human', 'semi', 'chibi'}
            assert preset['background_type'] in {'white', 'black', 'scene'}
            assert len(preset['ink']) == len(preset['background']) == 7
        elif preset['shape'] != 'stick':
            path = public/preset['preview'].lstrip('/')
            if path.suffix == '.svg':
                from xml.etree import ElementTree
                root = ElementTree.parse(path).getroot()
                _, _, width, height = map(float, root.attrib['viewBox'].split())
                assert root.tag.endswith('svg') and 0 < width <= 640 and 0 < height <= 426
            else:
                with Image.open(path) as preview:
                    assert preview.width <= 640 and preview.height <= 426
        assert 'character_design' not in preset and 'reference_version' not in preset
    assert set(stickman_actor.PALETTES) == {'stickman_light', 'stickman_dark', 'stickman_chalk', 'cartoon_light', 'cartoon_dark', 'cartoon_chalk'}


def test_voice_padding_keeps_every_sample_and_adds_only_frame_tail(tmp_path):
    source, target = tmp_path/'original.wav', tmp_path/'padded.wav'
    wav(source, .617)
    stickman.copy_voice(source, target)
    with wave.open(str(source), 'rb') as src, wave.open(str(target), 'rb') as dst:
        original, copied = src.readframes(src.getnframes()), dst.readframes(dst.getnframes())
        assert copied[:len(original)] == original
        assert copied[len(original):] == bytes(len(copied)-len(original))
        assert 0 <= dst.getnframes()/dst.getframerate() - .617 < 1/30


@pytest.mark.asyncio
async def test_estimate_counts_google_calls_and_local_mode(monkeypatch):
    monkeypatch.setattr(crud, 'get_project_characters', AsyncMock(return_value=[]))
    local = await stickman_production.estimate(str(uuid.uuid4()), body())
    assert local['reference_images'] == local['scene_images'] == local['flow_clips'] == 0
    ai = await stickman_production.estimate(str(uuid.uuid4()), body().model_copy(update={'motion': 'ai'}))
    assert (ai['reference_images'], ai['scene_images'], ai['flow_clips']) == (2, 2, 2)


@pytest.fixture
async def production(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path/'flow.db')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path/'jobs')
    monkeypatch.setattr(timeline, 'ROOT', tmp_path/'timeline')
    monkeypatch.setattr(editing, 'ROOT', tmp_path/'editing')
    monkeypatch.setattr(stickman, 'OUTPUT_DIR', tmp_path/'output')
    monkeypatch.setattr(video_assembly, 'OUTPUT_DIR', tmp_path/'output')
    monkeypatch.setattr(stickman.speech_voices, 'engine_service', lambda _: SimpleNamespace(worker=SimpleNamespace(abort=lambda: None)))
    monkeypatch.setattr(stickman, 'validate', AsyncMock())
    monkeypatch.setattr(api, 'flow_ready', lambda: pytest.fail('Local talking mode must work without Flow'))
    monkeypatch.setattr(stickman, 'generate_images', AsyncMock(side_effect=AssertionError('Talking mode must not use Google requests')))
    calls = []
    async def voices(rows, directory, **kwargs):
        calls.append((kwargs['preset_id'], [r['id'] for r in rows]))
        results = []
        for row in rows:
            path = Path(directory)/f"scene_{row['display_order']:03d}_{row['id']}.wav"
            length = .617 if kwargs['preset_id'] == 'v3_A' else .943
            wav(path, length)
            results.append({'audio_path': str(path), 'duration': length, 'status': 'COMPLETED'})
        return results
    monkeypatch.setattr(stickman.tts, 'generate_video_narration', voices)
    await schema.init_db()
    project = await crud.create_project('Original', material='realistic')
    created = await api.create(uuid.UUID(project['id']), body())
    vid = created['video_id']
    await asyncio.wait_for(media_jobs._tasks[vid], 60)
    assert media_jobs.latest(vid)['status'] == 'COMPLETED', media_jobs.latest(vid)
    yield vid, calls, project
    await media_jobs.shutdown()
    await schema.close_db()


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
async def test_two_speakers_selective_rebuild_keeps_old_files(production):
    vid, calls, _ = production
    assert [p for p, _ in calls] == ['v3_A', 'v3_B']
    saved = timeline.load(vid)
    tracks = {t['id']: t for t in saved['document']['tracks']}
    pictures = [c for c in saved['document']['clips'] if tracks[c['track_id']]['kind'] == 'video']
    voices = [c for c in saved['document']['clips'] if tracks[c['track_id']]['role'] == 'voice']
    assert voices[1]['start'] == pictures[1]['start'] == pytest.approx(19/30)
    old_paths = [Path(timeline.asset(vid, c['asset_id'])['path']) for c in pictures+voices]
    old_bytes = [p.read_bytes() for p in old_paths]
    changed = body()
    changed.scenes[1].gesture = 'point'
    await api.revise(uuid.UUID(vid), changed)
    await asyncio.wait_for(media_jobs._tasks[vid], 60)
    assert media_jobs.latest(vid)['status'] == 'COMPLETED', media_jobs.latest(vid)
    current = timeline.load(vid)['document']
    new_pictures = [c for c in current['clips'] if next(t for t in current['tracks'] if t['id'] == c['track_id'])['kind'] == 'video']
    assert new_pictures[0]['asset_id'] == pictures[0]['asset_id']
    assert new_pictures[1]['asset_id'] != pictures[1]['asset_id']
    assert [p.read_bytes() for p in old_paths] == old_bytes
    assert len(editing.load(vid)['exports']) == 2


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
async def test_solid_style_rebuild_preserves_voice_timing_and_previous_export(production):
    vid, _, _ = production
    original = timeline.load(vid)['document']
    voice_track = next(t['id'] for t in original['tracks'] if t['role'] == 'voice')
    old_voices = [c for c in original['clips'] if c['track_id'] == voice_track]
    old_export = editing.directory(vid)/'exports'/f"{editing.load(vid)['exports'][0]['export_id']}.mp4"
    old_bytes = old_export.read_bytes()
    await api.revise(uuid.UUID(vid), body(style='cartoon_dark'))
    await asyncio.wait_for(media_jobs._tasks[vid], 60)
    assert media_jobs.latest(vid)['status'] == 'COMPLETED', media_jobs.latest(vid)
    updated = timeline.load(vid)['document']
    new_voice_track = next(t['id'] for t in updated['tracks'] if t['role'] == 'voice')
    fields = ('asset_id', 'start', 'source_in', 'duration', 'speed', 'gain')
    assert [tuple(c[k] for k in fields) for c in updated['clips'] if c['track_id'] == new_voice_track] == [tuple(c[k] for k in fields) for c in old_voices]
    assert [(t['name'], t['kind'], t['role']) for t in updated['tracks']] == [(t['name'], t['kind'], t['role']) for t in original['tracks']]
    assert old_export.read_bytes() == old_bytes
    assert len(editing.load(vid)['exports']) == 2


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
async def test_timeline_gesture_changes_only_register_a_new_asset(production):
    vid, _, _ = production
    saved = timeline.load(vid)
    picture = next(c for c in saved['document']['clips'] if c['asset_id'] and timeline.asset(vid, c['asset_id'])['ref'].startswith('stickman:'))
    job = await api.change_actor(uuid.UUID(vid), ActorChange(asset_id=picture['asset_id'], emotion='surprised', gesture='point'))
    await asyncio.wait_for(media_jobs._tasks[vid], 60)
    assert job['status'] == 'COMPLETED'
    assert job['result']['asset_id'] != picture['asset_id']
    assert job['result']['asset']['id'] == job['result']['asset_id']
    assert 'path' not in job['result']['asset']
    records = stickman.editor_records(vid)
    refs = {record[1] for record in records}
    assert timeline.asset(vid, picture['asset_id'])['ref'] in refs
    assert timeline.asset(vid, job['result']['asset_id'])['ref'] in refs
    assert timeline.load(vid) == saved  # Client applies one asset through normal editor undo.
    assert await api.actor(uuid.UUID(vid), uuid.UUID(job['result']['asset_id'])) == {'emotion': 'surprised', 'gesture': 'point'}
    changed = TimelineDocument.model_validate(saved['document'])
    changed.clips[0].gain = .4
    timeline.save(vid, saved['revision'], changed)
    with pytest.raises(HTTPException) as exc:
        await api.revise(uuid.UUID(vid), body())
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_batch_remembers_success_even_when_another_request_fails(monkeypatch):
    from agent.api import requests
    rows = [{'id': 'one', 'scene_id': 'A', 'status': 'COMPLETED'}, {'id': 'two', 'scene_id': 'B', 'status': 'FAILED', 'error_message': 'test'}]
    monkeypatch.setattr(requests, 'create_batch', AsyncMock(return_value=rows))
    monkeypatch.setattr(requests, 'batch_status', AsyncMock(return_value=SimpleNamespace(done=True)))
    monkeypatch.setattr(crud, 'list_requests', AsyncMock(return_value=rows))
    completed = AsyncMock()
    with pytest.raises(ValueError, match='test'):
        await stickman.generate_images(str(uuid.uuid4()), str(uuid.uuid4()), ['A', 'B'], 'GENERATE_VIDEO', 'VERTICAL', lambda **_: None, 45, 15, completed)
    completed.assert_awaited_once_with(rows[0])


@pytest.mark.asyncio
async def test_model_denied_explains_configuration_change_and_keeps_success(monkeypatch):
    from agent.api import requests
    rows = [{'id': 'one', 'scene_id': 'A', 'status': 'COMPLETED'},
            {'id': 'two', 'scene_id': 'B', 'status': 'FAILED', 'error_message': 'PUBLIC_ERROR_MODEL_ACCESS_DENIED'}]
    monkeypatch.setattr(requests, 'create_batch', AsyncMock(return_value=rows))
    monkeypatch.setattr(requests, 'batch_status', AsyncMock(return_value=SimpleNamespace(done=True)))
    monkeypatch.setattr(crud, 'list_requests', AsyncMock(return_value=rows))
    completed = AsyncMock()
    updates = []
    with pytest.raises(ValueError, match='Chọn model thay thế'):
        await stickman.generate_images(str(uuid.uuid4()), str(uuid.uuid4()), ['A', 'B'], 'GENERATE_VIDEO', 'HORIZONTAL', lambda **data: updates.append(data), 45, 15, completed)
    completed.assert_awaited_once_with(rows[0])
    assert {'error_code': 'VIDEO_MODEL_ACCESS_DENIED'} in updates


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
@pytest.mark.parametrize('directed', [False, True])
async def test_flow_clips_use_batch_and_fit_full_voice(production, monkeypatch, tmp_path, directed):
    from agent.services import pacing, video_reviewer
    vid, _, project = production
    scenes = await crud.list_scenes(vid)
    saved = speech_store.read_json(stickman.directory(vid)/'renders.json')
    voices = [{'audio_path': saved[s['id']]['audio'], 'duration': stickman.tts.wav_duration(Path(saved[s['id']]['audio']))} for s in scenes]
    character = {'id': str(uuid.uuid4()), 'name': 'Reference A', 'media_id': str(uuid.uuid4())}
    monkeypatch.setattr(stickman, 'reference', AsyncMock(return_value=character))
    monkeypatch.setattr(crud, 'get_character', AsyncMock(return_value=character))
    calls = []
    models = []
    async def generate(vid, pid, ids, kind, orientation, update, begin, span, on_completed=None, *, video_model=None):
        calls.append((kind, ids))
        if video_model:
            models.append(video_model)
        prefix = 'image' if 'IMAGE' in kind else 'video'
        for sid in ids:
            await crud.update_scene(sid, **{f'vertical_{prefix}_status': 'COMPLETED', f'vertical_{prefix}_media_id': str(uuid.uuid4())})
            if on_completed:
                await on_completed({'scene_id': sid})
    monkeypatch.setattr(stickman, 'generate_images', generate)
    raw = tmp_path/'raw.mp4'
    await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=blue:s=128x128:r=30', '-t', '0.4', '-c:v', 'libx264', str(raw)])
    async def download(media_id, target):
        uuid.UUID(media_id)
        shutil.copyfile(raw, target)
    monkeypatch.setattr(video_reviewer, '_download_via_get_media', download)
    planned = body().model_copy(update={'motion': 'ai', 'dialogue': False, 'second_preset_id': None, 'video_model': 'veo_3_1_i2v_lite'})
    planned.scenes[1].speaker = 'A'
    if directed:
        for scene in planned.scenes:
            scene.motion_beats = ['Put down the heavy sack.', 'Take a breath under the tree.', 'Walk toward the light.']
    _, paths, lengths = await stickman_production.prepare(vid, planned, project, scenes, voices, lambda **_: None)
    assert [kind for kind, _ in calls] == ['GENERATE_IMAGE', 'GENERATE_VIDEO']
    assert models == ['veo_3_1_i2v_lite']
    for path, length in zip(paths, lengths):
        info = await pacing.probe(Path(path))
        assert info['duration'] == pytest.approx(length, abs=.04)
        assert length > .4  # Directed footage scales motion; legacy clips keep their end-frame behavior.
    calls.clear()
    planned = planned.model_copy(update={'video_model': 'abra_i2v_8s'})
    scenes = await crud.list_scenes(vid)
    _, same, _ = await stickman_production.prepare(vid, planned, project, scenes, voices, lambda **_: None)
    assert calls == [] and same == paths
    # Switching models continues missing clips, without charging for completed ones.
    await crud.update_scene(scenes[0]['id'], vertical_video_media_id=None, vertical_video_status='FAILED')
    scenes = await crud.list_scenes(vid)
    _, partial, _ = await stickman_production.prepare(vid, planned, project, scenes, voices, lambda **_: None)
    assert calls == [('GENERATE_VIDEO', [scenes[0]['id']])]
    assert models == ['veo_3_1_i2v_lite', 'abra_i2v_8s']
    assert partial[1] == paths[1]


@pytest.mark.asyncio
async def test_preview_checkpoint_uses_valid_db_status_and_preserves_previous_export(production, monkeypatch):
    vid, _, project = production
    previous = (await crud.get_video(vid))['vertical_url']
    # Reuse the real video/voice DB fixture; no network or credits are used.
    monkeypatch.setattr(stickman_production, 'prepare', AsyncMock(return_value=(await crud.list_scenes(vid), None, None)))
    speech_store.atomic_json(stickman.directory(vid)/'pending.json', {'rebuild': True, 'revision': timeline.load(vid)['revision']})
    stickman.start(vid, body().model_copy(update={'preview_first': True}), project)
    await asyncio.wait_for(media_jobs._tasks[vid], 60)
    assert media_jobs.latest(vid)['status'] == 'COMPLETED'
    video = await crud.get_video(vid)
    assert video['status'] == 'COMPLETED' and video['vertical_url'] == previous
    # First production has no prior export: its real schema accepts DRAFT.
    (stickman.directory(vid)/'result.json').unlink()
    stickman.start(vid, body().model_copy(update={'preview_first': True}), project)
    await asyncio.wait_for(media_jobs._tasks[vid], 60)
    assert media_jobs.latest(vid)['status'] == 'COMPLETED'
    assert (await crud.get_video(vid))['status'] == 'DRAFT'


@pytest.mark.asyncio
async def test_cached_speech_checks_follow_changed_scene_positions(production, monkeypatch, tmp_path):
    from agent.services import speech_analysis, transcription
    vid, _, _ = production
    scenes = await crud.list_scenes(vid)
    saved = speech_store.read_json(stickman.directory(vid)/'renders.json')
    voices = [{'audio_path': saved[s['id']]['audio'], 'duration': stickman.tts.wav_duration(Path(saved[s['id']]['audio']))} for s in scenes]
    monkeypatch.setattr(transcription, 'models', lambda: [{'id': 'small', 'ready': True}])
    counts = []
    async def recognize(items, *args, **kwargs):
        counts.append(len(items))
        return [{**row, 'recognized': row['text'], 'needs_review': False, 'difference': .18} for row in items]
    monkeypatch.setattr(speech_analysis, 'recognize', recognize)
    await stickman_production.speech_review(vid, body(), scenes, voices, lambda **_: None)
    wav(tmp_path/'longer.wav', .81)
    voices[0] = {'audio_path': str(tmp_path/'longer.wav'), 'duration': .81}
    await stickman_production.speech_review(vid, body(), scenes, voices, lambda **_: None)
    assert counts == [2, 1]
    report = speech_store.read_json(stickman.directory(vid)/'quality.json')
    assert report['words'][1]['start'] == pytest.approx(25/30)
    assert report['words'][1]['needs_review'] is True


@pytest.mark.asyncio
async def test_resume_refuses_to_overwrite_manual_edits(production):
    vid, _, project = production
    saved = timeline.load(vid)
    speech_store.atomic_json(stickman.directory(vid)/'pending.json', {'rebuild': True, 'revision': saved['revision']})
    doc = TimelineDocument.model_validate(saved['document'])
    doc.clips[0].gain = .6
    current = timeline.save(vid, saved['revision'], doc)
    stickman.start(vid, body(), project)
    await asyncio.wait_for(media_jobs._tasks[vid], 60)
    assert media_jobs.latest(vid)['status'] == 'FAILED'
    assert 'Timeline đã thay đổi' in media_jobs.latest(vid)['error']
    assert timeline.load(vid) == current
    assert (await crud.get_video(vid))['status'] == 'COMPLETED'


@pytest.mark.asyncio
async def test_rebuild_publishes_matching_video_orientation(production):
    from agent.services import pacing
    vid, _, _ = production
    planned = body(orientation='HORIZONTAL')
    await api.revise(uuid.UUID(vid), planned)
    await asyncio.wait_for(media_jobs._tasks[vid], 60)
    job = media_jobs.latest(vid)
    assert job['status'] == 'COMPLETED', job
    assert (await crud.get_video(vid))['orientation'] == 'HORIZONTAL'
    exported = editing.directory(vid)/'exports'/f"{job['result']['export_id']}.mp4"
    info = await pacing.probe(exported)
    assert (info['width'], info['height']) == (1280, 720)
