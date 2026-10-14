"""Real layout/audio preservation plus export safety and settings revision checks."""
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.models.editing import ColorSettings, EditRegion, EditingSettings, FrameSettings
from agent.services import audio_mix, editing
from agent.services.speech_store import timeline_key
from tests.unit.test_audio_mix import audio_project  # Shared isolated DB/source fixture.

VID = 'ab4a478c-3393-4647-9ac8-7a63b64a2a9d'


@pytest.mark.parametrize('text', ['中文汉语', '日本語こんにちは', '한국어'])
def test_cjk_fallback_has_real_glyphs_and_keeps_latin_fonts(text):
    from PIL import ImageFont
    if not any(Path(p).is_file() for p in editing.CJK_FONT_PATHS):
        pytest.skip('CJK font required')
    path = editing.font_path('sans', text)
    font = ImageFont.truetype(path, 24)
    missing = font.getmask(chr(0x378))  # Unassigned character: the font's missing-glyph box.
    missing_glyph = (missing.size, bytes(missing))
    for char in text:
        mask = font.getmask(char)
        assert (mask.size, bytes(mask)) != missing_glyph, char
    for font_id in ('sans', 'sans-bold', 'serif', 'mono'):
        assert editing.font_path(font_id, 'Đường về bình yên · Hello') == editing.font_path(font_id)
    assert any(f['id'] == 'cjk' for f in editing.fonts())


def test_missing_cjk_font_has_clear_error_and_does_not_accept_arbitrary_paths(monkeypatch):
    monkeypatch.setattr(editing, 'CJK_FONT_PATHS', ())
    with pytest.raises(ValueError, match='Noto Sans CJK'):
        editing.font_path('sans', '中文')
    with pytest.raises(ValueError, match='danh sách'):
        editing.font_path('../../secret.ttf', '中文')
    assert editing.font_path('sans', 'Xin chào')


def test_settings_survive_reload_conflict_and_export_record(tmp_path, monkeypatch):
    monkeypatch.setattr(editing, 'ROOT', tmp_path)
    state = editing.save(VID, 0, EditingSettings(mode='voice'))
    assert editing.load(VID)['settings']['mode'] == 'voice'
    with pytest.raises(ValueError, match='tab khác'):
        editing.save(VID, 0, EditingSettings())
    editing.save(VID, state['revision'], EditingSettings(mode='subtitles'))
    editing.record_export(VID, {'url': '/older.mp4', 'revision': 1})
    assert editing.load(VID)['revision'] == 2
    assert editing.load(VID)['settings']['mode'] == 'subtitles'
    assert editing.load(VID)['last_export']['revision'] == 1
    editing.record_export(VID, {'url': '/second.mp4', 'revision': 2})
    assert [r['url'] for r in editing.load(VID)['exports']] == ['/second.mp4', '/older.mp4']


@pytest.mark.parametrize('value', [{'width': 241, 'preset': 'custom'}, {'width': 3840, 'height': 3840, 'preset': 'custom'}, {'zoom': float('nan')}, {'color': 'red;movie=/tmp/x'}])
def test_reject_invalid_layout(value):
    with pytest.raises(ValidationError):
        FrameSettings(**value)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
@pytest.mark.parametrize('fit,background,zoom', [('contain', 'color', 1), ('contain', 'blur', 1), ('cover', 'color', 1.7)])
def test_real_resize_preview_and_caption_timing_keep_audio(tmp_path, fit, background, zoom):
    from PIL import Image
    from agent.services.post_process import add_scene_subtitles
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=640x360:r=25:d=2',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, capture_output=True)
    original = source.read_bytes()
    settings = EditingSettings(frame=FrameSettings(preset='custom', width=320, height=480, fit=fit, background=background, zoom=zoom), mode='subtitles')
    target = tmp_path / 'framed.mp4'
    progress = []
    editing.transform(source, target, settings, progress.append)
    info = editing.probe(target)
    assert (info['width'], info['height']) == (320, 480)
    assert info['has_audio'] and abs(info['duration'] - 2) < .1
    assert progress[-1] == 1
    preview = tmp_path / 'preview.png'
    editing.still_preview(source, preview, settings, .3, 'Xin chào Việt Nam')
    assert Image.open(preview).size == (320, 480)
    subtitled = tmp_path / 'subtitled.mp4'
    assert add_scene_subtitles(str(target), [{'id': 'one', 'duration': 2, 'narrator_text': 'Xin chào Việt Nam'}], str(subtitled), {'one': .7}, caption_style=settings.captions.model_dump())
    assert editing.probe(subtitled)['has_audio']
    assert source.read_bytes() == original
    # Check selected top placement and RGB style in renderer without browser font substitution.
    style = settings.captions.model_copy(update={'position': 'top', 'align': 'left', 'color': '#ff0000', 'background_opacity': 0})
    png = tmp_path / 'caption.png'
    editing.caption_image('Đường về bình yên', 320, 480, style, png)
    with Image.open(png) as image:
        box = image.getbbox()
        assert box and box[1] < 120 and box[3] < 240


