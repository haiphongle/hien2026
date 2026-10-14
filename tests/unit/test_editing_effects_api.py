"""Real short previews, persisted overlay uploads, version names and trial guards."""
import asyncio
from io import BytesIO
import shutil
import subprocess
import uuid

import httpx
from fastapi import FastAPI
from PIL import Image
import pytest

from agent.api import editing as api
from agent.models.editing import EditRegion, EditingSettings, ProPainterTrialRequest
from agent.services import editing, media_jobs, propainter_trial
from tests.unit.test_audio_mix import audio_project


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_assets_clip_preview_and_export_history(audio_project, tmp_path, monkeypatch):
    vid = audio_project[0]
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=320x240:r=12:d=2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, capture_output=True)
    monkeypatch.setattr(editing, 'ROOT', tmp_path / 'editing')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path / 'jobs')
    monkeypatch.setattr(api, 'local_source', lambda _: source)
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    image = BytesIO()
    Image.new('RGB', (80, 50), 'red').save(image, 'PNG')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        res = await client.post(f'/api/videos/{vid}/editing/assets', files={'file': ('logo.png', image.getvalue(), 'image/png')})
        assert res.status_code == 201
        asset = res.json()
        assert (await client.get(asset['url'])).content.startswith(b'\x89PNG')
        assert (await client.post(f'/api/videos/{vid}/editing/caption-preview', json={'width': 320, 'height': 192, 'style': {}, 'text': 'Chữ mẫu'})).status_code == 200
        region = EditRegion(id=uuid.uuid4(), kind='image', image_id=asset['id'], start=1, end=2)
        draft = EditingSettings(regions=[region])
        res = await client.put(f'/api/videos/{vid}/editing', json={'revision': 0, 'settings': draft.model_dump(mode='json')})
        assert res.status_code == 200
        res = await client.post(f'/api/videos/{vid}/editing/preview-clips', json={'settings': draft.model_dump(mode='json'), 'start': 1, 'duration': 1})
        assert res.status_code == 202
        await asyncio.gather(*list(media_jobs._tasks.values()))
        job = (await client.get(f'/api/videos/{vid}/editing/preview-job')).json()
        assert job['status'] == 'COMPLETED', job
        assert (await client.get(job['result']['url'])).status_code == 200
        result = job['result']
        path = editing.directory(vid) / 'previews' / (result['url'].split('/')[-1] + '.mp4')
        assert abs(editing.probe(path)['duration'] - 1) < .1
        assert editing.probe(path)['has_audio']
        for data in [b'not an image', b'x' * (5 * 1024 * 1024 + 1)]:
            assert (await client.post(f'/api/videos/{vid}/editing/assets', files={'file': ('bad.png', data, 'image/png')})).status_code in {413, 422}
        export_id = str(uuid.uuid4())
        editing.record_export(vid, {'export_id': export_id, 'url': f'/api/videos/{vid}/editing/exports/{export_id}', 'settings': draft.model_dump(mode='json')})
        res = await client.patch(f'/api/videos/{vid}/editing/exports/{export_id}', json={'name': 'Bản thử vùng che'})
        assert res.status_code == 200
        state = (await client.get(f'/api/videos/{vid}/editing')).json()
        assert state['exports'][0]['name'] == state['last_export']['name'] == 'Bản thử vùng che'
        assert state['revision'] == 1 and state['settings'] == draft.model_dump(mode='json')


