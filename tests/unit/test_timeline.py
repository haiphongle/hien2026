"""Frame-aligned AV reorder/gaps, real waveform cache, isolation and cancellation."""
import asyncio
import array
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import uuid

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from agent.api import music as music_api, pacing as pacing_api, timeline as api
from agent.models.timeline import TimelineDocument
from agent.services import editing, media_jobs, media_library, pacing, speech_store, timeline
from tests.unit.test_audio_mix import audio_project


def uid():
    return str(uuid.uuid4())


def test_ffmpeg_progress_ignores_startup_na_value():
    assert timeline._out_time_seconds('out_time_us=N/A\n') is None
    assert timeline._out_time_seconds('out_time_us=2500000\n') == pytest.approx(2.5)
    assert timeline._out_time_seconds('progress=end\n') is None
    assert timeline._out_time_seconds('out_time_us=nan\n') is None
    assert timeline._out_time_seconds('out_time_us=inf\n') is None


@pytest.fixture
async def timeline_context(audio_project, tmp_path, monkeypatch):
    vid = audio_project[0]
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=160x96:r=30:d=4',
        '-f', 'lavfi', '-i', r'aevalsrc=if(between(t\,1\,2)\,0\,0.12*sin(2*PI*440*t)):s=48000:d=4',
        '-vf', "drawbox=x=0:y=0:w=30:h=30:color=red:t=fill:enable='gte(t,2)'", '-t', '4',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, capture_output=True)
    monkeypatch.setattr(timeline, 'ROOT', tmp_path / 'timeline')
    monkeypatch.setattr(timeline, '_probe_slots', asyncio.Semaphore(2))
    monkeypatch.setattr(pacing, 'ROOT', tmp_path / 'pacing')
    monkeypatch.setattr(editing, 'ROOT', tmp_path / 'editing')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path / 'jobs')
    monkeypatch.setattr(speech_store, 'ROOT', tmp_path / 'speech')
    monkeypatch.setattr(api, 'original_path', lambda _: source)
    monkeypatch.setattr(api, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(media_library, 'ROOT', tmp_path / 'library')
    monkeypatch.setattr(media_library, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(pacing_api, 'local_source', lambda _: source)
    app = FastAPI(); app.include_router(api.router, prefix='/api')
    app.include_router(pacing_api.router, prefix='/api'); app.include_router(music_api.router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield vid, source, client
    await media_jobs.shutdown()


async def finished(client, url):
    await asyncio.gather(*list(media_jobs._tasks.values()))
    job = (await client.get(url + '/job')).json()
    assert job['status'] == 'COMPLETED', job
    return job


def segments(doc, definitions):
    base = doc['clips'][0]
    result = deepcopy(doc); result['clips'] = []
    for start, source_in, duration in definitions:
        group = uid()
        for track in doc['tracks'][:2]:
            result['clips'].append({**base, 'id': uid(), 'track_id': track['id'], 'group': group,
                                   'start': start, 'source_in': source_in, 'duration': duration})
    return result


def pixels(path, at):
    return subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path), '-vf', 'crop=2:2:4:4',
                           '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'], check=True, capture_output=True).stdout


