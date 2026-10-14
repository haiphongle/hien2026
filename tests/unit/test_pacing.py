"""Real silence detection, AV cuts, timed SFX, persistence and cancellation."""
import asyncio
import json
import shutil
import subprocess
import uuid

import httpx
import pytest
from fastapi import FastAPI

from agent.api import pacing as api
from agent.models.pacing import PacingDraft, SoundEvent
from agent.models.speech import Cue, SpeechDocument
from agent.services import editing, media_jobs, pacing, speech_store
from tests.unit.test_audio_mix import audio_project


def test_first_library_open_is_safe_for_concurrent_panels(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import wave
    monkeypatch.setattr(pacing, 'ROOT', tmp_path / 'pacing')
    video_id = str(uuid.uuid4())
    barrier = threading.Barrier(8)
    def open_library(_):
        barrier.wait(timeout=10)
        return pacing.library(video_id)
    with ThreadPoolExecutor(max_workers=8) as executor:
        catalogs = list(executor.map(open_library, range(8)))
    assert len(catalogs[0]) == 4
    assert all(catalog == catalogs[0] for catalog in catalogs)
    for asset in catalogs[0]:
        with wave.open(str(pacing.asset_path(video_id, asset['id']))) as audio:
            assert audio.getnframes() == round(asset['duration'] * 44100)
    assert not list(pacing.directory(video_id).rglob('*.tmp'))


def test_cut_padding_frame_mapping_and_all_silent_guard():
    cuts = pacing.candidates([(0, .5), (1, 2), (3, 4)], 4, .7, .2)
    assert [(c['start'], c['end']) for c in cuts] == [(1.2, 1.8), (3.2, 4)]
    ranges = pacing.keep_ranges(4, cuts, 30)
    assert [(r['start'], r['end']) for r in ranges] == [(0, 1.2), (1.8, 3.2)]
    assert ranges[-1]['output_end'] == pytest.approx(2.6)
    with pytest.raises(ValueError, match='gần hết'):
        pacing.keep_ranges(4, [{'start': 0, 'end': 4}], 30)
    assert pacing.speech_gaps([Cue(start=1, end=2, text='Hello'), Cue(start=1.5, end=3, text='World')], 4) == [(0, 1), (3, 4)]
    read, finish = pacing.silence_reader(5)
    read('silence_start: 2.0'); read('silence_end: 3.5 | silence_duration: 1.5'); read('silence_start: 4')
    assert finish() == [(2, 3.5), (4, 5)]


async def test_probe_uses_stream_duration_when_container_reports_na(monkeypatch):
    async def fake_process(_args, **_kwargs):
        return json.dumps({
            'format': {'duration': 'N/A'},
            'streams': [
                {'codec_type': 'video', 'width': 1080, 'height': 1920,
                 'avg_frame_rate': '24/1', 'duration': '12.000000'},
                {'codec_type': 'audio', 'duration': '12.021333'},
            ],
        })

    monkeypatch.setattr(pacing.media_jobs, 'process', fake_process)
    result = await pacing.probe('render.mp4')
    assert result == {
        'duration': 12.0, 'fps': 24, 'width': 1080, 'height': 1920, 'has_audio': True,
    }


@pytest.fixture
async def pacing_context(audio_project, tmp_path, monkeypatch):
    vid = audio_project[0]
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=160x96:r=30:d=4',
        '-f', 'lavfi', '-i', r'aevalsrc=if(between(t\,1\,2)\,0\,0.12*sin(2*PI*440*t)):s=48000:d=4',
        '-vf', "drawbox=x=0:y=0:w=30:h=30:color=red:t=fill:enable='gte(t,2)'", '-t', '4',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, capture_output=True)
    monkeypatch.setattr(pacing, 'ROOT', tmp_path / 'pacing')
    monkeypatch.setattr(editing, 'ROOT', tmp_path / 'editing')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path / 'jobs')
    monkeypatch.setattr(speech_store, 'ROOT', tmp_path / 'speech')
    monkeypatch.setattr(api, 'local_source', lambda _: source)
    app = FastAPI(); app.include_router(api.router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield vid, source, client
    await media_jobs.shutdown()


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_analyze_save_sfx_render_and_stale_source(pacing_context):
    vid, source, client = pacing_context
    original = source.read_bytes()
    url = f'/api/videos/{vid}/pacing'
    state = (await client.get(url)).json()
    assert len(state['assets']) == 4
    assert (await client.get(state['assets'][0]['url'])).content.startswith(b'RIFF')
    res = await client.post(url + '/analyze', json={})
    assert res.status_code == 202, res.text
    await asyncio.gather(*list(media_jobs._tasks.values()))
    job = (await client.get(url + '/job')).json()
    assert job['status'] == 'COMPLETED', job
    state = (await client.get(url)).json()
    assert len(state['plan']['cuts']) == len(state['draft']['cut_ids']) == 1
    cut = state['plan']['cuts'][0]
    assert 1.16 < cut['start'] < 1.23 and 1.77 < cut['end'] < 1.85
    state['draft']['effects'] = [dict(id=str(uuid.uuid4()), asset_id=state['assets'][0]['id'], start=.4, volume=.7)]
    state['draft']['base_volume'] = .3
    res = await client.put(url, json={k: state[k] for k in ('revision', 'draft')})
    assert res.status_code == 200, res.text
    assert (await client.put(url, json={k: state[k] for k in ('revision', 'draft')})).status_code == 409
    state = res.json()
    assert (await client.get(url)).json()['draft'] == state['draft']
    res = await client.post(url + '/render', json={'revision': state['revision']})
    assert res.status_code == 202, res.text
    await asyncio.gather(*list(media_jobs._tasks.values()))
    job = (await client.get(url + '/job')).json()
    assert job['status'] == 'COMPLETED', job
    result = job['result']; output = editing.directory(vid) / 'exports' / (result['export_id'] + '.mp4')
    assert 3.3 < result['duration'] < 3.5 and result['has_audio']
    assert len(result['mapping']) == 2
    # A baked visual marker after the silent gap must move together with speech.
    pixels = subprocess.run(['ffmpeg', '-v', 'error', '-ss', '1.5', '-i', str(output), '-vf', 'crop=2:2:4:4', '-frames:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'], capture_output=True, check=True).stdout
    assert pixels[0] > 200 and pixels[1] < 60 and pixels[2] < 60
    # Synthetic SFX adds audible energy at its chosen output time, base is quieter.
    def rms(at):
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(at), '-i', str(output), '-t', '0.1', '-map', '0:a', '-ac', '1', '-f', 'f32le', '-'], capture_output=True, check=True).stdout
        import array
        values = array.array('f', raw)
        return (sum(v*v for v in values) / len(values)) ** .5
    assert rms(.45) > rms(.1) * 1.5
    assert source.read_bytes() == original
    assert editing.load(vid)['exports'][0]['operation'] == 'pacing'
    source.write_bytes(original + b'changed')
    assert (await client.post(url + '/render', json={'revision': state['revision']})).status_code == 422


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_upload_and_speech_gaps_and_invalid_events(pacing_context, tmp_path):
    vid, source, client = pacing_context
    url = f'/api/videos/{vid}/pacing'
    wav = tmp_path / 'laugh.wav'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=0.5', str(wav)], capture_output=True, check=True)
    response = await client.post(url + '/assets', files={'file': ('laugh.wav', wav.read_bytes(), 'audio/wav')})
    assert response.status_code == 201, response.text
    assert (await client.get(response.json()['url'])).content.startswith(b'RIFF')
    assert (await client.post(url + '/assets', files={'file': ('invalid.mp3', b'bad', 'audio/mpeg')})).status_code == 422
    assert (await client.post(url + '/assets', files={'file': ('bad.exe', b'bad')})).status_code == 422
    speech_store.atomic_json(speech_store.video_dir(vid) / 'script.json', SpeechDocument(revision=1, cues=[Cue(start=0, end=1, text='Hello'), Cue(start=2, end=4, text='World')]).model_dump(mode='json'))
    assert (await client.post(url + '/analyze', json={'method': 'speech'})).status_code == 202
    await asyncio.gather(*list(media_jobs._tasks.values()))
    state = (await client.get(url)).json()
    assert state['plan']['speech_revision'] == 1
    assert state['plan']['cuts'][0]['start'] == 1.18
    speech_store.atomic_json(speech_store.video_dir(vid) / 'script.json', SpeechDocument(revision=2).model_dump(mode='json'))
    assert (await client.post(url + '/render', json={'revision': state['revision']})).status_code == 422
    state['draft']['plan_id'] = None; state['draft']['cut_ids'] = [str(uuid.uuid4())]
    assert (await client.put(url, json={k: state[k] for k in ('revision', 'draft')})).status_code == 422


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_cancel_does_not_publish_version_and_kills_child(pacing_context, monkeypatch):
    vid, source, client = pacing_context
    url = f'/api/videos/{vid}/pacing'
    state = (await client.get(url)).json()
    entered = asyncio.Event()
    actual = media_jobs.process
    async def slow(args, **kwargs):
        if args[0] == 'ffmpeg':
            entered.set()
            return await actual(['ffmpeg', '-v', 'error', '-re', '-f', 'lavfi', '-i', 'testsrc2=duration=60:size=64x64', '-f', 'null', '-'])
        return await actual(args, **kwargs)
    monkeypatch.setattr(media_jobs, 'process', slow)
    response = await client.post(url + '/render', json={'revision': state['revision']})
    assert response.status_code == 202, response.text
    await asyncio.wait_for(entered.wait(), 10)
    # Let subprocess startup finish before cancelling its active process group.
    await asyncio.sleep(.1)
    response = await client.post(url + f"/jobs/{response.json()['id']}/cancel")
    assert response.json()['status'] == 'CANCELLED', response.text
    assert editing.load(vid)['exports'] == []
    assert not list(pacing.directory(vid).glob('pacing-*'))
    assert not list((editing.directory(vid) / 'exports').glob('*.mp4'))


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_no_audio_source_can_use_sfx_and_prepare_missing_source(pacing_context, tmp_path, monkeypatch):
    vid, source, client = pacing_context
    url = f'/api/videos/{vid}/pacing'
    silent = tmp_path / 'silent.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-an', '-c:v', 'copy', str(silent)], capture_output=True, check=True)
    monkeypatch.setattr(api, 'local_source', lambda _: tmp_path / 'missing.mp4')
    async def assemble(_vid, target, _orientation, progress_callback=None):
        shutil.copyfile(silent, target)
        if progress_callback:
            progress_callback('assembling', 1)
    monkeypatch.setattr(api, 'render_video_to_file', assemble)
    assert not (await client.get(url)).json()['source_available']
    assert (await client.post(url + '/prepare')).status_code == 202
    await asyncio.gather(*list(media_jobs._tasks.values()))
    assert (await client.get(url + '/job')).json()['status'] == 'COMPLETED'
    state = (await client.get(url)).json()
    assert state['source_available'] and (await client.get(url + '/source')).content == silent.read_bytes()
    assert (await client.post(url + '/analyze', json={})).status_code == 202
    await asyncio.gather(*list(media_jobs._tasks.values()))
    assert (await client.get(url + '/job')).json()['status'] == 'FAILED'
    state['draft']['effects'] = [dict(id=str(uuid.uuid4()), asset_id=state['assets'][0]['id'], start=.4, volume=.7)]
    response = await client.put(url, json={k: state[k] for k in ('revision', 'draft')})
    assert response.status_code == 200, response.text
    response = await client.post(url + '/render', json={'revision': response.json()['revision']})
    assert response.status_code == 202
    await asyncio.gather(*list(media_jobs._tasks.values()))
    result = (await client.get(url + '/job')).json()
    assert result['status'] == 'COMPLETED', result
    assert result['result']['has_audio'] and result['result']['duration'] == pytest.approx(4, abs=.1)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_all_quiet_not_auto_selected_and_sfx_out_of_range_rejected(pacing_context, tmp_path, monkeypatch):
    vid, _, client = pacing_context
    url = f'/api/videos/{vid}/pacing'
    source = tmp_path / 'quiet.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=160x96:r=30:d=2',
        '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo', '-t', '2', '-c:v', 'libx264', '-c:a', 'aac', str(source)], capture_output=True, check=True)
    monkeypatch.setattr(api, 'local_source', lambda _: source)
    await client.get(url)
    assert (await client.post(url + '/analyze', json={})).status_code == 202
    await asyncio.gather(*list(media_jobs._tasks.values()))
    state = (await client.get(url)).json()
    assert state['plan']['warnings'] and not state['draft']['cut_ids']
    state['draft']['effects'] = [dict(id=str(uuid.uuid4()), asset_id=state['assets'][0]['id'], start=5, volume=.7)]
    response = await client.put(url, json={k: state[k] for k in ('revision', 'draft')})
    assert response.status_code == 200
    assert (await client.post(url + '/render', json={'revision': response.json()['revision']})).status_code == 202
    await asyncio.gather(*list(media_jobs._tasks.values()))
    job = (await client.get(url + '/job')).json()
    assert job['status'] == 'FAILED' and 'Mốc hiệu ứng' in job['error']
    assert editing.load(vid)['exports'] == []
