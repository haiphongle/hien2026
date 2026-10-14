"""End-to-end media checks for timeline audio, overlays, range and handoff."""
from copy import deepcopy
import uuid
import subprocess

import pytest

from agent.models.timeline import TimelineDocument
from agent.services import editing, imported_video, timeline, timeline_layers
from agent.db import crud
from tests.unit.test_timeline import timeline_context, finished, pixels, rms
from tests.unit.test_audio_mix import audio_project


def uid():
    return str(uuid.uuid4())


async def test_caption_export_tolerates_unavailable_ffmpeg_progress(tmp_path, monkeypatch):
    track = uid()
    doc = TimelineDocument(width=160, height=96, tracks=[{'id': uid(), 'name': 'Hình', 'kind': 'video'},
                                                       {'id': track, 'name': 'Chữ', 'kind': 'caption'}],
        clips=[{'id': uid(), 'track_id': track, 'start': 0, 'source_in': 0, 'duration': 1,
                'text': 'Thử phụ đề', 'caption': {'size': 10}}])
    updates = []

    async def process(args, *, on_line, **kwargs):
        for line in ['out_time_us=N/A', 'out_time_us=nan', 'out_time_us=inf', 'progress=continue',
                     'out_time_us=-1000', 'out_time_us=500000', 'out_time_us=1000000']:
            on_line(line)

    monkeypatch.setattr(timeline_layers.media_jobs, 'process', process)
    result = await timeline_layers.caption_movie(doc, tmp_path, 1, lambda **values: updates.append(values))
    assert result == tmp_path / 'captions.mov'
    sync = [u['progress'] for u in updates if u['stage'] == 'Đồng bộ chữ và phụ đề']
    assert sync == [85, 87, 90]
    assert (tmp_path / 'captions.txt').is_file()


async def test_fades_speed_solo_range_and_cached_proxy(timeline_context):
    vid, source, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    state = (await client.get(url)).json()
    doc = deepcopy(state['document'])
    for c in doc['clips']:
        c['speed'] = 2
        c['duration'] = 2
        if c['track_id'] == doc['tracks'][1]['id']:
            c['fade_in'] = .4
            c['fade_out'] = .4
    response = await client.put(url, json={'revision': 0, 'document': doc})
    assert response.status_code == 200, response.text
    response = await client.post(url+'/render', json={'revision': 1})
    assert response.status_code == 202, response.text
    job = await finished(client, url)
    path = editing.directory(vid)/'exports'/f"{job['result']['export_id']}.mp4"
    assert job['result']['duration'] == pytest.approx(2, abs=.06)
    assert rms(path, .04) < rms(path, .3)*.5
    assert rms(path, 1.8) < rms(path, 1.4)*.7
    assert pixels(path, 1.1)[0] > 200 and pixels(path, 1.1)[1] < 70
    response = await client.post(url+'/render', json={'revision': 1, 'start': .2, 'end': 1.4})
    assert response.status_code == 202, response.text
    job = await finished(client, url)
    assert job['result']['duration'] == pytest.approx(1.2, abs=.06)
    doc['tracks'][2]['solo'] = True
    await client.put(url,json={'revision':1,'document':doc})
    await client.post(url+'/render',json={'revision':2})
    job=await finished(client,url)
    assert rms(editing.directory(vid)/'exports'/f"{job['result']['export_id']}.mp4", .2)<.0001
    aid = state['document']['clips'][0]['asset_id']
    response=await client.post(url+'/proxies',json={'asset_ids':[aid]})
    assert response.status_code==202,response.text
    await finished(client,url)
    proxy=timeline.directory(vid)/'proxies'/f'{aid}.mp4'
    assert (await imported_video.probe(proxy))['duration']==pytest.approx(4,abs=.1)
    assert (await client.get(url+f'/assets/{aid}/proxy')).status_code==200
    assert (await client.get(url)).json()['assets'][0]['proxy_url']
    assert source.is_file()


