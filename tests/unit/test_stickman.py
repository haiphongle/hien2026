"""Contracts and real FFmpeg checks for voice-first Studio stories."""
import asyncio
import json
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
from agent.models.stickman import CreateRequest, PlanRequest
from agent.materials import get_material
from agent.services import editing, media_jobs, speech_store, stickman, timeline, video_assembly
from agent.services.video_concat import media_duration


def request(**changes):
    return CreateRequest(title='Một chút bình yên', preset_id='v3_test', scenes=[
        {'narration': 'Buông xuống để lòng nhẹ hơn.', 'action': 'The protagonist puts a heavy sack down.'},
        {'narration': 'Ngồi lại, tận hưởng một ngày bình yên.', 'action': 'The protagonist rests under a tree.'},
    ], **changes)


@pytest.mark.parametrize('field,value', [('title', '  '), ('preset_id', ''), ('style', 'realistic'), ('speed', float('nan'))])
def test_invalid_settings_are_rejected(field, value):
    data = request().model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        CreateRequest.model_validate(data)


def test_language_must_match_engine():
    with pytest.raises(ValidationError):
        request(language='en')


@pytest.mark.asyncio
async def test_srt_preserves_every_word_and_order(monkeypatch):
    source = '1\n00:00:00,000 --> 00:00:01,800\nSống thật nhẹ nhàng.\n\n2\n00:00:01,800 --> 00:00:04,500\nGiữ lại những điều vui.'
    groups = [c.text for c in stickman.srt_cues(source)]
    reply = {'title': 'Ngày bình yên', 'scenes': [{'cue_start': i, 'cue_end': i, 'action': 'The protagonist rests.'} for i in (1, 2)]}
    complete = AsyncMock(return_value=json.dumps(reply))
    monkeypatch.setattr(stickman.ai_tasks, 'complete', complete)
    result = await stickman.plan(PlanRequest(source=source, scene_count=2))
    assert [s.narration for s in result.scenes] == groups
    reply['scenes'].reverse()
    complete.return_value = json.dumps(reply)
    with pytest.raises(ValueError, match='sai thứ tự'):
        await stickman.plan(PlanRequest(source=source, scene_count=2))


LESSON_SRT = '''1
00:00:00,000 --> 00:00:02,300
Mỗi khi nản lòng, hãy nhớ năm câu này:[cite: 2]

2
00:00:02,300 --> 00:00:04,300
1. Ông trời không cho ai tất cả,[cite: 2]

3
00:00:04,300 --> 00:00:06,100
cũng không lấy đi của ai tất cả.[cite: 2]

4
00:00:06,100 --> 00:00:09,300
2. Chỉ có bản thân mình mới thay đổi được cuộc sống của chính mình.[cite: 2]

5
00:00:09,300 --> 00:00:11,500
3. Muốn có điều mình chưa từng có,[cite: 2]

6
00:00:11,500 --> 00:00:13,700
phải làm điều mình chưa từng làm.[cite: 2]

7
00:00:13,700 --> 00:00:16,200
4. Thành công nào cũng cần sự đánh đổi.[cite: 2]

8
00:00:16,200 --> 00:00:18,200
5. Thất bại chỉ là tạm thời,[cite: 2]

9
00:00:18,200 --> 00:00:20,300
bỏ cuộc mới thực sự hết cơ hội.[cite: 2]

10
00:00:20,300 --> 00:00:22,300
Mệt thì nghỉ một chút rồi tiếp tục,[cite: 2]

11
00:00:22,300 --> 00:00:24,200
mọi nỗ lực sẽ được đền đáp.[cite: 2]'''


