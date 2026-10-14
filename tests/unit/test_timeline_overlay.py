"""Real PiP exports: timed picture, linked voice, hidden frame and independent gain."""
from copy import deepcopy
import subprocess

import pytest
from pydantic import ValidationError

from agent.models.timeline import TimelineDocument, VideoOverlay
from agent.services import timeline, timeline_layers, pacing
from tests.unit.test_timeline import timeline_context, uid, rms
from tests.unit.test_audio_mix import audio_project


def pixel(path, at, x, y):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path),
        '-vf', f'crop=2:2:{x}:{y}', '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'],
        check=True, capture_output=True).stdout
    return list(raw[:3])


async def setup(timeline_context):
    vid, source, client = timeline_context
    state = (await client.get(f'/api/videos/{vid}/timeline')).json()
    second = source.parent / 'narrator.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=blue:s=96x96:r=30:d=4',
        '-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=4',
        '-vf', "drawbox=x=0:y=0:w=48:h=96:color=red:t=fill", '-t', '4',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(second)], check=True, capture_output=True)
    asset = await timeline.register(vid, vid, 'test-narrator', 'Narrator', second, 'source')
    doc = state['document']
    video_track, audio_track, group = uid(), uid(), uid()
    doc['tracks'] += [dict(id=video_track,name='Video chèn',kind='overlay'),dict(id=audio_track,name='Tiếng video chèn',kind='audio',role='voice',purpose='overlay')]
    base = dict(asset_id=asset['id'],start=1,source_in=.5,duration=2,speed=1.5,gain=.4,group=group)
    doc['clips'] += [dict(**base,id=uid(),track_id=video_track,overlay=dict(x=.5,y=.5,width=.5,height=.5,fit='cover',border=2,border_color='#00ff00')),
                     dict(**base,id=uid(),track_id=audio_track)]
    return vid, source, doc, client


async def test_pip_export_timing_border_flip_audio_and_saved_roundtrip(timeline_context, tmp_path):
    vid, source, doc, client = await setup(timeline_context)
    before = source.read_bytes()
    # Main soundtrack is muted independently, narrator remains audible only in its clip.
    doc['clips'][1]['muted'] = True
    url = f'/api/videos/{vid}/timeline'
    response = await client.put(url,json={'revision':0,'document':doc})
    assert response.status_code == 200, response.text
    saved = (await client.get(url)).json()['document']
    assert saved['clips'][-2]['overlay']['border_color'] == '#00ff00'
    output = tmp_path/'pip.mp4'
    await timeline.render(vid,TimelineDocument.model_validate(saved),output,lambda **kw:None)
    assert (await pacing.probe(output))['duration'] == pytest.approx(4,abs=.08)
    assert min(pixel(output,.5,90,65)) > 220
    red, green, blue = pixel(output,1.5,90,65)
    assert red > 200 and blue < 40
    assert pixel(output,1.5,150,65)[2] > 200
    assert pixel(output,1.5,100,48)[1] > 150, 'green frame is inside the overlay'
    assert min(pixel(output,3.5,90,65)) > 220, 'overlay must disappear at its end'
    assert rms(output,.5) < .002 and rms(output,1.5) > .015 and rms(output,3.5) < .002
    flipped = deepcopy(saved); flipped['clips'][-2]['overlay']['flip'] = True
    flipped['clips'][-1]['gain'] = .1
    second = tmp_path/'flipped.mp4'
    await timeline.render(vid,TimelineDocument.model_validate(flipped),second,lambda **kw:None)
    assert pixel(second,1.5,90,65)[2] > 200
    assert rms(second,1.5)/rms(output,1.5) == pytest.approx(.25,abs=.05)
    assert source.read_bytes() == before


async def test_pip_hidden_keeps_voice_and_range_split_does_not_truncate_movie(timeline_context,tmp_path):
    vid, _, doc, _ = await setup(timeline_context)
    doc['clips'][1]['muted'] = True
    doc['clips'][-2]['overlay']['hidden'] = True
    output = tmp_path/'hidden.mp4'
    await timeline.render(vid,TimelineDocument.model_validate(doc),output,lambda **kw:None)
    assert min(pixel(output,1.5,90,65)) > 220
    assert rms(output,1.5) > .015
    doc['clips'][-2]['overlay'].update(hidden=False,layout='split',fit='contain',border=0)
    doc['clips'][-1]['muted'] = True
    full = TimelineDocument.model_validate(doc)
    selected = timeline_layers.range_document(full,1.5,3.5)
    second = tmp_path/'range.mp4'
    await timeline.render(vid,selected,second,lambda **kw:None,source_document=full,range_start=1.5)
    assert (await pacing.probe(second))['duration'] == pytest.approx(2,abs=.08)
    assert min(pixel(second,.25,40,40)) > 220, 'main movie remains visible and fills the left pane vertically'
    assert pixel(second,.25,90,40)[0] > 200 and pixel(second,.25,150,40)[2] > 200
    assert max(pixel(second,.25,100,4)) < 20, 'contain preserves whole square with black bars'
    assert min(pixel(second,1.8,90,65)) > 220, 'full-screen main resumes after PiP'
    assert rms(second,.25) < .002, 'both clips independently muted'