def rms(path, at):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(path), '-t', '0.1', '-map', '0:a',
                          '-ac', '1', '-f', 'f32le', '-'], check=True, capture_output=True).stdout
    values = array.array('f', raw)
    return (sum(v*v for v in values) / len(values)) ** .5


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_reorder_render_keeps_picture_sound_source_and_revision(timeline_context):
    vid, source, client = timeline_context
    before = source.read_bytes()
    url = f'/api/videos/{vid}/timeline'
    state = (await client.get(url)).json()
    assert len(state['document']['clips']) == 2 and len(state['assets']) >= 5
    aid = state['document']['clips'][0]['asset_id']
    assert (await client.get(url + f'/assets/{aid}/file')).content == before
    assert 'path' not in state['assets'][0]
    doc = segments(state['document'], [(0, 2, 2), (2, 0, 2)])
    result = await client.put(url, json={'revision': 0, 'document': doc})
    assert result.status_code == 200, result.text
    assert (await client.put(url, json={'revision': 0, 'document': doc})).status_code == 409
    assert (await client.get(url)).json()['document'] == doc
    result = await client.post(url + '/render', json={'revision': 1})
    assert result.status_code == 202, result.text
    # Drafts remain editable during export; the job renders its saved snapshot.
    changed = segments(doc, [(0, 0, 4)])
    assert (await client.put(url, json={'revision': 1, 'document': changed})).status_code == 200
    job = await finished(client, url)
    exported = editing.directory(vid) / 'exports' / f"{job['result']['export_id']}.mp4"
    assert job['result']['duration'] == pytest.approx(4, abs=.05)
    assert pixels(exported, .5)[0] > 200 and pixels(exported, .5)[1] < 60
    assert min(pixels(exported, 2.5)) > 200
    assert rms(exported, .5) > .05 and rms(exported, 3.4) < .002
    assert source.read_bytes() == before
    saved = editing.load(vid)['exports'][0]
    assert saved['operation'] == 'timeline' and saved['timeline'] == doc and saved['revision'] == 1
    assert (await client.get(url)).json()['revision'] == 2


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_gap_mute_overlay_volume_and_preview(timeline_context):
    vid, _, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    state = (await client.get(url)).json()
    doc = segments(state['document'], [(0, 0, 1), (2, 2, 2)])
    for clip in doc['clips']:
        if clip['track_id'] == doc['tracks'][1]['id']:
            clip['gain'] = .2
    overlay = next(a for a in state['assets'] if a['category'] == 'sfx' and a['duration'] >= .7)
    span = int(overlay['duration'] * 30) / 30
    doc['clips'].append(dict(id=uid(), asset_id=overlay['id'], track_id=doc['tracks'][2]['id'],
                            start=1, source_in=0, duration=span, gain=.6, group=None))
    res = await client.put(url, json={'revision': 0, 'document': doc})
    assert res.status_code == 200, res.text
    await client.post(url + '/render', json={'revision': 1, 'preview': True})
    job = await finished(client, url)
    assert editing.load(vid)['exports'] == []
    assert (await client.get(job['result']['url'])).status_code == 200
    output = timeline.directory(vid) / 'previews' / f"{job['result']['export_id']}.mp4"
    assert max(pixels(output, 1.2)) < 10
    assert rms(output, 1.1) > .02 and rms(output, .1) < .03
    doc['tracks'][1]['muted'] = True; doc['tracks'][2]['muted'] = True
    assert (await client.put(url, json={'revision': 1, 'document': doc})).status_code == 200
    await client.post(url + '/render', json={'revision': 2, 'preview': True})
    job = await finished(client, url)
    output = timeline.directory(vid) / 'previews' / f"{job['result']['export_id']}.mp4"
    assert rms(output, .1) < .0001


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_real_waveform_sprite_cache_and_replaced_asset(timeline_context, monkeypatch):
    vid, source, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    state = (await client.get(url)).json(); aid = state['document']['clips'][0]['asset_id']
    assert (await client.get(url + f'/assets/{aid}/visuals')).json() is None
    await client.post(url + '/visuals', json={'asset_ids': [aid]})
    await finished(client, url)
    summary = (await client.get(url + f'/assets/{aid}/visuals')).json()
    assert len(summary['peaks']) == pytest.approx(125, abs=2)
    assert max(summary['peaks'][36:58]) < .005
    assert max(summary['peaks'][:25]) > .1
    assert (await client.get(summary['thumbnail_url'])).content[:2] == b'\xff\xd8'
    actual = media_jobs.process
    async def forbid(args, **kwargs):
        if args[0] == 'ffmpeg':
            raise AssertionError('A cached summary must not decode the source again')
        return await actual(args, **kwargs)
    monkeypatch.setattr(media_jobs, 'process', forbid)
    await client.post(url + '/visuals', json={'asset_ids': [aid]})
    await finished(client, url)
    source.write_bytes(source.read_bytes() + b'changed')
    assert (await client.post(url + '/render', json={'revision': 0})).status_code == 422
    assert (await client.get(url + f'/assets/{aid}/file')).status_code == 422
    state = (await client.get(url)).json()
    assert next(a for a in state['assets'] if a['id'] == aid)['missing']
    assert state['document']['clips'][0]['asset_id'] == aid


