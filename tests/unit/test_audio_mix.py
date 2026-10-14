import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.api import videos
from agent.db import crud, schema
from agent.services import audio_mix, video_assembly, video_concat


@pytest.fixture
async def audio_project(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(audio_mix, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(video_assembly, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(video_assembly, "_media_duration", AsyncMock(return_value=8.0))
    lock = asyncio.Lock()
    monkeypatch.setattr(video_assembly, "video_export_lock", lock)
    monkeypatch.setattr(audio_mix, "video_export_lock", lock)
    await schema.init_db()
    try:
        project = await crud.create_project(name="Audio test", material="realistic")
        video = await crud.create_video(project_id=project["id"], title="Existing export", orientation="VERTICAL")
        await crud.update_video(video["id"], thumbnail_url="/chosen.jpg", vertical_url="/previous.mp4", status="COMPLETED")
        scene = await crud.create_scene(video["id"], 0, "Scene")
        await crud.update_scene(scene["id"], vertical_video_status="COMPLETED", vertical_video_url="https://clips.example/source.mp4")
        directory = tmp_path / "audio_test"
        (directory / "music").mkdir(parents=True)
        (directory / "music" / "saved.wav").write_bytes(b"music")
        (directory / "audio_mix").mkdir()
        settings = directory / "audio_mix" / f"{video['id']}.json"
        settings.write_text(json.dumps({"music": {"filename": "saved.wav", "volume": 0.2}}))
        output = video_assembly.assembled_path(video["id"])
        output.parent.mkdir()
        output.write_bytes(b"previous export")
        downloads = []

        async def download(url, target):
            downloads.append(url)
            target.write_bytes(b"clean source")

        monkeypatch.setattr(video_assembly, "_download_clip", download)
        yield video["id"], scene["id"], directory, output, settings, downloads
    finally:
        await schema.close_db()


async def test_missing_saved_narration_keeps_previous_export(audio_project):
    vid, sid, _, output, settings, downloads = audio_project
    await crud.update_scene(sid, narrator_text="New narration")
    settings.write_text(json.dumps({"narration": {"narration_volume": 0.8}}))
    previous = settings.read_bytes()
    with pytest.raises(HTTPException) as error:
        await videos.mix_video_music(vid, videos.MusicMixRequest(filename="saved.wav"))
    assert error.value.status_code == 409
    assert not downloads
    assert output.read_bytes() == b"previous export"
    assert settings.read_bytes() == previous
    assert (await crud.get_video(vid))["vertical_url"] == "/previous.mp4"


async def test_failed_ffmpeg_keeps_previous_export_and_settings(audio_project, monkeypatch):
    vid, _, _, output, settings, _ = audio_project
    previous = settings.read_bytes()
    monkeypatch.setattr(audio_mix, "add_music", lambda *args, **kwargs: False)
    with pytest.raises(HTTPException) as error:
        await videos.mix_video_music(vid, videos.MusicMixRequest(filename="saved.wav", volume=0.4))
    assert error.value.status_code == 502
    assert output.read_bytes() == b"previous export"
    assert settings.read_bytes() == previous
    assert list(output.parent.iterdir()) == [output]
    assert (await crud.get_video(vid))["thumbnail_url"] == "/chosen.jpg"


async def test_remixing_keeps_both_layers_and_starts_from_clean_clips(audio_project, monkeypatch):
    vid, sid, directory, output, settings, downloads = audio_project
    await crud.update_scene(sid, narrator_text="Hello")
    (directory / "tts").mkdir()
    voice = directory / "tts" / "voice.wav"
    voice.write_bytes(b"voice")
    monkeypatch.setattr(audio_mix, "narration_path_for_scene", lambda *args: voice)
    monkeypatch.setattr(audio_mix, "narration_matches_text", lambda *args: True)

    def narration(source, tracks, target, **options):
        assert tracks == [(str(voice), 0.0, 8.0)]
        Path(target).write_bytes(Path(source).read_bytes() + f"|voice={options['narration_volume']}".encode())
        return True

    def music(source, track, target, **options):
        Path(target).write_bytes(Path(source).read_bytes() + f"|music={options['music_volume']}".encode())
        return True

    monkeypatch.setattr(audio_mix, "add_scene_narrations", narration)
    monkeypatch.setattr(audio_mix, "add_music", music)
    await videos.mix_video_narration(vid, videos.NarrationMixRequest(narration_volume=0.7))
    assert output.read_bytes() == b"clean source|voice=0.7|music=0.2"
    await videos.mix_video_music(vid, videos.MusicMixRequest(filename="saved.wav", volume=0.3))
    assert output.read_bytes() == b"clean source|voice=0.7|music=0.3"
    assert len(downloads) == 2
    assert json.loads(settings.read_text())["narration"]["narration_volume"] == 0.7
    assert (await crud.get_video(vid))["thumbnail_url"] == "/chosen.jpg"


async def test_assembly_waits_for_audio_export(audio_project, monkeypatch):
    vid, _, _, _, _, _ = audio_project
    started, release = asyncio.Event(), asyncio.Event()
    original_render = audio_mix.render_video_to_file

    async def delayed_render(*args):
        started.set()
        await release.wait()
        return await original_render(*args)

    def music(source, track, target, **options):
        Path(target).write_bytes(b"mixed")
        return True

    monkeypatch.setattr(audio_mix, "render_video_to_file", delayed_render)
    monkeypatch.setattr(audio_mix, "add_music", music)
    mixing = asyncio.create_task(videos.mix_video_music(vid, videos.MusicMixRequest(filename="saved.wav")))
    await asyncio.wait_for(started.wait(), timeout=2)
    assembly = asyncio.create_task(video_assembly.assemble_video(vid, "VERTICAL"))
    await asyncio.sleep(0)
    assert not assembly.done()
    release.set()
    await asyncio.wait_for(asyncio.gather(mixing, assembly), timeout=5)


@pytest.mark.parametrize('success', [False, True])
async def test_subtitles_are_applied_last_and_failed_export_keeps_previous(audio_project, monkeypatch, success):
    import wave
    vid, sid, directory, output, settings, _ = audio_project
    await crud.update_scene(sid, narrator_text='Xin chào')
    (directory / 'tts').mkdir()
    voice = directory / 'tts' / 'voice.wav'
    with wave.open(str(voice), 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b'\0\0' * 12000)
    monkeypatch.setattr(audio_mix, 'narration_path_for_scene', lambda *args: voice)
    monkeypatch.setattr(audio_mix, 'narration_matches_text', lambda *args: True)
    def layer(source, track, target, **options):
        Path(target).write_bytes(Path(source).read_bytes() + b'|audio')
        return True
    def subtitles(source, scenes, target, durations):
        assert Path(source).read_bytes() == b'clean source|audio|audio'
        assert durations == {sid: 0.5}
        Path(target).write_bytes(b'captions and audio')
        return success
    monkeypatch.setattr(audio_mix, 'add_scene_narrations', layer)
    monkeypatch.setattr(audio_mix, 'add_music', layer)
    monkeypatch.setattr(audio_mix, 'add_scene_subtitles', subtitles)
    before = settings.read_bytes()
    request = videos.NarrationMixRequest(burn_subtitles=True)
    if success:
        await videos.mix_video_narration(vid, request)
        assert output.read_bytes() == b'captions and audio'
        assert json.loads(settings.read_text())['narration']['burn_subtitles'] is True
    else:
        with pytest.raises(HTTPException) as exc:
            await videos.mix_video_narration(vid, request)
        assert exc.value.status_code == 502
        assert output.read_bytes() == b'previous export'
        assert settings.read_bytes() == before
    assert list(output.parent.iterdir()) == [output]


async def test_float_wav_narration_exports_with_subtitles_and_progress(audio_project, monkeypatch):
    """OmniVoice's IEEE-float WAV must work through the actual export pipeline."""
    import shutil
    import subprocess
    from agent.services import post_process, audio_mix_jobs
    from agent.services.voice_samples import wav_duration

    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required')
    vid, sid, directory, output, settings, _ = audio_project
    source = directory / 'source.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=160x284:r=25:d=2',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source),
    ], check=True, capture_output=True, timeout=30)
    (directory / 'tts').mkdir()
    voice = directory / 'tts' / 'float.wav'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.8',
        '-c:a', 'pcm_f32le', str(voice),
    ], check=True, capture_output=True, timeout=30)
    shutil.copyfile(voice, directory / 'music' / 'saved.wav')
    assert wav_duration(voice) == pytest.approx(0.8)
    await crud.update_scene(sid, narrator_text='Xin chào Việt Nam.')
    monkeypatch.setattr(video_assembly, '_media_duration', video_concat.media_duration)
    monkeypatch.setattr(audio_mix, 'narration_path_for_scene', lambda *args: voice)
    monkeypatch.setattr(audio_mix, 'narration_matches_text', lambda *args: True)

    async def copy_source(url, target):
        shutil.copyfile(source, target)
    monkeypatch.setattr(video_assembly, '_download_clip', copy_source)
    events = []
    original = audio_mix_jobs.finish_audio_mix
    async def observe(*args, progress_callback):
        def report(stage, value):
            events.append((stage, value))
            progress_callback(stage, value)
        return await original(*args, progress_callback=report)
    monkeypatch.setattr(audio_mix_jobs, 'finish_audio_mix', observe)
    queued = await videos.queue_narration_mix(vid, videos.NarrationMixRequest(burn_subtitles=True))
    assert queued['status'] == 'QUEUED'
    await audio_mix_jobs.drain_jobs()
    result = await videos.get_audio_mix_job(vid)
    assert result['status'] == 'COMPLETED', result
    assert result['progress'] == 100
    assert {stage for stage, _ in events} >= {'waiting', 'downloading', 'assembling', 'narration', 'music', 'subtitles', 'saving'}
    assert ('subtitles', 1.0) in events
    assert post_process._media_duration(str(output)) == pytest.approx(2, abs=0.1)
    for timestamp, has_caption in [(0.3, True), (1.4, False)]:
        pixels = subprocess.run([
            'ffmpeg', '-v', 'error', '-ss', str(timestamp), '-i', str(output),
            '-frames:v', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1',
        ], check=True, capture_output=True, timeout=30).stdout
        assert (sum(value < 150 for value in pixels) > 50) is has_caption
    # Music also runs as a job and preserves the saved narration/subtitle layer.
    await videos.queue_music_mix(vid, videos.MusicMixRequest(filename='saved.wav', volume=0.1))
    await audio_mix_jobs.drain_jobs()
    assert (await videos.get_audio_mix_job(vid))['status'] == 'COMPLETED'
    assert json.loads(settings.read_text())['narration']['burn_subtitles'] is True