@pytest.mark.asyncio
async def test_auto_srt_groups_by_meaning_without_retyping_narration(monkeypatch):
    ranges = [(1, 1), (2, 3), (4, 4), (5, 6), (7, 7), (8, 9), (10, 11)]
    reply = {'title': 'Năm lời nhắc', 'scenes': [
        {'cue_start': start, 'cue_end': end, 'action': 'The protagonist climbs one step.'}
        for start, end in ranges
    ]}
    complete = AsyncMock(return_value=json.dumps(reply))
    monkeypatch.setattr(stickman.ai_tasks, 'complete', complete)
    result = await stickman.plan(PlanRequest(source=LESSON_SRT))
    cues = stickman.srt_cues(LESSON_SRT)
    assert len(result.scenes) == 7
    assert result.scenes[0].narration == 'Mỗi khi nản lòng, hãy nhớ năm câu này:'
    assert [s.narration for s in result.scenes] == [
        ' '.join(c.text for c in cues[start-1:end]) for start, end in ranges
    ]
    assert ' '.join(s.narration for s in result.scenes) == ' '.join(c.text for c in cues)
    assert '[cite:' not in complete.call_args.args[1]
    assert 'narration' not in complete.call_args.kwargs['schema']['$defs']['SRTScene']['properties']
    complete.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('ranges', [
    [(2, 11)], [(1, 3), (3, 11)], [(1, 3), (5, 11)],
    [(1, 12)], [(1, 3), (4, 2)], [(1, 10)],
])
async def test_auto_srt_rejects_missing_duplicate_or_reordered_cues(monkeypatch, ranges):
    reply = {'title': 'Năm lời nhắc', 'scenes': [
        {'cue_start': start, 'cue_end': end, 'action': 'The protagonist rests.'}
        for start, end in ranges
    ]}
    monkeypatch.setattr(stickman.ai_tasks, 'complete', AsyncMock(return_value=json.dumps(reply)))
    with pytest.raises(ValueError, match='Chưa áp dụng kịch bản'):
        await stickman.plan(PlanRequest(source=LESSON_SRT))


@pytest.mark.asyncio
async def test_auto_srt_rejects_overlong_cue_before_calling_ai(monkeypatch):
    complete = AsyncMock()
    monkeypatch.setattr(stickman.ai_tasks, 'complete', complete)
    with pytest.raises(ValueError, match='SRT quá dài'):
        await stickman.plan(PlanRequest(source='1\n00:00:00,000 --> 00:00:02,000\n' + 'a'*451))
    complete.assert_not_awaited()


def test_citation_cleanup_keeps_other_bracketed_text_and_cues():
    assert stickman.clean_source('Giữ [cười] và lời này. [CITE: 2, 3]') == 'Giữ [cười] và lời này.'
    cues = stickman.srt_cues(LESSON_SRT)
    assert len(cues) == 11
    assert cues[0].text.startswith('Mỗi khi nản lòng, hãy nhớ năm câu này:')
    assert '[cite:' not in ' '.join(c.text for c in cues)


VISUAL_NOTES = '''Prompt phong cách chung (thêm vào cuối mỗi cảnh):
2D monochrome anime style, 9:16 vertical.
Cảnh 1: A couple sits at opposite ends of a sofa. The husband scrolls on his phone.
Cảnh 2: The wife washes dishes while watching their child.[cite: 4]'''


@pytest.mark.asyncio
@pytest.mark.parametrize('scene_count', [None, 4])
async def test_srt_with_visual_notes_preserves_narration_and_sends_notes_to_ai(monkeypatch, scene_count):
    cues = stickman.srt_cues(LESSON_SRT)
    ranges = [(1, 11)] if scene_count is None else [(1, 1), (2, 4), (5, 7), (8, 11)]
    expected = [' '.join(c.text for c in cues[start-1:end]) for start, end in ranges]
    scenes = [{'cue_start': start, 'cue_end': end, 'action': 'The protagonist washes dishes.'} for start, end in ranges]
    complete = AsyncMock(return_value=json.dumps({'title': 'Một ngày bình yên', 'scenes': scenes}))
    monkeypatch.setattr(stickman.ai_tasks, 'complete', complete)
    source = LESSON_SRT + '\n\n\n' + VISUAL_NOTES
    result = await stickman.plan(PlanRequest(source=source, scene_count=scene_count))
    assert [s.narration for s in result.scenes] == expected
    assert [c.model_dump(exclude={'id'}) for c in stickman.srt_cues(source)] == [c.model_dump(exclude={'id'}) for c in cues]
    prompt = complete.call_args.args[1]
    data = json.loads(prompt[prompt.index('{"language"'):])
    assert data['visual_notes'] == stickman.clean_source(VISUAL_NOTES)
    assert data['source'] is None
    assert 'Prompt' not in ' '.join(s.narration for s in result.scenes)
    complete.assert_awaited_once()