async def test_waveform_analysis_accepts_more_than_eight_assets_in_one_sequential_job(timeline_context, monkeypatch):
    vid, source, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    assets = [await timeline.register(vid, vid, f'voice-{i}', f'Voice {i}', source, 'voice') for i in range(9)]
    ids = [a['id'] for a in assets]
    calls, active = [], 0

    async def visualize(key, aid, update):
        nonlocal active
        active += 1
        assert active == 1
        await asyncio.sleep(0)
        calls.append(aid)
        update(progress=99)
        active -= 1

    monkeypatch.setattr(timeline, 'visualize', visualize)
    response = await client.post(url + '/visuals', json={'asset_ids': ids + ids[:1]})
    assert response.status_code == 202, response.text
    job = await finished(client, url)
    assert calls == ids
    assert job['result']['asset_ids'] == ids
    assert (await client.post(url + '/proxies', json={'asset_ids': ids})).status_code == 422
    assert (await client.post(url + '/visuals', json={'asset_ids': [uid()]})).status_code == 422


@pytest.mark.skipif(not shutil.which('node') or not shutil.which('ffmpeg'), reason='node and ffmpeg required')
async def test_browser_bulk_pause_cuts_save_and_render_with_matching_audio_and_captions(timeline_context):
    vid, source, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    state = (await client.get(url)).json()
    doc = state['document']
    caption_track = uid()
    doc['tracks'].append({'id': caption_track, 'name': 'Phụ đề', 'kind': 'caption'})
    doc['clips'].append({'id': uid(), 'track_id': caption_track, 'start': 0, 'source_in': 0, 'duration': 4,
                         'text': 'Cắt và nối đồng bộ', 'anchor': doc['clips'][0]['id'], 'caption': {'size': 10}})
    module = Path(__file__).resolve().parents[2] / 'dashboard/src/components/projects/postproduction/timeline/operations.ts'
    script = f'''import {{readFileSync}} from 'node:fs';
const {{cutRanges}} = await import({json.dumps(module.as_uri())});
console.log(JSON.stringify(cutRanges(JSON.parse(readFileSync(0,'utf8')), [{{start:.5,end:1}},{{start:2,end:2.5}}])));'''
    result = subprocess.run(['node', '--input-type=module', '-e', script], input=json.dumps(doc), text=True,
                            capture_output=True, check=True)
    cut = json.loads(result.stdout)
    response = await client.put(url, json={'revision': 0, 'document': cut})
    assert response.status_code == 200, response.text
    response = await client.post(url + '/render', json={'revision': 1})
    assert response.status_code == 202, response.text
    job = await finished(client, url)
    exported = editing.directory(vid) / 'exports' / f"{job['result']['export_id']}.mp4"
    assert job['result']['duration'] == pytest.approx(3, abs=.06)
    assert rms(exported, .2) > .05
    assert rms(exported, .9) < .003
    assert rms(exported, 1.8) > .05
    assert pixels(exported, 1.8)[0] > 200 and pixels(exported, 1.8)[1] < 60
    assert (await client.get(url)).json()['document'] == TimelineDocument.model_validate(cut).model_dump(mode='json')


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_cancel_cleanup_and_foreign_project_asset_refused(timeline_context, monkeypatch):
    from agent.db import crud
    vid, source, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    await client.get(url)
    project = await crud.create_project(name='Other', material='realistic')
    other = await crud.create_video(project_id=project['id'], title='Foreign')
    foreign = await timeline.register(vid, other['id'], 'original', 'Foreign', source, 'source')
    assert (await client.get(url + f"/assets/{foreign['id']}/file")).status_code == 422
    entered = asyncio.Event(); actual = media_jobs.process
    async def slow(args, **kwargs):
        if args[0] == 'ffmpeg':
            entered.set()
            return await actual(['ffmpeg', '-v', 'error', '-re', '-f', 'lavfi', '-i', 'testsrc2=duration=60:size=64x64', '-f', 'null', '-'])
        return await actual(args, **kwargs)
    monkeypatch.setattr(media_jobs, 'process', slow)
    res = await client.post(url + '/render', json={'revision': 0})
    assert res.status_code == 202
    await asyncio.wait_for(entered.wait(), 10); await asyncio.sleep(.1)
    assert (await client.post(url + f"/jobs/{res.json()['id']}/cancel")).json()['status'] == 'CANCELLED'
    assert editing.load(vid)['exports'] == []
    assert not list(timeline.directory(vid).glob('render-*'))
    assert not list((editing.directory(vid) / 'exports').glob('*.mp4'))


