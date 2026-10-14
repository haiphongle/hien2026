import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.services import post_process


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
@pytest.mark.parametrize('styled', [False, True])
def test_cjk_glyphs_in_export_match_caption_renderer(tmp_path, styled):
    import numpy as np
    from PIL import Image
    from agent.models.editing import CaptionStyle
    from agent.services import editing
    if not any(Path(p).is_file() for p in editing.CJK_FONT_PATHS):
        pytest.skip('CJK font required')
    source, output = tmp_path / 'source.mp4', tmp_path / 'out.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=640x360:r=10:d=1',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', '-t', '1',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)],
                   check=True, capture_output=True, timeout=30)
    text = '中文汉语'
    style = CaptionStyle(size=6.5)
    assert post_process.add_scene_subtitles(str(source), [{'duration': 1, 'narrator_text': text}], str(output),
                                           caption_style=style.model_dump() if styled else None)
    overlay = tmp_path / 'expected.png'
    if styled:
        editing.caption_image(text, 640, 360, style, overlay)
    else:
        post_process._make_caption_image(text, 640, 360, editing.font_path('cjk'), overlay)
    with Image.open(overlay) as image:
        expected = np.asarray(image)
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '0.5', '-i', str(output),
                                   '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    exported = np.frombuffer(raw, np.uint8).reshape(360, 640, 3)
    expected_glyphs = (expected[:, :, :3].min(axis=2) > 160) & (expected[:, :, 3] > 180)
    exported_glyphs = (exported.min(axis=2) > 160) & (expected[:, :, 3] > 0)
    union = np.count_nonzero(expected_glyphs | exported_glyphs)
    assert union > 100
    assert np.count_nonzero(expected_glyphs & exported_glyphs) / union > .75
    info = editing.probe(output)
    assert info['has_audio'] and abs(info['duration'] - 1) < .1


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
@pytest.mark.parametrize('size', ['320x568', '640x360'])
def test_captions_end_with_speech_and_leave_silent_scenes_blank(tmp_path, size):
    source, output = tmp_path / 'source.mp4', tmp_path / 'out.mp4'
    subprocess.run([
        'ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=white:s={size}:r=25:d=4',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-t', '4',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source),
    ], check=True, capture_output=True, timeout=30)
    # Intentionally unsorted input; also leave one second after the final scene.
    scenes = [
        {'id': 'last', 'display_order': 2, 'duration': 1, 'narrator_text': 'Một ngày thật bình yên.'},
        {'id': 'first', 'display_order': 0, 'duration': 1, 'narrator_text': 'Xin chào Việt Nam.'},
        {'id': 'silent', 'display_order': 1, 'duration': 1, 'narrator_text': ''},
    ]
    assert post_process.add_scene_subtitles(str(source), scenes, str(output), {'first': 0.7, 'last': 0.6})
    probe = json.loads(subprocess.run([
        'ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(output),
    ], check=True, capture_output=True, text=True).stdout)
    assert abs(float(probe['format']['duration']) - 4) < 0.1
    assert {stream['codec_type'] for stream in probe['streams']} == {'audio', 'video'}
    for timestamp, visible in [(0.3, True), (0.9, False), (1.4, False), (2.3, True), (2.9, False), (3.6, False)]:
        pixels = subprocess.run([
            'ffmpeg', '-v', 'error', '-ss', str(timestamp), '-i', str(output),
            '-frames:v', '1', '-pix_fmt', 'gray', '-f', 'rawvideo', 'pipe:1',
        ], check=True, capture_output=True, timeout=30).stdout
        assert (sum(value < 150 for value in pixels) > 50) is visible, timestamp


def test_timeout_cleans_partial_export_and_preserves_source(tmp_path, monkeypatch):
    source, output = tmp_path / 'source.mp4', tmp_path / 'out.mp4'
    source.write_bytes(b'original')

    def run(command, **kwargs):
        if command[0] == 'ffprobe':
            return SimpleNamespace(returncode=0, stdout=json.dumps({'format': {'duration': 2}, 'streams': [{'width': 320, 'height': 568}]}))
        output.write_bytes(b'partial')
        raise subprocess.TimeoutExpired(command, 600)

    monkeypatch.setattr(post_process.subprocess, 'run', run)
    assert not post_process.add_scene_subtitles(str(source), [{'narrator_text': 'Xin chào', 'duration': 2}], str(output))
    assert not output.exists()
    assert source.read_bytes() == b'original'
    assert not post_process.add_scene_subtitles(str(source), [], str(source))
    assert source.read_bytes() == b'original'


def test_long_tokens_wrap_inside_frame():
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.truetype(next(path for path in post_process._SUBTITLE_FONT_CANDIDATES if Path(path).is_file()), 24)
    draw = ImageDraw.Draw(Image.new('RGB', (320, 240)))
    lines = post_process._wrap_caption_lines('https://' + 'example' * 30 + ' tiếng Việt', font, draw, 260)
    assert len(lines) > 2
    assert all(draw.textlength(line, font=font) <= 260 for line in lines)
