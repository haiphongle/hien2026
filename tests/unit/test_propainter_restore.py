"""Selected-scene restoration with real FFmpeg and isolated model substitutes."""
import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import httpx
from fastapi import FastAPI
from PIL import Image
import pytest

from agent.api import editing as api
from agent.models.editing import EditRegion
from agent.services import editing, media_jobs, propainter_restore as restore, propainter_trial as trial, timeline
from agent.services.speech_store import atomic_json
from tests.unit.test_audio_mix import audio_project


def test_windows_are_bounded_cover_all_frames_and_overlap():
    for frames in (1, 24, 49, 54, 55, 60, 96, 240, 480, 481, 1800):
        sections = restore.windows(frames)
        assert sections[0][0] == 0 and sections[-1][1] == frames
        assert all(0 < b-a <= 60 for a, b in sections)
        assert all(a < previous[1] for previous, (a, b) in zip(sections, sections[1:]))
        assert all(b > previous[1] for previous, (a, b) in zip(sections, sections[1:]))
        if frames <= 60:
            assert sections == [(0, frames)]


def test_readiness_checks_actual_source_instead_of_only_old_benchmark(tmp_path, monkeypatch):
    monkeypatch.setattr(trial, 'PROJECT', tmp_path / 'project')
    monkeypatch.setattr(trial, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(trial, 'PYTHON', tmp_path / 'python')
    trial.PYTHON.touch()
    for name in trial.WEIGHTS:
        path = trial.CACHE / 'weights' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    atomic_json(trial.CACHE / 'benchmark.json', {'status': 'COMPLETED'})
    assert not trial.availability()['available']
    assert 'mã nguồn' in trial.availability()['unavailable_reason']
    source = trial.CACHE / 'source' / 'inference_propainter.py'
    source.parent.mkdir(); source.touch()
    assert trial.availability()['available']


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
@pytest.mark.parametrize('fps', ['12', '30000/1001', '60'])
async def test_eight_second_scene_keeps_size_timing_audio_and_reuses_completed_output(tmp_path, monkeypatch, fps):
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=s=384x216:r={fps}:d=10',
        '-f', 'lavfi', '-i', 'sine=frequency=440:duration=10', '-t', '10', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, capture_output=True)
    original = source.read_bytes()
    monkeypatch.setattr(editing, 'ROOT', tmp_path / 'editing')
    monkeypatch.setattr(trial, 'CACHE', tmp_path / 'cache')
    monkeypatch.setattr(trial, 'LOCK', asyncio.Lock())
    (trial.CACHE / 'weights').mkdir(parents=True)
    process, calls, fail_second = media_jobs.process, [], fps == '12'
    async def model(args, **options):
        nonlocal fail_second
        if args[0] != str(trial.PYTHON):
            return await process(args, **options)
        calls.append(args)
        if fail_second and len(calls) == 2:
            fail_second = False
            raise RuntimeError('interrupted window')
        work = Path(args[args.index('--work')+1])
        raw = work / 'output' / 'frames' / 'inpaint_out.mp4'
        raw.parent.mkdir(parents=True)
        await process(['ffmpeg', '-v', 'error', '-y', '-framerate', '12', '-i', str(work / 'frames' / '%05d.png'),
                       '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(raw)])
        atomic_json(work / 'metrics.json', dict(inference_seconds=0, device='test', peak_mps_driver_bytes=0))
        options['on_line']('FLOWKIT_STAGE:inpaint')
    monkeypatch.setattr(media_jobs, 'process', model)
    region = EditRegion(id=uuid.uuid4(), x=.8, y=.8, width=.15, height=.12, end=10)
    targets = [dict(clip_id=str(uuid.uuid4()), asset_id=str(uuid.uuid4()), source_in=1, duration=8, speed=1)]
    updates = []
    vid = str(uuid.uuid4())
    async def run():
        return await restore.run_clip(vid, source, 1, 8, region, 4, 192, targets, lambda **u: updates.append(u))
    if fail_second:
        with pytest.raises(RuntimeError, match='interrupted'):
            await run()
        assert not trial.results(vid)
        assert len(list((trial.CACHE / 'parts').glob('*/*.mp4'))) == 1
    result = await run()
    assert result['full_resolution'] and result['targets'] == targets
    assert (result['width'], result['height']) == (384, 216)
    assert abs(result['duration']-8) < .06 and result['has_audio']
    info = await trial.source_info(source)
    assert result['fps'] == info['fps']
    assert trial.results(vid)[0]['trial_id'] == result['trial_id']
    call_count = len(calls)
    assert call_count == len(restore.windows(round(8*info['fps_number']))) + (fps == '12')
    assert (await run())['trial_id'] == result['trial_id'] and len(calls) == call_count
    # Whole-frame detail is kept; the AI patch does not shrink the source to 192 px.
    output = editing.directory(vid) / 'propainter' / f"{result['trial_id']}.mp4"
    frames = []
    for path, seek in [(source, '3'), (output, '2')]:
        image_path = tmp_path / f'frame-{len(frames)}.png'
        await process(['ffmpeg', '-v', 'error', '-y', '-ss', seek, '-i', str(path), '-frames:v', '1', str(image_path)])
        frames.append(Image.open(image_path).convert('RGB'))
    import numpy as np
    a, b = (np.asarray(f).astype(float)[:120, :200] for f in frames)
    assert np.abs(a-b).mean() < 4
    assert source.read_bytes() == original
    assert not list(trial.CACHE.glob('restore-*'))


async def test_clip_endpoint_checks_saved_revision_selection_and_bound_source(audio_project, tmp_path, monkeypatch):
    from agent.api import timeline as timeline_api
    vid = audio_project[0]
    monkeypatch.setattr(editing, 'ROOT', tmp_path / 'editing')
    monkeypatch.setattr(timeline, 'ROOT', tmp_path / 'timeline')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path / 'jobs')
    aid, cid = str(uuid.uuid4()), str(uuid.uuid4())
    source = dict(id=aid, owner=vid, ref='original', name='Test', kind='video', category='source', duration=20,
                  width=384, height=216, fps=30, has_audio=True, path=str(tmp_path / 'source.mp4'))
    document = timeline.initial(source).model_dump(mode='json')
    document['clips'][0].update(id=cid, source_in=2, duration=8)
    document['clips'][1].update(source_in=2, duration=8)
    atomic_json(timeline.directory(vid) / 'document.json', dict(revision=3, document=document))
    monkeypatch.setattr(trial, 'availability', lambda: {'available': True})
    async def checked(*args):
        return source
    monkeypatch.setattr(timeline_api, 'checked_asset', checked)
    received = []
    async def fake_run(*args):
        received.append(args)
        return {'trial_id': str(uuid.uuid4())}
    monkeypatch.setattr(restore, 'run_clip', fake_run)
    app = FastAPI(); app.include_router(api.router, prefix='/api')
    payload = dict(revision=2, clip_ids=[cid], region=EditRegion(id=uuid.uuid4()).model_dump(mode='json'))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        url = f'/api/videos/{vid}/editing/propainter/clips'
        assert (await client.post(url, json=payload)).status_code == 409
        payload.update(revision=3, clip_ids=[str(uuid.uuid4())])
        assert (await client.post(url, json=payload)).status_code == 422
        payload['clip_ids'] = [cid, cid]
        response = await client.post(url, json=payload)
        assert response.status_code == 202, response.text
        await asyncio.gather(*list(media_jobs._tasks.values()))
        assert len(received) == 1
        assert received[0][1:4] == (Path(source['path']), 2, 8)
        assert received[0][7] == [dict(clip_id=cid, asset_id=aid, source_in=2, duration=8, speed=1)]
        assert media_jobs.latest(vid)['status'] == 'COMPLETED'


@pytest.mark.skipif(os.getenv('FLOWKIT_TEST_PROPAINTER') != '1', reason='explicit local-model smoke test')
async def test_real_model_eight_second_logo_sample(tmp_path, monkeypatch):
    source = tmp_path / 'sample.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
        'color=c=0x85a9bc:s=384x216:r=12:d=8', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8',
        '-vf', 'drawbox=x=310:y=172:w=42:h=20:color=red:t=fill', '-t', '8',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(source)], check=True, capture_output=True)
    monkeypatch.setattr(editing, 'ROOT', tmp_path / 'editing')
    monkeypatch.setattr(trial, 'LOCK', asyncio.Lock())
    region = EditRegion(id=uuid.uuid4(), x=308/384, y=170/216, width=46/384, height=24/216, end=8)
    result = await restore.run_clip(str(uuid.uuid4()), source, 0, 8, region, 4, 192, [], lambda **u: print(u, flush=True))
    assert result['duration'] == 8 and result['has_audio'] and result['width'] == 384
    output = editing.directory(result['url'].split('/')[3]) / 'propainter' / f"{result['trial_id']}.mp4"
    for seek in (1, 3.9, 4.1, 7):
        image_path = tmp_path / f'{seek}.png'
        await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-ss', str(seek), '-i', str(output), '-frames:v', '1', str(image_path)])
        import numpy as np
        image = np.asarray(Image.open(image_path).convert('RGB')).astype(float)
        # The owned red test mark should be filled with the surrounding blue background.
        assert abs(image[175:185, 315:340].mean(axis=(0, 1))[0] - 133) < 45
    print('REAL_RESULT', json.dumps(result), flush=True)