def test_overlay_schema_rejects_offscreen_bounds_and_wrong_layer():
    with pytest.raises(ValidationError):
        VideoOverlay(x=.9,width=.3)
    track, audio = uid(), uid()
    base = dict(version=1,tracks=[dict(id=track,name='main',kind='video'),dict(id=audio,name='audio',kind='audio')],
                clips=[dict(id=uid(),track_id=track,asset_id=uid(),start=0,duration=1,overlay={})])
    with pytest.raises(ValidationError,match='video chèn'):
        TimelineDocument.model_validate(base)


async def test_importing_ai_voice_and_legacy_mix_preserves_pip_audio_controls(timeline_context, monkeypatch, tmp_path):
    from agent.models.editing import EditingSettings
    from agent.models.speech import Cue, SpeechDocument
    from agent.services import editing, imported_video, speech, speech_store, timeline_bridge
    vid, source, doc, _ = await setup(timeline_context)
    scenes = await imported_video.timeline(vid)
    cue = Cue(start=0, end=4, text='Một lời đọc kiểm thử')
    speech_store.atomic_json(speech_store.video_dir(vid)/'script.json', SpeechDocument(
        timeline=speech_store.timeline_key(scenes), cues=[cue]).model_dump(mode='json'))
    monkeypatch.setattr(speech, 'cue_status', lambda *args: {'status': 'READY'})
    monkeypatch.setattr(speech, 'cue_audio', lambda *args: (source, None, None, None))
    result, *_ = await timeline_bridge.import_results(vid, TimelineDocument.model_validate(doc), effects=False)
    result = TimelineDocument.model_validate(result)
    pip = next(c for c in result.clips if str(c.id) == doc['clips'][-1]['id'])
    generated = next(c for c in result.clips if c.origin.startswith('voice:'))
    assert generated.track_id != pip.track_id
    editing.save(vid, 0, EditingSettings(mode='subtitles', narration_volume=.1))
    async def empty_mix(_):
        return tmp_path, {}
    monkeypatch.setattr(timeline_bridge, 'legacy_audio', empty_mix)
    migrated, *_ = await timeline_bridge.import_results(vid, result, voices=False, effects=False, legacy=True)
    migrated = TimelineDocument.model_validate(migrated)
    assert not next(t for t in migrated.tracks if t.id == pip.track_id).muted
    assert next(c for c in migrated.clips if c.id == pip.id).gain == .4


async def test_portrait_split_matches_preview_geometry_and_keeps_source_clock(timeline_context, tmp_path):
    vid, _, doc, _ = await setup(timeline_context)
    portrait = tmp_path/'portrait.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=c=red:s=96x160:r=30:d=4',
        '-f','lavfi','-i','anullsrc=r=48000:cl=stereo','-vf',"drawbox=x=0:y=0:w=iw:h=ih:color=blue:t=fill:enable='gte(t,2)'",
        '-t','4','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(portrait)],check=True,capture_output=True)
    asset = await timeline.register(vid,vid,'portrait-test','Portrait',portrait,'source')
    for clip in doc['clips'][:2]:
        clip['asset_id'] = asset['id']
    doc['frame'] = dict(preset='custom',width=640,height=360,fit='contain',color='#000000')
    doc['clips'][-2]['overlay'].update(layout='split',border=0)
    output = tmp_path/'portrait-split.mp4'
    await timeline.render(vid,TimelineDocument.model_validate(doc),output,lambda **kw:None)
    # Canvas frameGeometry uses [52,0,216,360] in the left 320x360 pane.
    assert pixel(output,1.5,160,14)[0] > 220
    assert max(pixel(output,1.5,24,14)) < 20
    assert max(pixel(output,.5,160,14)) < 20
    assert pixel(output,2.5,160,14)[2] > 220, 'split boundary must not restart the source'
    assert max(pixel(output,3.5,160,14)) < 20, 'full frame resumes at the PiP end'