@pytest.mark.parametrize('header', ['Prompt:', '### Prompt phong cách chung:', '**Gợi ý hình ảnh**:', 'Hướng dẫn hình ảnh:'])
def test_visual_notes_support_explicit_headers_and_windows_newlines(header):
    source = (LESSON_SRT + '\n\n' + header + '\nA person rests.').replace('\n', '\r\n')
    narration, notes = stickman.srt_input(source)
    assert narration == stickman.clean_source(LESSON_SRT)
    assert notes == header + '\nA person rests.'


def test_spoken_prompt_header_stays_inside_srt_cue():
    source = '1\n00:00:00,000 --> 00:00:02,000\nPrompt: Hãy nghỉ ngơi một chút.'
    assert stickman.srt_input(source) == (source, None)
    assert stickman.srt_cues(source)[0].model_dump(exclude={'id'}) == speech_store.parse_srt(source)[0].model_dump(exclude={'id'})


@pytest.mark.asyncio
@pytest.mark.parametrize('source', [
    LESSON_SRT + '\n\n12\nCâu thiếu mốc thời gian.\n\n' + VISUAL_NOTES,
    LESSON_SRT + '\n\n' + VISUAL_NOTES + '\n\n12\n00:00:24,200 --> 00:00:25,000\nKhông được bỏ câu này.',
    LESSON_SRT + '\n\nCâu không có tiêu đề prompt hoặc mốc thời gian.',
])
async def test_visual_notes_do_not_hide_malformed_or_misplaced_cues(monkeypatch, source):
    complete = AsyncMock()
    monkeypatch.setattr(stickman.ai_tasks, 'complete', complete)
    with pytest.raises(ValueError):
        await stickman.plan(PlanRequest(source=source))
    complete.assert_not_awaited()


def test_general_subtitle_parser_still_rejects_visual_notes():
    with pytest.raises(ValueError, match='thiếu mốc thời gian'):
        speech_store.parse_srt(LESSON_SRT + '\n\n' + VISUAL_NOTES)


@pytest.mark.asyncio
async def test_plain_source_auto_accepts_count_but_explicit_count_is_checked(monkeypatch):
    reply = {'title': 'Ngày bình yên', 'scenes': [
        {'narration': 'Nghỉ một chút để thấy bình yên.', 'action': 'The protagonist rests.'}
    ]}
    monkeypatch.setattr(stickman.ai_tasks, 'complete', AsyncMock(return_value=json.dumps(reply)))
    assert len((await stickman.plan(PlanRequest(source='Sống nhẹ nhàng.'))).scenes) == 1
    with pytest.raises(ValueError, match='sai số cảnh'):
        await stickman.plan(PlanRequest(source='Sống nhẹ nhàng.', scene_count=2))


def test_caption_parts_cover_voice_without_losing_words():
    text = 'Một ngày bình yên bắt đầu khi ta buông xuống những điều không thể thay đổi và trân trọng những người luôn ở bên mình.'
    parts = list(stickman.caption_parts(text, 9.927))
    assert ' '.join(p[2] for p in parts) == text
    assert parts[0][0] == 0
    assert parts[-1][0]+parts[-1][1] == pytest.approx(round(9.927*30)/30)
    assert all(left[0]+left[1] == pytest.approx(right[0]) for left, right in zip(parts, parts[1:]))


