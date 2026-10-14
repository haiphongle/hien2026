"""Reimport AI layers over immutable Studio scenes without replacing the edit."""
from copy import deepcopy
from pathlib import Path
import uuid
import wave
from unittest.mock import AsyncMock

import pytest

from agent.db import crud
from agent.models.speech import Cue, SpeechDocument
from agent.models.timeline import GainPoint, TimelineDocument
from agent.services import imported_video, speech, speech_store, stickman, timeline, timeline_bridge
from tests.unit.test_audio_mix import audio_project
from tests.unit.test_timeline import timeline_context, uid


@pytest.fixture
async def story_context(timeline_context, tmp_path, monkeypatch):
    vid, source, client = timeline_context
    first = (await crud.list_scenes(vid))[0]
    second = await crud.create_scene(vid, 1, 'Second scene')
    old = 'Một câu nói cũ đủ dài để được chia thành hai phần phụ đề trong bản dựng ban đầu.'
    scenes = [dict(first, duration=4, narrator_text='Lời mới.'), dict(second, duration=4, narrator_text='Câu thứ hai.')]
    monkeypatch.setattr(imported_video, 'timeline', AsyncMock(return_value=scenes))
    monkeypatch.setattr(timeline_bridge, 'OUTPUT_DIR', tmp_path)
    voice = tmp_path/'audio_test'/'tts'/'new.wav'
    voice.parent.mkdir(parents=True)
    with wave.open(str(voice), 'wb') as output:
        output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        output.writeframes(bytes(24000*2*2))
    monkeypatch.setattr(timeline_bridge, 'narration_path_for_scene', lambda *_: voice)
    monkeypatch.setattr(timeline_bridge, 'narration_matches_text', lambda *_: True)
    speech_store.atomic_json(stickman.directory(vid)/'plan.json', {'scenes': [{'narration': old}, {'narration': 'Câu thứ hai.'}]})
    assets = [await timeline.register(vid, vid, f"stickman:{s['id']}:immutable", 'Scene', source, 'source') for s in scenes]
    tracks = [dict(id=uid(), name='Hình', kind='video'), dict(id=uid(), name='Giọng', kind='audio', role='voice'),
              dict(id=uid(), name='Phụ đề', kind='caption'), dict(id=uid(), name='Nhạc', kind='audio', role='music'),
              dict(id=uid(), name='Tiêu đề', kind='text')]
    pictures = [dict(id=uid(), track_id=tracks[0]['id'], asset_id=a['id'], start=index*4, duration=4) for index, a in enumerate(assets)]
    captions = [dict(id=uid(), track_id=tracks[2]['id'], start=start, duration=span, text=text,
                    anchor=pictures[0]['id'], caption={'color': '#ff0000', 'x': .3})
                for start, span, text in stickman.caption_parts(old, 4)]
    manual = dict(id=uid(), track_id=tracks[2]['id'], start=0, duration=1, text='Ghi chú thủ công', anchor=pictures[0]['id'])
    title = dict(id=uid(), track_id=tracks[4]['id'], start=0, duration=8, text='Tiêu đề của tôi')
    music = dict(id=uid(), track_id=tracks[3]['id'], asset_id=assets[0]['id'], start=0, duration=4, gain=.2)
    old_voice = dict(id=uid(), track_id=tracks[1]['id'], asset_id=assets[0]['id'], start=0, duration=4,
                     anchor=pictures[0]['id'], origin=f"voice:{pictures[0]['id']}:{first['id']}", gain=.7)
    doc = TimelineDocument(tracks=tracks, clips=pictures+captions+[manual, title, music, old_voice])
    return vid, doc, scenes, voice, client