def test_propainter_trial_limits():
    region = EditRegion(id=uuid.uuid4(), x=.6, y=.6, width=.25, height=.2, start=5, end=8)
    settings = EditingSettings(regions=[region])
    request = ProPainterTrialRequest(settings=settings, region_id=region.id, start=5, duration=1)
    assert request.max_side == 192
    assert request.model_copy(update={'max_side': 320}).max_side == 320
    with pytest.raises(ValueError):
        ProPainterTrialRequest(settings=settings, region_id=region.id, start=5, duration=1, max_side=256)
    info = {'duration': 10, 'fps_number': 30}
    assert propainter_trial.validate(settings, region.id, 5, 2, info) == region
    assert propainter_trial.validate(settings, region.id, 5, 1, {**info, 'fps_number': 60}) == region
    with pytest.raises(ValueError, match='60 frame'):
        propainter_trial.validate(settings, region.id, 5, 3, info)
    with pytest.raises(ValueError, match='khoảng thời gian'):
        propainter_trial.validate(settings, region.id, 4, 2, info)
    with pytest.raises(ValueError, match='Thêm và chọn'):
        propainter_trial.validate(settings, uuid.uuid4(), 5, 2, info)
    large = region.model_copy(update={'x': .1, 'y': .1, 'width': .8, 'height': .8})
    with pytest.raises(ValueError, match='35%'):
        propainter_trial.validate(EditingSettings(regions=[large]), large.id, 5, 2, info)


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
@pytest.mark.parametrize('cancel', [False, True])
async def test_trial_restores_fractional_fps_or_cleans_cancelled_output(tmp_path, monkeypatch, cancel):
    import json
    from pathlib import Path
    trial = propainter_trial
    vid = str(uuid.uuid4())
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=white:s=320x240:r=30000/1001:d=2',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, capture_output=True)
    original = source.read_bytes()
    monkeypatch.setattr(editing, 'ROOT', tmp_path / 'editing')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path / 'jobs')
    monkeypatch.setattr(trial, 'CACHE', tmp_path / 'cache')
    (trial.CACHE / 'weights').mkdir(parents=True)
    monkeypatch.setattr(trial, 'LOCK', asyncio.Lock())
    process = media_jobs.process
    ready = asyncio.Event()
    phases = []
    async def fake_model(args, **options):
        if args[0] != str(trial.PYTHON):
            return await process(args, **options)
        work = Path(args[args.index('--work') + 1])
        if cancel:
            ready.set()
            await asyncio.Event().wait()
        else:
            for name in ['flow', 'complete', 'propagate', 'inpaint']:
                options['on_line'](f'FLOWKIT_STAGE:{name}')
            options['on_line']('50%|█████     | 5/10')
            raw = work / 'output' / 'frames' / 'inpaint_out.mp4'
            raw.parent.mkdir(parents=True)
            await process(['ffmpeg', '-v', 'error', '-y', '-framerate', '12', '-i', str(work / 'frames' / '%05d.png'), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(raw)])
            (work / 'metrics.json').write_text(json.dumps({'inference_seconds': 0, 'device': 'test', 'peak_mps_driver_bytes': 0}))
    monkeypatch.setattr(media_jobs, 'process', fake_model)
    info = await trial.source_info(source)
    region = EditRegion(id=uuid.uuid4(), start=0, end=2.1)
    settings = EditingSettings(regions=[region])
    async def run(update):
        def recorded(**change):
            if 'progress' in change:
                phases.append(change['progress'])
            update(**change)
        await trial.run_trial(vid, source, settings, region, 0, 2, 4, info, recorded)
    media_jobs.start(vid, 'propainter-trial', run)
    if cancel:
        await asyncio.wait_for(ready.wait(), 5)
        job = await media_jobs.cancel(vid)
        assert job['status'] == 'CANCELLED'
        assert not list((editing.directory(vid) / 'propainter').glob('*.mp4'))
    else:
        await asyncio.gather(*list(media_jobs._tasks.values()))
        job = media_jobs.latest(vid)
        assert job['status'] == 'COMPLETED', job
        assert job['result']['fps'] == '30000/1001' and job['result']['has_audio']
        assert max(job['result']['width'], job['result']['height']) == 192
        assert job['result']['max_side'] == 192
        assert phases == [0, 5, 15, 20, 40, 55, 65, 75, 90]
        assert abs(job['result']['duration'] - 2) < .12
        stored = trial.results(vid)
        assert len(stored) == 1 and stored[0] == job['result']
        before = editing.directory(vid) / 'propainter' / f"{stored[0]['trial_id']}-before.mp4"
        assert before.is_file() and editing.probe(before)['has_audio']
        # A later workbench job cannot erase the persistent AI result history.
        (media_jobs.directory(vid) / 'job.json').unlink()
        assert trial.results(vid) == stored
    assert not list(trial.CACHE.glob('trial-*'))
    assert source.read_bytes() == original