@pytest.mark.asyncio
async def test_missing_voice_prevents_creating_video(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path/'flow.db')
    monkeypatch.setattr(api, 'flow_ready', lambda: None)
    monkeypatch.setattr(stickman, 'validate', AsyncMock(side_effect=ValueError('Giọng chưa sẵn sàng')))
    await schema.init_db()
    try:
        project = await crud.create_project('Original', material='realistic')
        with pytest.raises(HTTPException) as exc:
            await api.create(uuid.UUID(project['id']), request())
        assert exc.value.status_code == 422
        assert not await crud.list_videos(project['id'])
        assert (await crud.get_project(project['id']))['material'] == 'realistic'
    finally:
        await schema.close_db()


@pytest.mark.asyncio
@pytest.mark.parametrize('style', ['stickman_chalk', 'cartoon_studio', 'cartoon_story', '3d_pixar', 'anime', 'ghibli'])
async def test_api_saves_narration_and_does_not_change_project_style(tmp_path, monkeypatch, style):
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path/'flow.db')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path/'jobs')
    monkeypatch.setattr(api, 'flow_ready', lambda: None)
    monkeypatch.setattr(stickman, 'validate', AsyncMock())
    monkeypatch.setattr(stickman, 'start', lambda *args: {'status': 'QUEUED'})
    await schema.init_db()
    try:
        project = await crud.create_project('Original', material='realistic')
        planned = request(style=style)
        result = await api.create(uuid.UUID(project['id']), planned)
        scenes = await crud.list_scenes(result['video_id'])
        assert [s['narrator_text'] for s in scenes] == [s.narration for s in planned.scenes]
        assert all(get_material(style)['scene_prefix'] in s['image_prompt'] for s in scenes)
        assert not await crud.list_requests(video_id=result['video_id'])
        assert (await crud.get_project(project['id']))['material'] == 'realistic'
        assert speech_store.read_json(stickman.directory(result['video_id'])/'plan.json')['style'] == style
    finally:
        await schema.close_db()


@pytest.mark.asyncio
async def test_retry_uses_existing_saved_timeline(monkeypatch, tmp_path):
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path/'jobs')
    vid = str(uuid.uuid4())
    saved = {'revision': 3, 'document': {'clips': []}}
    monkeypatch.setattr(timeline, 'load', lambda _: saved)
    monkeypatch.setattr(crud, 'update_video', AsyncMock())
    render = AsyncMock()
    monkeypatch.setattr(stickman, 'export', render)
    voice = AsyncMock(side_effect=AssertionError('Existing voice must not be regenerated'))
    monkeypatch.setattr(stickman.tts, 'generate_video_narration', voice)
    stickman.start(vid, request(), {'id': str(uuid.uuid4()), 'name': 'Test'})
    await asyncio.wait_for(media_jobs._tasks[vid], 5)
    assert media_jobs.latest(vid)['status'] == 'COMPLETED'
    assert render.await_args.args[2] is saved
    voice.assert_not_awaited()


@pytest.mark.asyncio
async def test_corrupt_image_cache_is_replaced_atomically(monkeypatch, tmp_path):
    from agent.api import flow
    from fastapi import Response
    from io import BytesIO
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path/'jobs')
    vid, mid = str(uuid.uuid4()), str(uuid.uuid4())
    root = stickman.directory(vid)
    root.mkdir(parents=True)
    target = root/f'{mid}.image'
    target.write_bytes(b'partial jpeg')
    content = BytesIO()
    Image.new('RGB', (32, 32)).save(content, format='PNG')
    download = AsyncMock(return_value=Response(content.getvalue(), media_type='image/png'))
    monkeypatch.setattr(flow, 'download_image', download)
    assert await stickman.image_file(vid, mid) == target
    assert target.read_bytes() == content.getvalue()
    assert not list(root.glob('*.download'))
    await stickman.image_file(vid, mid)
    download.assert_awaited_once()