async def test_scene_reimport_adopts_old_captions_and_preserves_manual_edits(story_context):
    vid, doc, scenes, _, _ = story_context
    doc.clips[-1].fade_in, doc.clips[-1].fade_out = .4, .6
    doc.clips[-1].envelope = [GainPoint(time=1, value=.5), GainPoint(time=4, value=.8)]
    before = doc.model_dump(mode='json')
    result, warnings, count = await timeline_bridge.import_results(vid, doc, source='scenes', effects=False)
    assert not warnings and count > 0
    assert doc.model_dump(mode='json') == before  # Import is an undoable draft, never an in-place save.
    clips = {c['id']: c for c in result['clips']}
    first_caption = next(c for c in before['clips'] if c['text'].startswith('Một câu nói'))
    assert clips[first_caption['id']]['text'] == scenes[0]['narrator_text']
    assert clips[first_caption['id']]['caption'] == first_caption['caption']
    assert clips[first_caption['id']]['duration'] == 2  # Follow the new voice, not the old 4s picture.
    assert len([c for c in result['clips'] if c['origin'].startswith('caption:')]) == 2
    assert not any(c['text'] and 'bản dựng ban đầu' in c['text'] for c in result['clips'])
    for original in before['clips']:
        if original['text'] in {'Ghi chú thủ công', 'Tiêu đề của tôi'} or original['track_id'] in {before['tracks'][0]['id'], before['tracks'][3]['id']}:
            assert clips[original['id']] == original
    voice = next(c for c in result['clips'] if c['origin'] == before['clips'][-1]['origin'])
    assert voice['id'] == before['clips'][-1]['id'] and voice['gain'] == .7 and voice['duration'] == 2
    assert (voice['fade_in'], voice['fade_out']) == (.2, .3)
    assert voice['envelope'] == [{'time': .5, 'value': .5}, {'time': 2, 'value': .8}]
    again, warnings, count = await timeline_bridge.import_results(vid, TimelineDocument.model_validate(result), source='scenes', effects=False)
    assert not warnings and count == 0 and again == result
    # Regeneration replaces the provider WAV, while undo/export keep the old asset.
    source_voice = Path(timeline.asset(vid, voice['asset_id'])['path'])
    original_audio = source_voice.read_bytes()
    new_audio = story_context[3]
    with wave.open(str(new_audio), 'wb') as output:
        output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        output.writeframes(bytes(24000*2))
    timeline.validate_assets(vid, TimelineDocument.model_validate(result))
    assert source_voice.read_bytes() == original_audio
    updated, warnings, _ = await timeline_bridge.import_results(vid, TimelineDocument.model_validate(result), source='scenes', effects=False)
    assert not warnings
    assert next(c for c in updated['clips'] if c['id'] == voice['id'])['asset_id'] != voice['asset_id']


async def test_speech_reimport_maps_global_cues_through_reordered_trimmed_scenes(story_context, monkeypatch):
    vid, original, scenes, voice, _ = story_context
    with wave.open(str(voice), 'wb') as output:
        output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        output.writeframes(bytes(24000*2*3))
    data = original.model_dump(mode='json')
    first, second = data['clips'][:2]
    first.update(start=0, source_in=3, duration=1)
    second.update(start=10, source_in=1, duration=1, speed=2)
    data['clips'] = [first, second]
    cue = Cue(start=3, end=6, text='Một bản dịch chạy qua hai cảnh.')
    speech_store.atomic_json(speech_store.video_dir(vid)/'script.json',
        SpeechDocument(timeline=speech_store.timeline_key(scenes), cues=[cue]).model_dump(mode='json'))
    monkeypatch.setattr(speech, 'cue_status', lambda *_: {'status': 'READY'})
    monkeypatch.setattr(speech, 'cue_audio', lambda *_: (voice, None, None, None))
    result, warnings, _ = await timeline_bridge.import_results(vid, TimelineDocument.model_validate(data), source='speech', effects=False)
    assert not warnings
    captions = [c for c in result['clips'] if c['text']]
    assert [(c['start'], c['duration'], c['anchor']) for c in captions] == [(0, 1, first['id']), (10, .5, second['id'])]
    voices = [c for c in result['clips'] if c['origin'].startswith('voice:')]
    assert [(c['start'], c['source_in'], c['duration'], c['speed']) for c in voices] == [(0, 0, 1, 1), (10, 2, .5, 2)]
    again, warnings, count = await timeline_bridge.import_results(vid, TimelineDocument.model_validate(result), source='speech', effects=False)
    assert not warnings and count == 0 and again == result


async def test_long_regenerated_voice_does_not_remove_existing_recording(story_context):
    vid, doc, _, voice, _ = story_context
    before = doc.model_dump(mode='json')
    previous = before['clips'][-1]
    with wave.open(str(voice), 'wb') as output:
        output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        output.writeframes(bytes(24000*2*5))
    result, warnings, _ = await timeline_bridge.import_results(vid, doc, source='scenes', effects=False)
    assert any('giữ lời đọc cũ' in warning for warning in warnings)
    assert next(c for c in result['clips'] if c['id'] == previous['id']) == previous
    assert doc.model_dump(mode='json') == before
    timeline.validate_assets(vid, TimelineDocument.model_validate(result))


