import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.services import post_process, video_assembly, video_concat


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
@pytest.mark.parametrize("cancellable", [False, True])
async def test_merging_flow_clips_with_different_time_bases_preserves_timing(tmp_path, cancellable):
    paths = []
    for index, time_base in enumerate(['12288', '90000']):
        path = tmp_path / f'clip-{index}.mp4'
        subprocess.run([
            'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=blue:s=160x284:r=24:d=1',
            '-f', 'lavfi', '-i', 'sine=duration=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-video_track_timescale', time_base, str(path),
        ], check=True, capture_output=True, timeout=30)
        paths.append(str(path))
    progress = []
    output = tmp_path / 'joined.mp4'
    if cancellable:
        assert await video_concat.merge_videos(paths, str(output), progress.append)
    else:
        assert post_process.merge_videos(paths, str(output), progress.append)
    data = json.loads(subprocess.run([
        'ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,nb_frames:format=duration',
        '-of', 'json', str(output),
    ], check=True, capture_output=True, text=True).stdout)
    assert float(data['format']['duration']) == pytest.approx(2, abs=0.1)
    assert next(s for s in data['streams'] if s['codec_type'] == 'video')['nb_frames'] == '48'
    assert progress[-1] == 1
    assert progress == sorted(progress)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
@pytest.mark.parametrize("with_source_audio", [False, True])
def test_ffmpeg_audio_mix_smoke(tmp_path, with_source_audio):
    video = tmp_path / "video.mp4"
    music = tmp_path / "music.wav"
    narration = tmp_path / "narration.wav"
    music_video = tmp_path / "music-video.mp4"
    narrated_video = tmp_path / "narrated-video.mp4"
    source = [
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=160x90:d=2:r=24",
    ]
    if with_source_audio:
        source.extend(["-f", "lavfi", "-i", "sine=frequency=440:duration=2"])
    source.extend(["-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p"])
    if with_source_audio:
        source.extend(["-c:a", "aac"])
    else:
        source.append("-an")
    source.append(str(video))
    subprocess.run(source, check=True, capture_output=True, timeout=30)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=880:duration=1", str(music),
    ], check=True, capture_output=True, timeout=30)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=660:duration=0.5", str(narration),
    ], check=True, capture_output=True, timeout=30)

    assert post_process.add_music(str(video), str(music), str(music_video))
    assert post_process.add_scene_narrations(str(video), [(str(narration), 0.5)], str(narrated_video))
    for output in (music_video, narrated_video):
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type",
            "-of", "csv=p=0", str(output),
        ], check=True, capture_output=True, text=True, timeout=30)
        assert probe.stdout.strip() == "audio"


@pytest.mark.parametrize("has_audio", [False, True])
def test_add_music_supports_video_with_or_without_original_audio(tmp_path, monkeypatch, has_audio):
    video = tmp_path / "video.mp4"
    music = tmp_path / "music.mp3"
    video.write_bytes(b"video")
    music.write_bytes(b"music")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            streams = [{"codec_type": "audio"}] if has_audio else [{"codec_type": "video"}]
            metadata = {"format": {"duration": "16"}, "streams": streams}
            return SimpleNamespace(returncode=0, stdout=json.dumps(metadata), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(post_process.subprocess, "run", fake_run)

    assert post_process.add_music(str(video), str(music), str(tmp_path / "mixed.mp4"), music_volume=0.2)
    ffmpeg = commands[-1]
    assert "-stream_loop" in ffmpeg
    filter_text = ffmpeg[ffmpeg.index("-filter_complex") + 1]
    assert "afade=t=in" in filter_text
    assert ("amix=inputs=2" in filter_text) is has_audio
    assert "[1:a]" in filter_text


def test_add_scene_narrations_offsets_each_voice_track(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    video.write_bytes(b"video")
    first.write_bytes(b"audio")
    second.write_bytes(b"audio")
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            if "csv=p=0" in command:
                return SimpleNamespace(returncode=0, stdout="4.0", stderr="")
            metadata = {"format": {"duration": "24"}, "streams": []}
            return SimpleNamespace(returncode=0, stdout=json.dumps(metadata), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(post_process.subprocess, "run", fake_run)

    assert post_process.add_scene_narrations(
        str(video), [(str(first), 0), (str(second), 8)], str(tmp_path / "narrated.mp4"),
    )
    filters = commands[-1][commands[-1].index("-filter_complex") + 1]
    assert "adelay=0:all=1" in filters
    assert "adelay=8000:all=1" in filters
    assert "amix=inputs=3" in filters


@pytest.mark.asyncio
async def test_render_single_scene_refreshes_source_without_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr(video_assembly, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(video_assembly, "_media_duration", AsyncMock(return_value=3.5))
    monkeypatch.setattr(video_assembly.crud, "get_video", AsyncMock(return_value={"orientation": "VERTICAL"}))
    scene = {"id": "scene-one", "display_order": 0, "vertical_video_status": "COMPLETED", "vertical_video_url": "https://flow.example/first.mp4"}
    monkeypatch.setattr(video_assembly.crud, "list_scenes", AsyncMock(side_effect=[
        [scene], [{**scene, "vertical_video_url": "https://flow.example/second.mp4"}],
    ]))
    update = AsyncMock()
    monkeypatch.setattr(video_assembly.crud, "update_video", update)

    async def fake_download(url: str, target: Path):
        target.write_text(url.rsplit("/", 1)[-1])

    monkeypatch.setattr(video_assembly, "_download_clip", fake_download)
    path = tmp_path / "source.mp4"
    result = await video_assembly.render_video_to_file("33333333-3333-3333-3333-333333333333", path, "VERTICAL")
    assert result['duration'] == 3.5
    assert result['scene_durations'] == [{'id': 'scene-one', 'duration': 3.5}]
    assert path.read_text() == "first.mp4"
    await video_assembly.render_video_to_file("33333333-3333-3333-3333-333333333333", path, "VERTICAL")
    assert path.read_text() == "second.mp4"
    update.assert_not_called()
