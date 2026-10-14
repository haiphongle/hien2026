"""Long import, bounded/resumable ASR and timeline timing after two hours."""
import hashlib
import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from agent.media_limits import MAX_DURATION, MAX_SPEECH_CUES
from agent.models.timeline import TimelineDocument, RenderTimeline
from agent.models.editing import EditingPreviewRequest
from agent.models.speech import SpeechDocument
from agent.services import imported_video, media_jobs as jobs, speech_store, timeline, timeline_layers, transcription, subtitle_translation
from tests.unit.test_media_workbench import project, wait_job, clip


def stream_hash(path):
    return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
        '-c', 'copy', '-f', 'hash', '-hash', 'sha256', '-']).strip()


async def test_remux_preserves_encoded_picture_and_uploads_are_not_retained(project, tmp_path):
    p, client = project
    original = tmp_path / 'original.mp4'
    response = await client.post(f"/api/projects/{p['id']}/import-video?filename=test.mp4", content=clip(original))
    vid = response.json()['video']['id']
    assert (await wait_job(vid))['status'] == 'COMPLETED'
    assert imported_video.metadata(vid)['remux']
    assert stream_hash(original) == stream_hash(imported_video.source_path(vid))
    assert not (jobs.directory(vid) / 'upload').exists()


async def test_two_hour_source_can_import_save_and_export_its_tail(project, tmp_path, monkeypatch):
    p, client = project
    monkeypatch.setattr(timeline, 'ROOT', tmp_path / 'timeline')
    source = tmp_path / 'long.mp4'
    # Sparse, tiny fixture exercises real container duration/seek without a multi-GB file.
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=160x96:r=1:d=7205',
        '-vf', "drawbox=x=0:y=0:w=30:h=30:color=red:t=fill:enable='gte(t,7203)'",
        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', str(source)], check=True, capture_output=True)
    response = await client.post(f"/api/projects/{p['id']}/import-video?filename=two-hours.mp4", content=source.read_bytes())
    assert response.status_code == 202, response.text
    vid = response.json()['video']['id']
    assert (await wait_job(vid))['status'] == 'COMPLETED'
    local = imported_video.source_path(vid)
    before = hashlib.sha256(local.read_bytes()).hexdigest()
    asset = await timeline.register(vid, vid, 'original', 'Long video', local, 'source')
    doc = TimelineDocument.model_validate(timeline.initialize(vid, asset)['document'])
    assert doc.clips[0].duration == 7205
    timeline.save(vid, 0, doc)
    assert timeline.load(vid)['revision'] == 1
    tail = timeline_layers.range_document(doc, 7203, 7205)
    output = tmp_path / 'tail.mp4'
    result = await timeline.render(vid, tail, output, lambda **_: None, source_document=doc, range_start=7203)
    assert result['duration'] == pytest.approx(2, abs=.1)
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output), '-frames:v', '1', '-vf', 'crop=16:16:0:0', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
    assert raw[0] > 200 and raw[1] < 80
    assert hashlib.sha256(local.read_bytes()).hexdigest() == before


def test_three_hour_timeline_and_late_editing_times_stay_bounded():
    v, a, caption = [str(uuid4()) for _ in range(3)]
    doc = TimelineDocument(tracks=[{'id': v, 'name': 'Video', 'kind': 'video'}, {'id': a, 'name': 'Voice', 'kind': 'audio'},
        {'id': caption, 'name': 'Captions', 'kind': 'caption'}], clips=[{'id': uuid4(), 'track_id': v, 'asset_id': uuid4(), 'start': 0, 'duration': MAX_DURATION},
        *[{'id': uuid4(), 'track_id': caption, 'start': i*.9, 'duration': .9, 'text': 'Caption'} for i in range(MAX_SPEECH_CUES)],
        *[{'id': uuid4(), 'track_id': a, 'asset_id': uuid4(), 'start': i*.9, 'duration': .9} for i in range(MAX_SPEECH_CUES)]])
    assert len(doc.clips) == MAX_SPEECH_CUES*2+1
    assert EditingPreviewRequest(time=10000, settings={}).time == 10000
    assert RenderTimeline(revision=0, start=10799, end=10800).end == MAX_DURATION
    with pytest.raises(ValidationError):
        RenderTimeline(revision=0, end=MAX_DURATION+.1)