async def test_caption_and_timed_regions_export_and_handoff(timeline_context):
    vid, source, client=timeline_context
    url=f'/api/videos/{vid}/timeline'
    state=(await client.get(url)).json();doc=deepcopy(state['document'])
    caption_track, effect_track=uid(),uid()
    doc['tracks'].extend([dict(id=caption_track,name='Chữ',kind='caption'),dict(id=effect_track,name='Hiệu ứng',kind='effect')])
    doc['clips'].extend([
        dict(id=uid(),track_id=caption_track,start=1,source_in=0,duration=1,text='Xin chào',caption={'size':10,'x':.5,'y':.5}),
        dict(id=uid(),track_id=effect_track,start=2,source_in=0,duration=1,effect={'id':uid(),'kind':'color','x':0,'y':0,'width':.25,'height':.25,'start':0,'end':3,'color':'#0000ff'}),
        dict(id=uid(),track_id=effect_track,start=3,source_in=0,duration=1,colors={'brightness':-.4,'contrast':1,'saturation':1}),
    ])
    response=await client.put(url,json={'revision':0,'document':doc});assert response.status_code==200,response.text
    response=await client.post(url+'/render',json={'revision':1});assert response.status_code==202,response.text
    job=await finished(client,url);eid=job['result']['export_id'];path=editing.directory(vid)/'exports'/f'{eid}.mp4'
    assert min(pixels(path,.4))>200
    assert pixels(path,2.4)[2]>180 and pixels(path,2.4)[0]<50
    assert max(pixels(path,3.4)[1:])<180
    def center(at):
        return subprocess.run(['ffmpeg','-v','error','-ss',str(at),'-i',str(path),'-vf','crop=80:40:40:28','-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'],capture_output=True,check=True).stdout
    assert sum(center(1.4))<sum(center(.4))*.8
    before=source.read_bytes()
    response=await client.post(url+f'/exports/{eid}/handoff');assert response.status_code==200,response.text
    new_id=response.json()['video_id']
    assert new_id!=vid and (await crud.get_video(new_id))['project_id']==(await crud.get_video(vid))['project_id']
    assert (await imported_video.timeline(new_id))[0]['duration']==pytest.approx(4,abs=.1)
    assert imported_video.source_path(new_id).read_bytes()==path.read_bytes()
    assert source.read_bytes()==before
    assert (await client.post(url+f'/exports/{eid}/handoff')).json()['video_id']==new_id
    foreign=uid()
    assert (await client.post(url+f'/exports/{foreign}/handoff')).status_code==422


async def test_ducking_and_imported_subtitles_remap_to_cut_source(timeline_context):
    from agent.models.speech import Cue,SpeechDocument
    from agent.services import speech_store
    vid,_,client=timeline_context;url=f'/api/videos/{vid}/timeline'
    state=(await client.get(url)).json();doc=deepcopy(state['document'])
    doc['tracks'][1]['muted']=True
    voice,music=uid(),uid();doc['tracks'].extend([dict(id=voice,name='Giọng',kind='audio',role='voice',muted=False),dict(id=music,name='Nhạc',kind='audio',role='music',muted=False)])
    aid=state['document']['clips'][0]['asset_id']
    doc['clips'].extend([dict(id=uid(),asset_id=aid,track_id=music,start=0,source_in=0,duration=4,gain=.5),dict(id=uid(),asset_id=aid,track_id=voice,start=2,source_in=1,duration=1,gain=1)])
    doc['ducking']={'enabled':True,'amount':.2,'attack':.1,'release':.1}
    await client.put(url,json={'revision':0,'document':doc});await client.post(url+'/render',json={'revision':1})
    job=await finished(client,url);path=editing.directory(vid)/'exports'/f"{job['result']['export_id']}.mp4"
    # The voice source interval is silent; only the music should be ducked.
    assert rms(path,2.4)<rms(path,.4)*.3
    scenes=await imported_video.timeline(vid)
    speech_store.atomic_json(speech_store.video_dir(vid)/'script.json',SpeechDocument(timeline=speech_store.timeline_key(scenes),cues=[Cue(start=1,end=3,text='Một câu để thử')]).model_dump(mode='json'))
    for c in doc['clips'][:2]:
        c.update(start=0,source_in=2,duration=2)
    await client.put(url,json={'revision':1,'document':doc})
    response=await client.post(url+'/import-results',json={'revision':2,'voices':False,'effects':False})
    assert response.status_code==200,response.text
    captions=[c for c in response.json()['document']['clips'] if c['text']]
    assert len(captions)==1 and captions[0]['start']==0 and captions[0]['duration']==1
    assert captions[0]['anchor']==doc['clips'][0]['id']
    await client.put(url,json={'revision':2,'document':response.json()['document']})
    assert (await client.post(url+'/import-results',json={'revision':3,'voices':False,'effects':False})).json()['added']==0
    # New translations update existing blocks without replacing their style.
    current=(await client.get(url)).json()['document']
    block=next(c for c in current['clips'] if c['text'])
    block['caption'].update(x=.3,color='#00ff00')
    await client.put(url,json={'revision':3,'document':current})
    script=speech_store.video_dir(vid)/'script.json'
    import json
    updated=json.loads(script.read_text())
    updated['cues'][0]['text']='Bản dịch đã cập nhật'
    speech_store.atomic_json(script,updated)
    response=await client.post(url+'/import-results',json={'revision':4,'voices':False,'effects':False})
    assert response.status_code==200,response.text
    captions=[c for c in response.json()['document']['clips'] if c['text']]
    assert len(captions)==1 and captions[0]['id']==block['id']
    assert captions[0]['text']=='Bản dịch đã cập nhật'
    assert captions[0]['caption']['x']==.3 and captions[0]['caption']['color']=='#00ff00'


