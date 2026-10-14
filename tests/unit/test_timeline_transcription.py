"""Whisper hears the saved edit, with the same mixer/timing as MP4 export."""
import asyncio
import array
import json
import math
from pathlib import Path
import subprocess
import wave
from uuid import UUID
from unittest.mock import AsyncMock

import pytest

from agent.db import crud
from agent.models.speech import Cue, SpeechDocument
from agent.models.timeline import Clip, TimelineDocument
from agent.services import imported_video, media_jobs, speech, speech_store, timeline, transcription
from tests.unit.test_audio_mix import audio_project
from tests.unit.test_timeline import timeline_context, uid, rms


@pytest.fixture
async def narrated_edit(timeline_context, tmp_path, monkeypatch):
    vid, source, _ = timeline_context
    silent = tmp_path/'silent.mp4'
    voice = tmp_path/'narrator.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-an', '-c:v', 'copy', str(silent)], check=True, capture_output=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', str(voice)], check=True, capture_output=True)
    picture = await timeline.register(vid, vid, 'original', 'Silent picture', silent, 'source')
    sound = await timeline.register(vid, vid, 'voice:test', 'Narrator', voice, 'voice')
    doc = timeline.initial(picture)
    doc.tracks[1].role = 'voice'
    doc.clips.append(Clip(id=uid(), asset_id=sound['id'], track_id=doc.tracks[1].id, start=0, duration=4))
    doc = TimelineDocument.model_validate(doc.model_dump())
    timeline.save(vid, 0, doc)
    scenes = await crud.list_scenes(vid)
    monkeypatch.setattr(imported_video, 'metadata', lambda _: None)
    monkeypatch.setattr(imported_video, 'timeline', AsyncMock(return_value=scenes))
    return vid, doc, scenes


async def test_audio_window_matches_export_after_speed_fade_and_ducking(narrated_edit, tmp_path):
    vid, doc, _ = narrated_edit
    doc.clips[-1] = doc.clips[-1].model_copy(update={'source_in': 1., 'duration': 2., 'speed': 1.5, 'fade_in': .5})
    doc.clips.append(doc.clips[-1].model_copy(update={'id': UUID(uid()), 'track_id': doc.tracks[2].id, 'gain': .2}))
    doc.tracks[2].role = 'music'
    doc.ducking.enabled = True
    doc = TimelineDocument.model_validate(doc.model_dump())
    movie, audio = tmp_path/'edit.mp4', tmp_path/'window.wav'
    await timeline.render(vid, doc, movie, lambda **_: None)
    await timeline.render_audio(vid, doc, audio, lambda **_: None, start=.2, end=1.8)
    with wave.open(str(audio)) as output:
        assert output.getframerate() == 16000 and output.getnchannels() == 1
        assert output.getnframes()/16000 == pytest.approx(1.6, abs=.02)
    # The ASR mix must use the exported gain/fade/ducking, including range offset.
    def mono_pcm_level(path, at):
        # Compare the same integer mono downmix. FFmpeg's floating-point stereo
        # downmix uses a different channel matrix than PCM16.
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path), '-t', '0.1',
            '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], check=True, capture_output=True).stdout
        samples = array.array('h', raw)
        return math.sqrt(sum(n*n for n in samples)/len(samples))/32768
    assert mono_pcm_level(audio, .6) == pytest.approx(mono_pcm_level(movie, .8), rel=.12, abs=.002)