def test_chunks_overlap_without_duplicate_cues_and_keep_absolute_timing():
    assert transcription.windows(7200, 8500) == [(7200, 7800), (7800, 8400), (8400, 8500)]
    segment = lambda start, end, text: {'offsets': {'from': start*1000, 'to': end*1000}, 'text': text}
    first = {'transcription': [segment(1, 3, 'First'), segment(599, 601, 'Boundary')]}
    second = {'transcription': [segment(1, 3, 'Boundary'), segment(4, 6, 'Next')]}
    cues = transcription.append_chunk([], first, 0, 602, 0, 600)
    cues = transcription.append_chunk(cues, second, 598, 604, 600, 1200)
    assert [(c.start, c.end, c.text) for c in cues] == [(1, 3, 'First'), (599, 601, 'Boundary'), (602, 604, 'Next')]
    assert transcription.append_chunk(cues, {'transcription': []}, 1198, 602, 1200, 1800) == cues


def chunk(*segments):
    return {'transcription': [{'offsets': {'from': a*1000, 'to': b*1000}, 'text': text} for a, b, text in segments]}


def test_asr_retains_spoken_repetition_in_a_chunk_and_after_a_boundary_pause():
    cues = transcription.append_chunk([], chunk((1, 1.4, 'Không.'), (1.7, 2, 'Không.'), (599, 600, 'Không.')), 0, 602, 0, 600)
    cues = transcription.append_chunk(cues, chunk((1, 2, 'Không.'), (2.2, 3, 'Không.')), 598, 604, 600, 1200)
    assert [(c.start, c.end) for c in cues] == [(1, 1.4), (1.7, 2), (599, 600), (600.2, 601)]


@pytest.mark.parametrize('first,second,expected', [
    ([(599, 602, 'Qua ranh giới.')], [(0, 2, 'Qua ranh giới.')], ['Qua ranh giới.']),
    ([], [(0, 2, 'Chỉ phần sau nhận ra.')], ['Chỉ phần sau nhận ra.']),
    ([(599, 601, 'Xin chào')], [(1, 4, 'Xin chào các bạn.')], ['Xin chào', 'các bạn.']),
    ([(598, 599, 'Xin'), (599, 600, 'chào')], [(0, 2, 'Xin chào'), (3, 4, 'Tiếp theo')], ['Xin', 'chào', 'Tiếp theo']),
    ([(599, 602, 'Xin chào')], [(1, 3, 'Xin chào bạn')], ['Xin chào bạn']),
])
def test_asr_boundary_jitter_and_changed_segmentation_cannot_drop_both_copies(first, second, expected):
    cues = transcription.append_chunk([], chunk(*first), 0, 602, 0, 600)
    cues = transcription.append_chunk(cues, chunk(*second), 598, 604, 600, 1200)
    assert [c.text for c in cues] == expected
    assert all(a.end <= b.start for a, b in zip(cues, cues[1:]))


def test_long_speech_document_and_translation_checkpoint_share_the_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    cues = transcription.normalize(chunk(*[(i*.9, (i+1)*.9, 'Lời đọc.') for i in range(MAX_SPEECH_CUES)]), MAX_DURATION)
    assert len(SpeechDocument.model_validate_json(SpeechDocument(cues=cues).model_dump_json()).cues) == MAX_SPEECH_CUES
    vid = str(uuid4())
    saved = dict(version=1, total=MAX_SPEECH_CUES, rows=[], assignment={}, provider_selector='configured',
                 signature='', language='vi', synopsis='', glossary='')
    path = jobs.directory(vid) / 'translation-progress.json'
    speech_store.atomic_json(path, saved)
    assert subtitle_translation.checkpoint(vid)['total'] == MAX_SPEECH_CUES
    speech_store.atomic_json(path, {**saved, 'total': MAX_SPEECH_CUES+1})
    assert subtitle_translation.checkpoint(vid) is None
    with pytest.raises(ValidationError):
        SpeechDocument(cues=[*cues, cues[-1]])