@pytest.mark.parametrize('mode', ['original', 'subtitles', 'voice', 'voice_subtitles'])
async def test_editor_export_is_separate_and_captions_do_not_require_tts(audio_project, monkeypatch, mode):
    vid, sid, _, original, audio_settings, downloads = audio_project
    from agent.db import crud
    from agent.services.imported_video import timeline
    await crud.update_scene(sid, narrator_text='Chào buổi sáng')
    scenes = await timeline(vid)
    before = audio_settings.read_bytes()
    output = original.parent / 'new-export.mp4'
    def transform(source, target, settings, progress, **options):
        Path(target).write_bytes(Path(source).read_bytes() + b'|layout')
    def captions(source, scenes, target, durations, **kwargs):
        Path(target).write_bytes(Path(source).read_bytes() + b'|captions')
        return True
    monkeypatch.setattr(editing, 'transform', transform)
    monkeypatch.setattr(editing, 'source_volume', lambda source, *args: source)
    monkeypatch.setattr(editing, 'probe', lambda p: {'duration': 8, 'width': 320, 'height': 480})
    monkeypatch.setattr(audio_mix, 'add_scene_subtitles', captions)
    voiced = mode in {'voice', 'voice_subtitles'}
    captioned = mode in {'subtitles', 'voice_subtitles'}
    if voiced:
        monkeypatch.setattr(audio_mix, '_narration_tracks', lambda *args: [('voice.wav', 0, 8)])
        monkeypatch.setattr(audio_mix, 'wav_duration', lambda *args: 1)
        def voice(source, tracks, target, **options):
            Path(target).write_bytes(Path(source).read_bytes() + b'|voice')
            return True
        monkeypatch.setattr(audio_mix, 'add_scene_narrations', voice)
    else:
        monkeypatch.setattr(audio_mix, '_narration_tracks', lambda *args: pytest.fail('No TTS should be required'))
    narration = None if mode == 'original' else {'captions_only': not voiced, 'burn_subtitles': captioned}
    snapshot = {'settings': EditingSettings(mode=mode, voice_source='scenes').model_dump(), 'revision': 1,
                'audio_mix': {'narration': narration, 'music': None}, 'output': str(output), 'url': '/new-export',
                'timeline': timeline_key(scenes), 'scene_texts': [s.get('narrator_text') for s in scenes]}
    result = await audio_mix.finish_audio_mix(vid, 'VERTICAL', {}, edit_export=snapshot)
    assert result['url'] == '/new-export'
    assert output.read_bytes() == b'clean source' + (b'|voice' if voiced else b'') + b'|layout' + (b'|captions' if captioned else b'')
    assert original.read_bytes() == b'previous export'
    assert audio_settings.read_bytes() == before
    assert (await crud.get_video(vid))['vertical_url'] == '/previous.mp4'


async def test_changed_scene_rejects_editor_snapshot_before_download(audio_project):
    vid, sid, _, output, _, downloads = audio_project
    from agent.db import crud
    from agent.services.imported_video import timeline
    scenes = await timeline(vid)
    snapshot = {'settings': EditingSettings().model_dump(), 'audio_mix': {},
                'timeline': timeline_key(scenes), 'scene_texts': [s.get('narrator_text') for s in scenes]}
    await crud.update_scene(sid, narrator_text='Changed while waiting')
    with pytest.raises(audio_mix.AudioMixError, match='Cảnh đã thay đổi'):
        await audio_mix.finish_audio_mix(vid, 'VERTICAL', {}, edit_export=snapshot)
    assert not downloads
    assert output.read_bytes() == b'previous export'