@pytest.mark.asyncio
async def test_completed_story_remains_visible_after_other_editor_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path/'jobs')
    vid = uuid.uuid4()
    monkeypatch.setattr(api, 'saved_video', AsyncMock(return_value=({}, request())))
    monkeypatch.setattr(media_jobs, 'latest', lambda _: {'kind': 'timeline-visuals', 'status': 'COMPLETED'})
    completed = {'id': str(uuid.uuid4()), 'kind': 'stickman', 'status': 'COMPLETED', 'result': {'url': '/export.mp4'}}
    speech_store.atomic_json(stickman.directory(str(vid))/'result.json', completed)
    assert await api.job(vid) == completed


@pytest.mark.asyncio
@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='FFmpeg required')
async def test_real_export_keeps_measured_scene_lengths_and_all_voice_layers(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path/'flow.db')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path/'jobs')
    monkeypatch.setattr(timeline, 'ROOT', tmp_path/'timeline')
    monkeypatch.setattr(editing, 'ROOT', tmp_path/'editing')
    monkeypatch.setattr(stickman, 'OUTPUT_DIR', tmp_path/'output')
    monkeypatch.setattr(video_assembly, 'OUTPUT_DIR', tmp_path/'output')
    worker = SimpleNamespace(abort=lambda: None)
    monkeypatch.setattr(stickman.speech_voices, 'engine_service', lambda _: SimpleNamespace(worker=worker))
    image = tmp_path/'image.png'
    Image.new('RGB', (180, 320), '#23262b').save(image)
    monkeypatch.setattr(stickman, 'image_file', AsyncMock(return_value=image))
    lengths = [.613, .947]

    async def voices(scenes, directory, **kwargs):
        results = []
        Path(directory).mkdir(parents=True, exist_ok=True)
        for scene, length in zip(scenes, lengths):
            path = Path(directory)/f"scene_{scene['display_order']:03d}_{scene['id']}.wav"
            with wave.open(str(path), 'wb') as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(48000)
                import array
                samples = array.array('h', (int(2000*math.sin(i*2*math.pi*440/48000)) for i in range(round(length*48000))))
                wav.writeframes(samples.tobytes())
            results.append(dict(status='COMPLETED', duration=length, audio_path=str(path)))
        return results
    monkeypatch.setattr(stickman.tts, 'generate_video_narration', voices)
    await schema.init_db()
    try:
        project = await crud.create_project('Original', material='realistic')
        character = await crud.create_character('Hero', media_id=str(uuid.uuid4()))
        await crud.link_character_to_project(project['id'], character['id'])
        video = await crud.create_video(project['id'], 'Test', orientation='VERTICAL')
        body = request(character_id=character['id'])
        for index, scene in enumerate(body.scenes, 1):
            row = await crud.create_scene(video['id'], index, scene.action)
            await crud.update_scene(row['id'], narrator_text=scene.narration, vertical_image_status='COMPLETED', vertical_image_media_id=str(uuid.uuid4()))
        stickman.start(video['id'], body, project)
        await asyncio.wait_for(media_jobs._tasks[video['id']], 90)
        job = media_jobs.latest(video['id'])
        assert job['status'] == 'COMPLETED', job
        saved = timeline.load(video['id'])['document']
        tracks = {t['id']: t for t in saved['tracks']}
        pictures = [c for c in saved['clips'] if tracks[c['track_id']]['kind'] == 'video']
        voice_clips = [c for c in saved['clips'] if tracks[c['track_id']]['role'] == 'voice']
        expected = [math.ceil(n*30)/30 for n in lengths]
        assert [c['duration'] for c in pictures] == pytest.approx(expected)
        assert voice_clips[1]['start'] == pytest.approx(expected[0])
        assert all(v['duration'] <= p['duration'] for v, p in zip(voice_clips, pictures))
        actual = await media_duration(editing.directory(video['id'])/'exports'/f"{job['result']['export_id']}.mp4")
        assert actual == pytest.approx(sum(expected), abs=.06)
        completed = await crud.get_video(video['id'])
        assert completed['status'] == 'COMPLETED'
        assert completed['vertical_url'] == job['result']['url']
    finally:
        await media_jobs.shutdown()
        await schema.close_db()