def test_silence_and_range_are_bounded_and_keep_envelopes():
    assert timeline_layers.silence_ranges([.2]*10+[0]*20+[.2]*10,.032)==[{'start':.32,'end':.96}]
    source=dict(id=uid(),fps=30,duration=4,width=160,height=96,has_audio=True)
    document=timeline.initial(source)
    document.clips[1].fade_in=1
    selection=timeline_layers.range_document(document,.5,2)
    assert selection.clips[1].envelope[0].value==.5
    assert selection.clips[1].source_in==.5
    with pytest.raises(ValueError):timeline_layers.range_document(document,1,5)


async def test_logo_blur_and_multiple_audio_batches(timeline_context):
    from PIL import Image
    vid,_,client=timeline_context;url=f'/api/videos/{vid}/timeline'
    state=(await client.get(url)).json();doc=deepcopy(state['document'])
    image_id=uid();path=editing.asset_path(vid,image_id);path.parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGBA',(32,32),'#00ff00').save(path)
    fx=uid();doc['tracks'].append(dict(id=fx,name='Logo',kind='effect'))
    doc['clips'].extend([
        dict(id=uid(),track_id=fx,start=1,duration=1,effect=dict(id=uid(),kind='image',image_id=image_id,x=0,y=0,width=.15,height=.25,start=0,end=3)),
        dict(id=uid(),track_id=fx,start=2,duration=1,effect=dict(id=uid(),kind='blur',x=.3,y=.3,width=.3,height=.3,start=0,end=3)),
    ])
    base=doc['clips'][1]
    doc['clips'].extend({**base,'id':uid(),'group':None,'gain':.01,'start':3,'source_in':0,'duration':1} for _ in range(13))
    response=await client.put(url,json={'revision':0,'document':doc});assert response.status_code==200,response.text
    response=await client.post(url+'/render',json={'revision':1});assert response.status_code==202,response.text
    job=await finished(client,url);output=editing.directory(vid)/'exports'/f"{job['result']['export_id']}.mp4"
    assert pixels(output,1.4)[1]>180 and pixels(output,1.4)[0]<60
    assert rms(output,3.4)>.07  # delayed second batch lands at the proper time
    response=await client.post(url+'/caption-preview',json={'text':'Thử chữ','caption':{'x':.2,'y':.4},'width':160,'height':96})
    assert response.status_code==200 and response.content.startswith(b'\x89PNG')


async def test_output_frame_caption_background_and_blur_share_renderer(timeline_context):
    vid, _, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    doc = (await client.get(url)).json()['document']
    doc['frame'] = {'preset': 'custom', 'width': 240, 'height': 320, 'fit': 'contain', 'color': '#0000ff'}
    track = uid()
    doc['tracks'].append({'id': track, 'kind': 'caption', 'name': 'Chữ'})
    doc['clips'].append({'id': uid(), 'track_id': track, 'start': 1, 'duration': 1, 'text': 'Xin chào', 'caption': {'size': 10, 'x': .5, 'y': .5}})
    response = await client.put(url, json={'revision': 0, 'document': doc})
    assert response.status_code == 200, response.text
    await client.post(url+'/render', json={'revision': 1})
    job = await finished(client, url)
    path = editing.directory(vid)/'exports'/f"{job['result']['export_id']}.mp4"
    assert (job['result']['width'], job['result']['height']) == (240, 320)
    assert pixels(path, .4)[2] > 200 and pixels(path, .4)[0] < 20
    def middle(at):
        return subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path), '-vf', 'crop=150:70:45:125', '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
    assert sum(middle(1.4)) < sum(middle(.4))*.85
    doc['frame']['background'] = 'blur'
    await client.put(url, json={'revision': 1, 'document': doc})
    await client.post(url+'/render', json={'revision': 2, 'start': 0, 'end': 1})
    job = await finished(client, url)
    path = editing.directory(vid)/'exports'/f"{job['result']['export_id']}.mp4"
    assert min(pixels(path, .4)) > 220
    assert job['result']['duration'] == pytest.approx(1, abs=.06)


