"""Titles use the persisted timeline and the same raster in preview/export."""
from copy import deepcopy
from io import BytesIO
import json
import subprocess
import uuid

from PIL import Image, ImageChops, ImageStat
import pytest

from agent.models.timeline import TimelineCaption
from agent.services import editing, speech_store, timeline_layers
from tests.unit.test_timeline import timeline_context, finished
from tests.unit.test_audio_mix import audio_project


def test_multiline_text_bounds_match_alpha_pixels_and_large_fonts():
    style = TimelineCaption(size=18, background_opacity=0, x=1, y=0)
    one, two = BytesIO(), BytesIO()
    b1 = timeline_layers.caption_image('Một ngày', 640, 360, style, one)
    b2 = timeline_layers.caption_image('Một ngày\nBình yên', 640, 360, style, two)
    assert b2['height'] > b1['height'] * 1.5
    assert b2['x'] + b2['width'] == pytest.approx(1)
    assert b2['y'] == 0
    image = Image.open(BytesIO(two.getvalue()))
    x, y, right, bottom = image.getbbox()
    assert b2 == {'x': x / 640, 'y': y / 360, 'width': (right-x)/640, 'height': (bottom-y)/360}


async def test_title_saved_preview_export_timing_mute_and_subtitle_isolation(timeline_context):
    vid, source, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    initial = (await client.get(url)).json()
    doc = deepcopy(initial['document'])
    track, title_id = str(uuid.uuid4()), str(uuid.uuid4())
    doc['tracks'].append({'id': track, 'kind': 'text', 'name': 'Chữ & tiêu đề'})
    title = {'id': title_id, 'track_id': track, 'start': 1, 'duration': 1,
             'text': 'PHÚC\nGiới thiệu', 'caption': {'size': 18, 'x': .65, 'y': .4, 'background_opacity': 1,
                 'background_color': '#003030', 'color': '#00ffcc', 'font_id': 'sans-bold'}}
    doc['clips'].append(title)
    saved = await client.put(url, json={'revision': 0, 'document': doc})
    assert saved.status_code == 200, saved.text
    loaded = (await client.get(url)).json()
    assert loaded['document']['clips'][-1]['text'] == title['text']
    assert loaded['document']['clips'][-1]['caption']['size'] == 18
    preview = await client.post(url+'/caption-preview', json={'text': title['text'], 'caption': title['caption'], 'width': 160, 'height': 96})
    assert preview.status_code == 200, preview.text
    bounds = json.loads(preview.headers['X-Text-Bounds'])
    assert bounds['width'] > 0 and bounds['height'] > 0
    overlay = Image.open(BytesIO(preview.content)).convert('RGBA')
    # The font gallery uses exactly the installed fonts advertised to the editor.
    for font in initial['fonts']:
        response = await client.get(url+f"/fonts/{font['id']}/preview")
        assert response.status_code == 200
        assert Image.open(BytesIO(response.content)).getbbox()
    assert (await client.get(url+'/fonts/missing-font/preview')).status_code == 422
    response = await client.post(url+'/render', json={'revision': 1})
    assert response.status_code == 202, response.text
    job = await finished(client, url)
    output = editing.directory(vid)/'exports'/f"{job['result']['export_id']}.mp4"
    def frame(at):
        data = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(output), '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'], capture_output=True, check=True).stdout
        return Image.open(BytesIO(data)).convert('RGB')
    plain = frame(.5)
    assert min(ImageStat.Stat(plain).mean) > 245
    expected = Image.alpha_composite(plain.convert('RGBA'), overlay).convert('RGB')
    assert max(ImageStat.Stat(ImageChops.difference(frame(1.5), expected)).mean) < 12
    assert min(frame(2.5).getpixel((80, 48))) > 245  # Title stops at 2s.
    # Synchronizing an AI subtitle must preserve the hand-written title.
    from agent.models.speech import Cue, SpeechDocument
    from agent.services import imported_video
    scenes = await imported_video.timeline(vid)
    speech_store.atomic_json(speech_store.video_dir(vid)/'script.json', SpeechDocument(
        timeline=speech_store.timeline_key(scenes), cues=[Cue(start=0, end=1, text='Phụ đề AI')]).model_dump(mode='json'))
    imported = await client.post(url+'/import-results', json={'revision': 1, 'captions': True, 'voices': False, 'effects': False, 'source': 'speech'})
    assert imported.status_code == 200, imported.text
    assert next(c for c in imported.json()['document']['clips'] if c['id'] == title_id) == loaded['document']['clips'][-1]
    doc['tracks'][-1]['muted'] = True
    assert (await client.put(url, json={'revision': 1, 'document': doc})).status_code == 200
    assert (await client.post(url+'/render', json={'revision': 2, 'start': 1, 'end': 2})).status_code == 202
    job = await finished(client, url)
    output = editing.directory(vid)/'exports'/f"{job['result']['export_id']}.mp4"
    assert min(ImageStat.Stat(frame(.5)).mean) > 245
    assert source.is_file()