async def test_transcription_uses_edit_and_invalidates_checkpoint_on_audio_change(narrated_edit, tmp_path, monkeypatch):
    vid, doc, scenes = narrated_edit
    weights = tmp_path/'weights'
    weights.mkdir()
    (weights/'ggml-small.bin').write_bytes(b'model-test')
    monkeypatch.setattr(transcription, 'MODEL_DIR', weights)
    monkeypatch.setattr(transcription, 'binary', lambda: 'test-whisper')
    forbidden = AsyncMock(side_effect=AssertionError('Raw silent video must not be assembled'))
    monkeypatch.setattr(transcription, 'render_video_to_file', forbidden)
    process = media_jobs.process
    calls = []
    async def fake_whisper(args, **options):
        if args[0] != 'test-whisper':
            return await process(args, **options)
        audio = Path(args[args.index('-f')+1])
        assert rms(audio, .3) > .01
        calls.append(audio)
        result = {'transcription': [{'offsets': {'from': 200, 'to': 1800}, 'text': 'Giọng mới trên bản dựng.'}], 'result': {'language': 'vi'}}
        Path(args[args.index('-of')+1]).with_suffix('.json').write_text(json.dumps(result))
    monkeypatch.setattr(media_jobs, 'process', fake_whisper)
    draft = SpeechDocument(timeline=speech_store.timeline_key(scenes))
    async def run():
        transcription.start(vid, 'small', 'vi', draft, scenes)
        await asyncio.gather(*list(media_jobs._tasks.values()))
        assert media_jobs.latest(vid)['status'] == 'COMPLETED', media_jobs.latest(vid)
    await run()
    result = speech_store.read_json(media_jobs.directory(vid)/'transcript.json')
    assert result['document']['edit_signature'] == timeline.timing_key(doc)
    assert result['document']['cues'][0]['start'] == .2 and len(calls) == 1
    await run()
    assert len(calls) == 1  # Completed chunks are reusable for the same audio snapshot.
    doc.clips[-1].gain = .5
    timeline.save(vid, 1, doc)
    await run()
    assert len(calls) == 2
    forbidden.assert_not_awaited()
    assert not list(media_jobs.directory(vid).glob('asr-*'))


async def test_silent_edit_is_reported_before_starting_whisper(narrated_edit, tmp_path, monkeypatch):
    vid, doc, scenes = narrated_edit
    monkeypatch.setattr(transcription, 'binary', lambda: 'test-whisper')
    monkeypatch.setattr(transcription, 'MODEL_DIR', tmp_path)
    (tmp_path/'ggml-small.bin').write_bytes(b'test')
    doc.tracks[1].muted = True
    timeline.save(vid, 1, doc)
    with pytest.raises(ValueError, match='chưa có lớp âm thanh'):
        transcription.start(vid, 'small', 'vi', SpeechDocument(), scenes)
    assert vid not in media_jobs._tasks


async def test_edit_signature_preserves_caption_coordinates_and_rejects_changed_picture(narrated_edit):
    vid, doc, scenes = narrated_edit
    script = SpeechDocument(timeline=speech_store.timeline_key(scenes), edit_signature=timeline.timing_key(doc),
                            cues=[Cue(start=0, end=4, text='Giọng ở mốc bản dựng.')])
    speech.validate_document(script, scenes, vid)
    doc.clips[-1].gain = .2
    timeline.save(vid, 1, doc)
    speech.validate_document(script, scenes, vid)
    too_long = script.model_copy(update={'cues': [Cue(start=0, end=4.2, text='Vượt bản dựng.')]})
    with pytest.raises(ValueError, match='Câu cuối vượt'):
        speech.validate_document(too_long, scenes, vid)
    doc.clips[0].speed = .5
    timeline.save(vid, 2, doc)
    with pytest.raises(ValueError, match='Bản dựng đã cắt'):
        speech.validate_document(script, scenes, vid)


async def test_quiet_chunk_keeps_full_length_for_whisper(narrated_edit, tmp_path):
    vid, doc, _ = narrated_edit
    doc.clips[-1].start = 2
    doc.clips[-1].duration = 2
    doc = TimelineDocument.model_validate(doc.model_dump())
    audio = tmp_path/'quiet.wav'
    await timeline.render_audio(vid, doc, audio, lambda **_: None, start=0, end=1, allow_silence=True)
    with wave.open(str(audio)) as output:
        assert output.getnframes()/output.getframerate() == 1
    assert rms(audio, .2) == 0