@pytest.mark.parametrize('duration', [4.06, 4.18])
async def test_small_scene_overage_uses_full_new_voice_without_changing_picture(story_context, duration):
    vid, doc, _, voice, _ = story_context
    with wave.open(str(voice), 'wb') as output:
        output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        output.writeframes(bytes(round(24000*duration)*2))
    before = voice.read_bytes()
    first_asset = timeline.asset_file(vid, str(doc.clips[0].asset_id))
    metadata = speech_store.read_json(first_asset)
    metadata['duration'] = 3.9993  # Container timestamps need not land on a video frame.
    speech_store.atomic_json(first_asset, metadata)
    report = {}
    result, warnings, _ = await timeline_bridge.import_results(vid, doc, source='scenes', effects=False, report=report)
    assert not warnings and report == {'voices': 2, 'fitted': 2, 'blocked': []}
    voices = [c for c in result['clips'] if c['origin'].startswith('voice:')]
    assert all(c['duration'] == 4 and c['speed'] == pytest.approx(duration/4) for c in voices)
    assert voices[0]['gain'] == .7
    assert voice.read_bytes() == before
    assert result['clips'][:2] == doc.model_dump(mode='json')['clips'][:2]
    timeline.validate_assets(vid, TimelineDocument.model_validate(result))
    again, _, count = await timeline_bridge.import_results(vid, TimelineDocument.model_validate(result), source='scenes', effects=False)
    assert count == 0 and result == again