async def test_propainter_history_status_files_and_source_change(audio_project, tmp_path, monkeypatch):
    import json
    from agent.api import timeline as timeline_api
    from agent.services import pacing, timeline
    vid = audio_project[0]
    monkeypatch.setattr(editing, 'ROOT', tmp_path / 'editing')
    monkeypatch.setattr(timeline, 'ROOT', tmp_path / 'timeline')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path / 'jobs')
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    monkeypatch.setattr(api, 'local_source', lambda _: source)
    async def source_info(_):
        return dict(width=320, height=240, fps='30/1', fps_number=30, duration=5, has_audio=True)
    monkeypatch.setattr(propainter_trial, 'source_info', source_info)
    monkeypatch.setattr(propainter_trial, 'availability', lambda: {'available': True, 'benchmark': {}, 'scope': 'test'})
    rid = str(uuid.uuid4())
    folder = editing.directory(vid) / 'propainter'
    folder.mkdir(parents=True)
    (folder / f'{rid}.mp4').write_bytes(b'after')
    (folder / f'{rid}-before.mp4').write_bytes(b'before')
    record = {'trial_id': rid, 'created_at': 1, 'start': 1, 'duration': 1, 'width': 192, 'height': 192,
              'inference_seconds': 10, 'device': 'mps', 'peak_rss_bytes': 100, 'peak_mps_driver_bytes': 200,
              'url': f'/api/videos/{vid}/editing/propainter/results/{rid}',
              'before_url': f'/api/videos/{vid}/editing/propainter/results/{rid}/before'}
    (folder / f'{rid}.json').write_text(json.dumps(record))
    # Partially written or unrelated files do not become library media.
    (folder / 'unrelated.json').write_text('{}')
    ghost = str(uuid.uuid4())
    (folder / f'{ghost}.json').write_text(json.dumps({'trial_id': ghost}))
    # Corrupt JSON, unexpected shapes and invalid numbers must not break the catalog.
    for raw in ['{', '[]', 'null', json.dumps({**record, 'created_at': 'bad'}), json.dumps({**record, 'duration': float('nan')})]:
        bad = str(uuid.uuid4())
        (folder / f'{bad}.mp4').write_bytes(b'bad')
        (folder / f'{bad}.json').write_text(raw.replace(rid, bad))
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get(f'/api/videos/{vid}/editing/propainter')
        assert response.status_code == 200
        assert response.json()['results'] == [record]
        assert response.json()['source']['fingerprint'] == pacing.fingerprint(source)
        assert (await client.get(f'/api/videos/{vid}/editing/propainter/results/{rid}')).content == b'after'
        assert (await client.get(f'/api/videos/{vid}/editing/propainter/results/{rid}/before')).content == b'before'
        region = EditRegion(id=uuid.uuid4(), start=0, end=1)
        payload = {'settings': EditingSettings(regions=[region]).model_dump(mode='json'), 'region_id': str(region.id), 'start': 0, 'duration': 1, 'source_fingerprint': 'stale'}
        res = await client.post(f'/api/videos/{vid}/editing/propainter', json=payload)
        assert res.status_code == 409 and 'đã thay đổi' in res.text
        payload['duration'] = .5
        assert (await client.post(f'/api/videos/{vid}/editing/propainter', json=payload)).status_code == 422
    registered = []
    async def register(*args):
        registered.append(args)
    monkeypatch.setattr(timeline, 'register', register)
    monkeypatch.setattr(pacing, 'library', lambda _: [])
    monkeypatch.setattr(timeline_api.speech_store, 'load_document', lambda _: type('Doc', (), {'cues': []})())
    await timeline_api.catalog(await api.video(uuid.UUID(vid)))
    assert any(args[2] == f'propainter:{rid}' and args[4] == folder / f'{rid}.mp4' for args in registered)
    (folder / f'{rid}-before.mp4').unlink()
    assert propainter_trial.results(vid) == [{**record, 'before_url': None}]


async def test_propainter_cancel_rejects_stale_job_without_stopping_current_job(audio_project, tmp_path, monkeypatch):
    vid = audio_project[0]
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path / 'jobs')
    async def wait(update):
        await asyncio.Event().wait()
    job = media_jobs.start(vid, 'propainter-trial', wait)
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            path = f'/api/videos/{vid}/editing/propainter/cancel'
            response = await client.post(path, params={'job_id': str(uuid.uuid4())})
            assert response.status_code == 409
            assert vid in media_jobs._tasks and not media_jobs._tasks[vid].done()
            response = await client.post(path, params={'job_id': job['id']})
            assert response.status_code == 200 and response.json()['status'] == 'CANCELLED'
            assert vid not in media_jobs._tasks
    finally:
        await media_jobs.cancel(vid)