async def test_legacy_settings_music_and_pacing_migrate_once_with_linked_captions(timeline_context, monkeypatch, tmp_path):
    from agent.models.editing import EditingSettings
    from agent.models.speech import Cue, SpeechDocument
    from agent.services import pacing, speech_store, timeline_bridge
    from agent.utils.slugify import slugify
    vid, source, client = timeline_context
    monkeypatch.setattr(timeline_bridge, 'OUTPUT_DIR', tmp_path)
    # The shared fixture supplies an old mix; start empty for the discovery check.
    (tmp_path/'audio_test'/'audio_mix'/f'{vid}.json').write_text('{}')
    url = f'/api/videos/{vid}/timeline'
    state = (await client.get(url)).json()
    assert not state['legacy_available']
    settings = EditingSettings.model_validate({'frame': {'preset': 'custom', 'width': 240, 'height': 320}, 'mode': 'subtitles', 'source_volume': .2, 'captions': {'color': '#00ff00', 'size': 5}})
    editing.save(vid, 0, settings)
    scenes = await imported_video.timeline(vid)
    speech_store.atomic_json(speech_store.video_dir(vid)/'script.json', SpeechDocument(timeline=speech_store.timeline_key(scenes), cues=[Cue(start=0, end=4, text='Phụ đề cũ')]).model_dump(mode='json'))
    project = await crud.get_project((await crud.get_video(vid))['project_id'])
    directory = tmp_path/slugify(project['name'])
    music = directory/'music'/'legacy.wav'
    music.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=300:duration=4', str(music)], check=True, capture_output=True)
    speech_store.atomic_json(directory/'audio_mix'/f'{vid}.json', {'music': {'filename': music.name, 'volume': .15}})
    sound = pacing.library(vid)[0]
    sfx = await timeline.register(vid, vid, f"sfx:{sound['id']}", sound['name'], pacing.asset_path(vid, sound['id']), 'sfx')
    current = state['document']
    current['clips'].append({'id': uid(), 'asset_id': sfx['id'], 'track_id': current['tracks'][2]['id'], 'start': 0, 'duration': .3, 'gain': .45})
    assert (await client.put(url, json={'revision': 0, 'document': current})).status_code == 200
    plan_id, cut_id = uid(), uid()
    speech_store.atomic_json(pacing.directory(vid)/'plans'/f'{plan_id}.json', {'source': 'original', 'fingerprint': pacing.fingerprint(source), 'cuts': [{'id': cut_id, 'start': 1, 'end': 2}]})
    speech_store.atomic_json(pacing.directory(vid)/'draft.json', {'revision': 1, 'draft': {'plan_id': plan_id, 'cut_ids': [cut_id], 'effects': [{'id': uid(), 'asset_id': sound['id'], 'start': .5, 'volume': .4}]}})
    assert (await client.get(url)).json()['legacy_available']
    response = await client.post(url+'/import-results', json={'revision': 1, 'legacy': True, 'voices': False})
    assert response.status_code == 200, response.text
    result = response.json()
    assert not result['warnings'], result['warnings']
    doc = result['document']
    assert doc['legacy_imported'] and doc['frame']['width'] == 240
    picture = sorted([c for c in doc['clips'] if c['track_id'] == doc['tracks'][0]['id']], key=lambda c: c['start'])
    assert [(c['start'], c['source_in'], c['duration']) for c in picture] == [(0, 0, 1), (1, 2, 2)]
    assert len({c['id'] for c in doc['clips']}) == len(doc['clips'])
    captions = [c for c in doc['clips'] if c['text']]
    assert len(captions) == 2 and all(c['caption']['color'] == '#00ff00' for c in captions)
    assert {c['anchor'] for c in captions} == {c['id'] for c in picture}
    assert all(c['gain'] == .2 for c in doc['clips'] if c['track_id'] == doc['tracks'][1]['id'])
    assert next(c for c in doc['clips'] if c['track_id'] == doc['tracks'][2]['id'])['gain'] == .45
    assert next(c for c in doc['clips'] if c['origin'].startswith('legacy-music'))['duration'] == 3
    assert next(c for c in doc['clips'] if c['origin'].startswith('legacy-sfx'))['start'] == .5
    response = await client.put(url, json={'revision': 1, 'document': doc})
    assert response.status_code == 200, response.text
    assert (await client.post(url+'/import-results', json={'revision': 2, 'legacy': True})).status_code == 422
    # Import is an undoable draft change; the old settings and source remain.
    assert editing.load(vid)['settings'] == settings.model_dump(mode='json')
    assert source.is_file()