@pytest.mark.parametrize('shape', ['circle', 'rounded'])
async def test_shaped_frame_border_transparency_and_entrance_survive_range_export(timeline_context,tmp_path,shape):
    vid, _, doc, _ = await setup(timeline_context)
    pip = doc['clips'][-2]
    pip['overlay'].update(shape=shape,radius=25,entrance=.8)
    full = TimelineDocument.model_validate(doc)
    output = tmp_path/f'{shape}.mp4'
    await timeline.render(vid,full,output,lambda **kw:None)
    assert min(pixel(output,1.9,82,50)) > 220, 'corners must show the main image, not black'
    assert pixel(output,1.9,110,68)[0] > 220
    assert pixel(output,1.9,110,68)[1] < 35
    border_x = 120 if shape == 'circle' else 110
    assert pixel(output,1.9,border_x,48)[1] > 160, 'border must follow the shape'
    assert pixel(output,1.2,110,68)[1] > 140, 'entrance alpha should reveal the main image'
    selected = timeline_layers.range_document(full,1.4,2.4)
    assert selected.clips[-2].overlay.entrance_offset == pytest.approx(.4)
    cropped = tmp_path/f'{shape}-range.mp4'
    await timeline.render(vid,selected,cropped,lambda **kw:None,source_document=full,range_start=1.4)
    assert pixel(cropped,.1,110,68) == pytest.approx(pixel(output,1.5,110,68),abs=15), 'range export must retain entrance progress'
    assert (await pacing.probe(cropped))['duration'] == pytest.approx(1,abs=.05)


async def test_review_ducking_lowers_main_and_music_only_when_voice_has_sound(timeline_context,tmp_path):
    vid, _, doc, _ = await setup(timeline_context)
    narrator_asset = doc['clips'][-1]['asset_id']
    source_asset = doc['clips'][0]['asset_id']
    # Steady original sound + almost inaudible narration with a real 1–2s pause.
    doc['clips'][1].update(asset_id=narrator_asset,group=None,gain=.4)
    doc['clips'][0]['group'] = None
    doc['clips'][-1].update(asset_id=source_asset,group=None,start=0,source_in=0,duration=4,speed=1,gain=.001)
    doc['clips'][-2]['group'] = None
    doc['clips'][-2]['overlay']['hidden'] = True
    doc['ducking'] = dict(enabled=True,amount=.25,original_amount=.2,attack=.1,release=.1,detect_pauses=True)
    output = tmp_path/'duck-main.mp4'
    await timeline.render(vid,TimelineDocument.model_validate(doc),output,lambda **kw:None)
    assert rms(output,.5)/rms(output,1.5) == pytest.approx(.2,abs=.035)
    assert rms(output,2.5)/rms(output,1.5) == pytest.approx(.2,abs=.035)
    audio_track = doc['clips'][1]['track_id']
    next(t for t in doc['tracks'] if t['id']==audio_track)['role'] = 'music'
    second = tmp_path/'duck-music.mp4'
    await timeline.render(vid,TimelineDocument.model_validate(doc),second,lambda **kw:None)
    assert rms(second,.5)/rms(second,1.5) == pytest.approx(.25,abs=.035)
    # Muting the narrator must restore the other source to its chosen gain.
    doc['clips'][-1]['muted'] = True
    third = tmp_path/'duck-muted.mp4'
    await timeline.render(vid,TimelineDocument.model_validate(doc),third,lambda **kw:None)
    assert rms(third,.5)/rms(third,1.5) == pytest.approx(1,abs=.035)


async def test_shaped_pip_with_logo_and_captions_keeps_input_streams_separate(timeline_context,tmp_path):
    from PIL import Image
    from agent.services import editing
    vid, _, doc, client = await setup(timeline_context)
    doc['clips'][-2]['overlay'].update(shape='rounded',radius=25,entrance=.3)
    caption_track, effect_track, image_id = uid(), uid(), uid()
    logo = editing.asset_path(vid,image_id)
    logo.parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGBA',(20,20),'blue').save(logo)
    doc['tracks'] += [dict(id=caption_track,name='Chữ',kind='caption'),dict(id=effect_track,name='Logo',kind='effect')]
    doc['clips'] += [
        dict(id=uid(),track_id=caption_track,start=1,duration=1,text='Review',caption={'size':10,'x':.3,'y':.5}),
        dict(id=uid(),track_id=effect_track,start=1,duration=1,effect={'id':uid(),'kind':'image','image_id':image_id,
             'x':0,'y':0,'width':.25,'height':.25,'start':0,'end':3}),
    ]
    response = await client.put(f'/api/videos/{vid}/timeline',json={'revision':0,'document':doc})
    assert response.status_code == 200, response.text
    saved = response.json()['document']
    assert next(c for c in saved['clips'] if c.get('overlay'))['overlay']['entrance'] == .3
    output = tmp_path/'pip-caption-logo.mp4'
    await timeline.render(vid,TimelineDocument.model_validate(saved),output,lambda **kw:None)
    assert pixel(output,1.5,20,10)[2] > 220 and pixel(output,1.5,20,10)[0] < 35
    assert pixel(output,1.5,110,68)[0] > 220 and pixel(output,1.5,110,68)[1] < 35
    crop = subprocess.run(['ffmpeg','-v','error','-ss','1.5','-i',str(output),'-vf','crop=70:30:14:32',
        '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'],check=True,capture_output=True).stdout
    assert min(crop) < 50, 'caption must remain visible above the main image'
    assert pixel(output,2.5,20,10)[0] > 220 and pixel(output,2.5,20,10)[2] < 35, 'logo ends and reveals the red source frame'