async def test_live_preview_uses_raw_source_and_caption_renderer_without_ffmpeg(audio_project, tmp_path, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from agent.api import editing as api
    from agent.models.editing import CaptionStyle
    vid, _, _, _, _, _ = audio_project
    original = tmp_path / 'original.mp4'
    original.write_bytes(b'original source bytes')
    monkeypatch.setattr(api, 'local_source', lambda _: original)
    monkeypatch.setattr(editing, 'ROOT', tmp_path / 'settings')
    monkeypatch.setattr(editing, 'still_preview', lambda *args: pytest.fail('Live text preview must not decode video'))
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    style = CaptionStyle(font_id='serif', position='top', color='#ff0000')
    exact = tmp_path / 'exact.png'
    editing.caption_image('Đường về bình yên', 320, 480, style, exact)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post(f'/api/videos/{vid}/editing/caption-preview', json={
            'style': style.model_dump(), 'width': 320, 'height': 480, 'text': 'Đường về bình yên'})
        assert response.status_code == 200
        assert response.headers['content-type'] == 'image/png'
        assert response.content == exact.read_bytes()
        response = await client.get(f'/api/videos/{vid}/editing/source', headers={'Range': 'bytes=0-7'})
        assert response.status_code == 206 and response.content == b'original'
        for payload in [dict(style={'font_id': '../../secret'}, width=320, height=480, text='Hello'),
                        dict(style={}, width=3840, height=3840, text='Hello')]:
            response = await client.post(f'/api/videos/{vid}/editing/caption-preview', json=payload)
            assert response.status_code == 422
    assert original.read_bytes() == b'original source bytes'
    assert not (tmp_path / 'settings').exists()


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
@pytest.mark.parametrize('kind', ['color', 'image', 'blur', 'delogo'])
def test_region_effect_timing_and_audio_on_real_video(tmp_path, monkeypatch, kind):
    import uuid
    import numpy as np
    from PIL import Image
    monkeypatch.setattr(editing, 'ROOT', tmp_path / 'editing')
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=320x240:r=12:d=2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, capture_output=True)
    original = source.read_bytes()
    region = EditRegion(id=uuid.uuid4(), kind=kind, x=.6, y=.6, width=.3, height=.25, start=1, end=2, color='#ff0000')
    if kind == 'image':
        region.image_id = uuid.uuid4()
        path = editing.asset_path(VID, str(region.image_id))
        path.parent.mkdir(parents=True)
        Image.new('RGBA', (80, 50), 'red').save(path)
    settings = EditingSettings(regions=[region])
    editing.validate_effects(VID, settings, 2)
    target = tmp_path / 'result.mp4'
    editing.transform(source, target, settings, video_id=VID)
    info = editing.probe(target)
    assert info['has_audio'] and abs(info['duration'] - 2) < .1
    assert (info['width'], info['height']) == (320, 240)
    def frame(path, time):
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', str(time), '-i', str(path), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        return np.frombuffer(raw, np.uint8).reshape(240, 320, 3).astype(float)
    before, early = frame(source, .25), frame(target, .25)
    late, transformed = frame(source, 1.25), frame(target, 1.25)
    assert np.abs(early - before).mean() < 5  # Re-encode only; effect not enabled yet.
    roi = np.s_[150:195, 200:270]
    assert np.abs(transformed[roi] - late[roi]).mean() > 8
    if kind in {'color', 'image'}:
        assert transformed[170, 230, 0] > 220 and transformed[170, 230, 1] < 40
    still = tmp_path / 'still.png'
    editing.still_preview(source, still, settings, 1.25, '', video_id=VID)
    assert Image.open(still).size == (320, 240)
    assert source.read_bytes() == original


def test_region_validation_and_color_filter(tmp_path, monkeypatch):
    import uuid
    monkeypatch.setattr(editing, 'ROOT', tmp_path)
    for payload in [dict(x=.9, width=.3), dict(start=2, end=1), dict(color='red;movie=/etc/passwd')]:
        with pytest.raises(ValidationError):
            EditRegion(id=uuid.uuid4(), **payload)
    region = EditRegion(id=uuid.uuid4(), kind='image')
    with pytest.raises(ValueError, match='vượt'):
        editing.validate_effects(VID, EditingSettings(regions=[region]), 2)
    with pytest.raises(ValueError, match='Tải ảnh'):
        editing.validate_effects(VID, EditingSettings(regions=[region]), 3)
    graph, _, _ = editing.processing_filter(EditingSettings(colors=ColorSettings(brightness=.1, contrast=1.2, saturation=.8)), 320, 240)
    assert 'eq=brightness=0.1:contrast=1.2:saturation=0.8' in graph
    with pytest.raises(ValidationError, match='trùng'):
        EditingSettings(regions=[region, region])
    editing.save(VID, 0, EditingSettings(regions=[region]))
    assert editing.load(VID)['settings']['regions'][0]['id'] == str(region.id)