async def test_fitted_scene_voice_maps_trim_and_speed_without_clipping(story_context):
    vid, doc, _, voice, _ = story_context
    with wave.open(str(voice), 'wb') as output:
        output.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
        output.writeframes(bytes(24000*2)*4+bytes(24000*2//5))
    data = doc.model_dump(mode='json')
    picture = data['clips'][0]
    picture.update(source_in=1, duration=2, speed=1.5)
    data['clips'] = [picture]
    result, warnings, _ = await timeline_bridge.import_results(vid, TimelineDocument.model_validate(data), source='scenes', effects=False)
    assert not warnings
    voice_clip = next(c for c in result['clips'] if c['origin'].startswith('voice:'))
    assert voice_clip['source_in'] == pytest.approx(1.033333333)
    assert voice_clip['speed'] == pytest.approx(1.575)
    assert voice_clip['duration'] == 2
    timeline.validate_assets(vid, TimelineDocument.model_validate(result))


async def test_whisper_output_cues_use_edited_coordinates_once(story_context, monkeypatch):
    vid, doc, scenes, voice, _ = story_context
    data = doc.model_dump(mode='json')
    first, second = data['clips'][:2]
    first.update(source_in=1, duration=2, speed=1.5)
    second.update(start=2, source_in=1, duration=1, speed=2)
    data['clips'] = [first, second]
    edited = TimelineDocument.model_validate(data)
    cue = Cue(start=1, end=3, text='Lời theo mốc bản dựng.')
    speech_store.atomic_json(speech_store.video_dir(vid)/'script.json', SpeechDocument(
        timeline=speech_store.timeline_key(scenes), edit_signature=timeline.timing_key(edited), cues=[cue]).model_dump(mode='json'))
    monkeypatch.setattr(speech, 'cue_status', lambda *_: {'status': 'READY'})
    monkeypatch.setattr(speech, 'cue_audio', lambda *_: (voice, None, None, None))
    result, warnings, _ = await timeline_bridge.import_results(vid, edited, source='speech', effects=False)
    assert not warnings
    voices = [c for c in result['clips'] if c['origin'].startswith('voice:')]
    assert [(c['start'], c['source_in'], c['duration'], c['speed']) for c in voices] == [(1, 0, 1, 1), (2, 1, 1, 1)]
    timeline.save(vid, 0, edited)
    speech.validate_document(speech_store.load_document(vid), scenes, vid)
    data['clips'][0]['speed'] = 1
    with pytest.raises(ValueError, match='đổi mốc'):
        await timeline_bridge.import_results(vid, TimelineDocument.model_validate(data), source='speech', effects=False)


async def test_scene_ai_shortening_is_only_a_draft_and_preserves_audio(story_context, monkeypatch):
    import asyncio
    import json
    from agent.api import tts as tts_api
    from agent.models.tts import ShortenSceneRequest
    from agent.services import media_jobs, subtitle_translation
    vid, _, scenes, voice, _ = story_context
    scene_id = scenes[0]['id']
    await crud.update_scene(scene_id, narrator_text='Lời đọc khá dài cần rút gọn.')
    monkeypatch.setattr(tts_api, 'narration_path_for_scene', lambda *_: voice)
    monkeypatch.setattr(tts_api, 'narration_matches_text', lambda *_: True)
    monkeypatch.setattr(tts_api, '_TTS_JOBS', {})
    answer = json.dumps({'cues': [{'id': scene_id, 'text': 'Lời ngắn.'}]})
    ai = AsyncMock(return_value=answer)
    monkeypatch.setattr(subtitle_translation, 'translate_batch', ai)
    original = voice.read_bytes()
    await tts_api.shorten_scene(uuid.UUID(vid), ShortenSceneRequest(scene_id=scene_id, frame=1))
    await asyncio.gather(*list(media_jobs._tasks.values()))
    job = media_jobs.latest(vid)
    assert job['status'] == 'COMPLETED'
    assert job['result'] == {'scene_id': scene_id, 'original': 'Lời đọc khá dài cần rút gọn.', 'text': 'Lời ngắn.'}
    assert (await crud.get_scene(scene_id))['narrator_text'] == job['result']['original']
    assert voice.read_bytes() == original
    assert not speech_store.load_document(vid).cues  # No overwrite of the user's SRT.
    ai.assert_awaited_once()
    # IDs must still be checked before presenting an AI suggestion.
    ai.return_value = json.dumps({'cues': [{'id': str(uuid.uuid4()), 'text': 'Wrong scene.'}]})
    await tts_api.shorten_scene(uuid.UUID(vid), ShortenSceneRequest(scene_id=scene_id, frame=1))
    await asyncio.gather(*list(media_jobs._tasks.values()))
    assert media_jobs.latest(vid)['status'] == 'FAILED'
    assert (await crud.get_scene(scene_id))['narrator_text'] == 'Lời đọc khá dài cần rút gọn.'


@pytest.mark.parametrize('source', ['scenes', 'speech', 'auto'])
async def test_story_import_respects_locked_captions(story_context, source):
    vid, doc, scenes, _, _ = story_context
    doc.tracks[2].locked = True
    if source == 'speech':
        speech_store.atomic_json(speech_store.video_dir(vid)/'script.json',
            SpeechDocument(timeline=speech_store.timeline_key(scenes), cues=[Cue(start=0, end=2, text='Translated.')]).model_dump(mode='json'))
    before = doc.model_dump(mode='json')
    with pytest.raises(ValueError, match='Mở khóa'):
        await timeline_bridge.import_results(vid, doc, source=source, effects=False)
    assert doc.model_dump(mode='json') == before


async def test_stale_speech_and_mixed_exports_do_not_replace_story_layers(story_context):
    vid, doc, _, _, _ = story_context
    speech_store.atomic_json(speech_store.video_dir(vid)/'script.json',
        SpeechDocument(timeline='old', cues=[Cue(start=0, end=2, text='Stale.')]).model_dump(mode='json'))
    result, warnings, count = await timeline_bridge.import_results(vid, doc, source='speech', effects=False)
    assert warnings and count == 0 and result == doc.model_dump(mode='json')
    scenes = story_context[2]
    scenes[0]['duration'] = 6
    speech_store.atomic_json(speech_store.video_dir(vid)/'script.json',
        SpeechDocument(timeline=speech_store.timeline_key(scenes), cues=[Cue(start=0, end=2, text='Updated SRT.')]).model_dump(mode='json'))
    result, warnings, count = await timeline_bridge.import_results(vid, doc, source='speech', effects=False)
    assert any('thời lượng cũ' in warning for warning in warnings)
    assert not any(c['text'] == 'Updated SRT.' for c in result['clips'])
    data = deepcopy(result)
    source_asset = timeline.asset(vid, data['clips'][0]['asset_id'])
    export = await timeline.register(vid, vid, f'export:{uuid.uuid4()}', 'Mixed export', Path(source_asset['path']), 'export')
    data['clips'] = [{**data['clips'][0], 'asset_id': export['id']}]
    result, _, count = await timeline_bridge.import_results(vid, TimelineDocument.model_validate(data), source='scenes', effects=False)
    assert count == 0 and result == data