def test_schema_rejects_unaligned_overlap_and_broken_links():
    source = {'id': uid(), 'fps': 30, 'duration': 4, 'width': 160, 'height': 96, 'has_audio': True}
    doc = timeline.initial(source).model_dump(mode='json')
    broken = deepcopy(doc); broken['clips'][1]['source_in'] = 1
    with pytest.raises(ValidationError, match='liên kết'):
        TimelineDocument.model_validate(broken)
    unaligned = deepcopy(doc); unaligned['clips'][0]['start'] = .001
    with pytest.raises(ValidationError, match='khung hình'):
        TimelineDocument.model_validate(unaligned)
    overlap = segments(doc, [(0, 0, 3), (2, 0, 1)])
    with pytest.raises(ValidationError, match='chồng nhau'):
        TimelineDocument.model_validate(overlap)
    large = timeline.initial({**source, 'width': 7680, 'height': 4320})
    assert (large.width, large.height) == (3840, 2160)
    portrait = timeline.initial({**source, 'width': 2160, 'height': 3840})
    assert (portrait.width, portrait.height) == (2160, 3840)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_uploaded_music_and_effects_remain_in_library_without_selecting_music(timeline_context, audio_project, monkeypatch, tmp_path):
    from agent.db import crud
    vid, _, client = timeline_context
    item = await crud.get_video(vid)
    library = audio_project[2] / 'music'
    async def directory(_):
        return library
    monkeypatch.setattr(music_api, '_music_directory', directory)
    wav = tmp_path / 'demo.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=1:frequency=220', str(wav)], check=True, capture_output=True)
    uploads = []
    for name in ['Nhạc nền.wav', 'Nhạc khác.wav']:
        res = await client.post(f"/api/music/files/{item['project_id']}/upload", params={'filename': name}, content=wav.read_bytes())
        assert res.status_code == 201, res.text
        uploads.append(res.json()['filename'])
    res = await client.post(f'/api/videos/{vid}/pacing/assets', files={'file': ('Effect.wav', wav.read_bytes(), 'audio/wav')})
    assert res.status_code == 201, res.text
    effect_id = res.json()['id']
    url = f'/api/videos/{vid}/timeline'
    for _ in range(2):
        state = (await client.get(url)).json()
        music = [a for a in state['assets'] if a['category'] == 'music']
        assert {a['ref'] for a in music} == {f'music:{name}' for name in uploads}
        effect = next(a for a in state['assets'] if a['ref'] == effect_id)
        assert effect['category'] == 'sfx' and effect['has_audio'] and effect['duration'] == pytest.approx(1)
        assert (await client.get(effect['url'])).content[:4] == b'RIFF'
        assert all('path' not in a for a in music)
    # Selected music in the legacy mixer is unrelated to imported library items.
    assert 'saved.wav' in audio_project[4].read_text()
    outside = tmp_path / 'private.wav'; outside.write_bytes(wav.read_bytes())
    (library / 'escape.wav').symlink_to(outside)
    assert not any(a['ref'] == 'music:escape.wav' for a in (await client.get(url)).json()['assets'])


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_different_aspect_sources_share_output_frame_and_audio(timeline_context, tmp_path):
    vid, _, client = timeline_context
    url = f'/api/videos/{vid}/timeline'
    state = (await client.get(url)).json()
    portrait = tmp_path / 'portrait.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=blue:s=48x96:r=24:d=1',
                    '-f', 'lavfi', '-i', 'sine=duration=1:frequency=220', '-t', '1', '-c:v', 'libx264', '-c:a', 'aac', str(portrait)], check=True, capture_output=True)
    other = await timeline.register(vid, vid, 'portrait-test', 'Portrait', portrait, 'source')
    doc = segments(state['document'], [(0, 0, 1), (1, 0, 1)])
    for c in doc['clips'][2:]:
        c['asset_id'] = other['id']
    assert (await client.put(url, json={'revision': 0, 'document': doc})).status_code == 200
    await client.post(url + '/render', json={'revision': 1})
    job = await finished(client, url)
    result = job['result']
    assert (result['width'], result['height']) == (160, 96) and result['duration'] == pytest.approx(2, abs=.05)
    path = editing.directory(vid) / 'exports' / f"{result['export_id']}.mp4"
    # Portrait picture is contained in the shared canvas; side bars remain black.
    assert max(pixels(path, 1.5)) < 10
    assert rms(path, 1.5) > .05