async def test_invalid_narration_fails_before_downloading(audio_project, monkeypatch):
    vid, sid, directory, output, settings, downloads = audio_project
    await crud.update_scene(sid, narrator_text='Xin chào')
    (directory / 'tts').mkdir()
    voice = directory / 'tts' / 'broken.wav'
    voice.write_bytes(b'not audio')
    monkeypatch.setattr(audio_mix, 'narration_path_for_scene', lambda *args: voice)
    monkeypatch.setattr(audio_mix, 'narration_matches_text', lambda *args: True)
    before = settings.read_bytes()
    with pytest.raises(HTTPException) as exc:
        await videos.mix_video_narration(vid, videos.NarrationMixRequest(burn_subtitles=True))
    assert exc.value.status_code == 422
    assert 'scene 1' in exc.value.detail
    assert not downloads
    assert output.read_bytes() == b'previous export'
    assert settings.read_bytes() == before


async def test_timed_speech_mix_preserves_music_and_rejects_stale_script(audio_project, monkeypatch):
    import wave
    from agent.models.speech import Cue, SpeechDocument
    from agent.services import speech, speech_store, vieneu, audio_mix_jobs, post_process
    vid, _, directory, output, settings, _ = audio_project
    monkeypatch.setattr(speech_store, 'ROOT', directory / 'speech')
    scenes = await crud.list_scenes(vid)
    cue = Cue(start=1, end=3, text='Xin chào.', speaker='Phúc', voice_id=vieneu.catalog()[0]['id'])
    doc = SpeechDocument(revision=1, mode='srt', timeline=speech_store.timeline_key(scenes), cues=[cue])
    speech_store.atomic_json(speech_store.video_dir(vid) / 'script.json', doc.model_dump(mode='json'))
    voice, _, _, signature = speech.cue_audio(vid, cue)
    voice.parent.mkdir(parents=True)
    with wave.open(str(voice), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(48000)
        audio.writeframes(b'\0\0' * 24000)
    speech_store.atomic_json(Path(str(voice) + '.json'), {'signature': signature})
    observed = []
    def layer(source, track, target, **kwargs):
        observed.append(track)
        Path(target).write_bytes(Path(source).read_bytes() + b'|audio')
        return True
    def captions(source, caption_scenes, target, durations, **kwargs):
        assert caption_scenes == [{'duration': 1, 'narrator_text': ''}, {'duration': 2, 'narrator_text': 'Xin chào.'}]
        Path(target).write_bytes(Path(source).read_bytes() + b'|captions')
        return True
    monkeypatch.setattr(audio_mix, 'add_scene_narrations', layer)
    monkeypatch.setattr(audio_mix, 'add_music', layer)
    monkeypatch.setattr(audio_mix, 'add_scene_subtitles', captions)
    await videos.queue_narration_mix(vid, videos.NarrationMixRequest(speech_revision=1, burn_subtitles=True))
    await audio_mix_jobs.drain_jobs()
    assert (await videos.get_audio_mix_job(vid))['status'] == 'COMPLETED'
    assert (await videos.get_audio_mix_job(vid))['kind'] == 'speech'
    assert observed[0] == [(str(voice), 1, 2)]
    assert output.read_bytes() == b'clean source|audio|audio|captions'
    assert json.loads(settings.read_text())['music']['filename'] == 'saved.wav'
    # Subsequent music exports keep the saved SRT layer; editing the script makes
    # that layer stale instead of silently mixing audio from an earlier revision.
    doc.revision = 2
    speech_store.atomic_json(speech_store.video_dir(vid) / 'script.json', doc.model_dump(mode='json'))
    before = output.read_bytes()
    with pytest.raises(HTTPException) as exc:
        await videos.mix_video_music(vid, videos.MusicMixRequest(filename='saved.wav'))
    assert exc.value.status_code == 409
    assert output.read_bytes() == before