@pytest.mark.parametrize('actual,start,end,expected', [(6, 0, None, 6), (10, 0, None, 10), (10, 4, 9, 9), (6, 4, 9, None)])
async def test_generated_asr_uses_measured_duration_not_scene_estimate(tmp_path, monkeypatch, actual, start, end, expected):
    monkeypatch.setattr(jobs, 'ROOT', tmp_path / 'jobs')
    monkeypatch.setattr(transcription, 'MODEL_DIR', tmp_path / 'models')
    transcription.MODEL_DIR.mkdir()
    (transcription.MODEL_DIR / 'ggml-small.bin').write_bytes(b'model')
    monkeypatch.setattr(transcription, 'binary', lambda: 'whisper-test')
    vid = str(uuid4())
    spans = []
    async def render(_, target):
        target.write_bytes(b'video')
        return {'duration': actual}
    async def process(args, **options):
        if args[0] == 'ffmpeg':
            spans.append((float(args[args.index('-ss')+1]), float(args[args.index('-t')+1])))
        else:
            Path(args[args.index('-of')+1]+'.json').write_text(json.dumps(chunk((0, spans[-1][1], 'Toàn bộ lời.'))))
        return ''
    monkeypatch.setattr(transcription, 'render_video_to_file', render)
    # The fake render contains no media; stub the real source-audio probe too.
    async def probe(_):
        return {'duration': actual, 'has_audio': True}
    monkeypatch.setattr(transcription.pacing, 'probe', probe)
    monkeypatch.setattr(jobs, 'process', process)
    transcription.start(vid, 'small', 'vi', SpeechDocument(), [{'duration': 8}], start_time=start, end_time=end)
    job = await wait_job(vid)
    if expected is None:
        assert job['status'] == 'FAILED'
        assert 'nằm trong video' in job['error']
        assert spans == []
    else:
        assert job['status'] == 'COMPLETED', job
        assert spans == [(start, expected-start)]
        result = speech_store.read_json(jobs.directory(vid) / 'transcript.json')
        assert result['end'] == expected and result['document']['cues'][-1]['end'] == expected
        assert result['scope'] == ('range' if start or expected < actual else 'all')


async def test_asr_rejects_source_without_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'ROOT', tmp_path / 'jobs')
    monkeypatch.setattr(transcription, 'MODEL_DIR', tmp_path / 'models')
    transcription.MODEL_DIR.mkdir()
    (transcription.MODEL_DIR / 'ggml-small.bin').write_bytes(b'model')
    monkeypatch.setattr(transcription, 'binary', lambda: 'whisper-test')
    async def render(_, target):
        target.write_bytes(b'silent-video')
        return {'duration': 6}
    async def probe(_):
        return {'duration': 6, 'has_audio': False}
    async def process(*args, **options):
        pytest.fail('Silent sources must be rejected before extraction or ASR')
    monkeypatch.setattr(transcription, 'render_video_to_file', render)
    monkeypatch.setattr(transcription.pacing, 'probe', probe)
    monkeypatch.setattr(jobs, 'process', process)
    vid = str(uuid4())
    transcription.start(vid, 'small', 'vi', SpeechDocument(), [])
    job = await wait_job(vid)
    assert job['status'] == 'FAILED'
    assert 'không có âm thanh' in job['error']
    assert not (jobs.directory(vid) / 'transcript.json').exists()


async def test_twelve_second_preview_clips_long_ducked_original_and_music(project, tmp_path, monkeypatch):
    monkeypatch.setattr(timeline, 'ROOT', tmp_path / 'timeline')
    vid = str(uuid4())
    source = tmp_path / 'seventy-seconds.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=160x96:r=1:d=70',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=70', '-c:v', 'libx264', '-preset', 'ultrafast',
        '-c:a', 'aac', '-shortest', str(source)], check=True, capture_output=True)
    asset = await timeline.register(vid, vid, 'original', 'Long video', source, 'source')
    data = timeline.initialize(vid, asset)['document']
    music = {'id': str(uuid4()), 'name': 'Music', 'kind': 'audio', 'role': 'music'}
    data['tracks'].append(music)
    data['clips'].append({**data['clips'][1], 'id': str(uuid4()), 'track_id': music['id'], 'group': None})
    data['ducking'] = {'enabled': True, 'amount': .2, 'original_amount': .2}
    doc = TimelineDocument.model_validate(data)
    before = doc.model_dump_json()
    output = tmp_path / 'preview.mp4'
    result = await timeline.render(vid, doc, output, lambda **_: None, preview=True)
    assert result['duration'] == pytest.approx(12, abs=.1)
    from tests.unit.test_timeline import rms
    assert rms(output, 11) > .05
    assert doc.model_dump_json() == before