async def test_reimport_speech_updates_caption_timing_without_losing_style(timeline_context):
    from agent.models.speech import Cue, SpeechDocument
    from agent.services import speech_store
    vid, _, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    scenes = await imported_video.timeline(vid)
    script_path = speech_store.video_dir(vid) / 'script.json'
    cue = Cue(start=0, end=2, text='First line')
    speech_store.atomic_json(script_path, SpeechDocument(timeline=speech_store.timeline_key(scenes), cues=[cue]).model_dump(mode='json'))
    initial = (await client.get(url)).json()['document']
    assert (await client.put(url, json={'revision': 0, 'document': initial})).status_code == 200
    first = await client.post(url + '/import-results', json={'revision': 1, 'source': 'speech', 'voices': False})
    assert first.status_code == 200, first.text
    doc = first.json()['document']
    caption = next(c for c in doc['clips'] if c['origin'].startswith('caption:'))
    caption['caption'] = {'color': '#ff0000'}
    assert (await client.put(url, json={'revision': 1, 'document': doc})).status_code == 200

    cue.start, cue.end, cue.text = .5, 3.5, 'Updated line'
    speech_store.atomic_json(script_path, SpeechDocument(timeline=speech_store.timeline_key(scenes), cues=[cue]).model_dump(mode='json'))
    updated = await client.post(url + '/import-results', json={'revision': 2, 'source': 'speech', 'voices': False})
    assert updated.status_code == 200, updated.text
    captions = [c for c in updated.json()['document']['clips'] if c['origin'].startswith('caption:')]
    assert len(captions) == 1
    assert (captions[0]['id'], captions[0]['text'], captions[0]['start'], captions[0]['duration']) == (
        caption['id'], 'Updated line', .5, 3)
    assert captions[0]['caption']['color'] == '#ff0000'


async def test_explicit_ai_source_replaces_old_layers_and_drops_stale_voice(timeline_context):
    from agent.models.speech import Cue, SpeechDocument
    from agent.services import speech_store
    vid, _, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    state = (await client.get(url)).json(); doc = state['document']
    scenes = await imported_video.timeline(vid)
    cue = Cue(start=0, end=2, text='Một lời đọc chưa tạo')
    speech_store.atomic_json(speech_store.video_dir(vid)/'script.json', SpeechDocument(timeline=speech_store.timeline_key(scenes), cues=[cue]).model_dump(mode='json'))
    tracks = uid(), uid()
    doc['tracks'].extend([{'id': tracks[0], 'name': 'Phụ đề', 'kind': 'caption'}, {'id': tracks[1], 'name': 'Giọng', 'kind': 'audio', 'role': 'voice'}])
    picture = doc['clips'][0]
    doc['clips'].extend([
        {'id': uid(), 'track_id': tracks[0], 'start': 0, 'duration': 1, 'text': 'Câu cũ đã xóa', 'origin': f"caption:{picture['id']}:{uid()}"},
        {'id': uid(), 'track_id': tracks[1], 'asset_id': picture['asset_id'], 'start': 0, 'duration': 1, 'origin': f"voice:{picture['id']}:{cue.id}"},
        {'id': uid(), 'track_id': tracks[0], 'start': 2, 'duration': 1, 'text': 'Chữ thêm thủ công'},
    ])
    await client.put(url, json={'revision': 0, 'document': doc})
    response = await client.post(url+'/import-results', json={'revision': 1, 'source': 'speech'})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['warnings']  # Audio is not ready, never retain the old voice.
    assert not any(c['origin'].startswith('voice:') for c in result['document']['clips'])
    assert sorted(c['text'] for c in result['document']['clips'] if c['text']) == ['Chữ thêm thủ công', cue.text]
    # Scene narration explicitly uses its own text even when speech is saved.
    real_scenes = await crud.list_scenes(vid)
    await crud.update_scene(real_scenes[0]['id'], narrator_text='Lời thuyết minh theo cảnh')
    response = await client.post(url+'/import-results', json={'revision': 1, 'source': 'scenes', 'voices': False})
    assert response.status_code == 200, response.text
    assert 'Lời thuyết minh theo cảnh' in [c['text'] for c in response.json()['document']['clips']]
    assert cue.text not in [c['text'] for c in response.json()['document']['clips']]