async def test_asr_resumes_completed_parts_and_invalidates_changed_options(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'ROOT', tmp_path / 'jobs')
    monkeypatch.setattr(transcription, 'MODEL_DIR', tmp_path / 'models')
    transcription.MODEL_DIR.mkdir()
    (transcription.MODEL_DIR / 'ggml-small.bin').write_bytes(b'model')
    monkeypatch.setattr(transcription, 'binary', lambda: 'whisper-test')
    vid = str(uuid4())
    jobs.directory(vid).mkdir(parents=True)
    imported_video.source_path(vid).write_bytes(b'source')
    speech_store.atomic_json(jobs.directory(vid) / 'source.json', {'duration': 1300, 'status': 'READY'})
    calls = []
    fail = True
    async def process(args, **options):
        nonlocal fail
        if args[0] == 'ffmpeg':
            assert float(args[args.index('-t')+1]) <= 604
            Path(args[-1]).write_bytes(b'wav')
            return ''
        calls.append(args[args.index('-l')+1])
        if fail and len(calls) == 2:
            raise RuntimeError('interrupted')
        path = Path(args[args.index('-of')+1]+'.json')
        path.write_text(json.dumps({'transcription': [{'offsets': {'from': 4000, 'to': 6000}, 'text': f'Part {len(calls)}'}]}))
        return ''
    monkeypatch.setattr(jobs, 'process', process)
    # This test exercises checkpoints with fake source bytes, not ffprobe.
    async def probe(_):
        return {'duration': 1300, 'has_audio': True}
    monkeypatch.setattr(transcription.pacing, 'probe', probe)
    doc = SpeechDocument(revision=3, timeline='source-timeline')
    transcription.start(vid, 'small', 'vi', doc, [])
    assert (await wait_job(vid))['status'] == 'FAILED'
    assert transcription.checkpoint_status(vid)['completed'] == 1
    fail = False
    transcription.start(vid, 'small', 'vi', doc, [])
    assert (await wait_job(vid))['status'] == 'COMPLETED'
    assert len(calls) == 4  # One failed part is retried; completed part is reused.
    result = speech_store.read_json(jobs.directory(vid) / 'transcript.json')
    assert [c['start'] for c in result['document']['cues']] == [4, 602, 1202]
    assert transcription.checkpoint_status(vid)['completed'] == 3
    before = json.dumps(result)
    transcription.start(vid, 'small', 'en', doc, [], start_time=720, end_time=730)
    assert (await wait_job(vid))['status'] == 'COMPLETED'
    assert transcription.checkpoint_status(vid)['total'] == 1
    assert speech_store.read_json(jobs.directory(vid) / 'transcript.json')['document']['cues'][0]['start'] == 724
    assert before != json.dumps(speech_store.read_json(jobs.directory(vid) / 'transcript.json'))


async def test_large_final_mix_keeps_input_count_bounded_and_sample_positions(tmp_path):
    sound = tmp_path / 'tone.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=0.1',
        '-ac', '2', str(sound)], check=True, capture_output=True)
    stems = [(sound, i*.2, .1) for i in range(17)]
    reduced = await timeline_layers.collapse_stems(stems, tmp_path, lambda **_: None)
    assert len(reduced) == 2
    assert reduced[0][1:] == (0, pytest.approx(3.1))
    assert reduced[1] == stems[-1]
    info = await jobs.process(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(reduced[0][0])])
    assert float(json.loads(info)['format']['duration']) == pytest.approx(3.1, abs=.01)
