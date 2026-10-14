import asyncio
import shutil
import subprocess

import pytest

from agent.services import audio_mix_jobs as jobs
from agent.services.audio_mix import AudioMixError
from agent.services.ffmpeg_progress import run_ffmpeg

VID = 'd1fbdc5b-95a4-42b2-b6e0-1ad785b08350'


@pytest.fixture(autouse=True)
def isolated_exports(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, 'ROOT', tmp_path)
    monkeypatch.setattr(jobs, '_JOBS', {})
    monkeypatch.setattr(jobs, '_TOKENS', {})


@pytest.mark.parametrize('fail', [False, True])
async def test_progress_duplicate_requests_and_terminal_status(monkeypatch, fail):
    monkeypatch.setattr(jobs, '_JOBS', {})
    started, release = asyncio.Event(), asyncio.Event()
    async def mix(vid, orientation, update, progress_callback):
        progress_callback('music', 0.5)
        started.set()
        await release.wait()
        if fail:
            raise AudioMixError(422, 'Invalid scene audio')
        return {'url': '/finished.mp4'}
    monkeypatch.setattr(jobs, 'finish_audio_mix', mix)
    created = jobs.start_job(VID, 'music', None, {})
    assert created['status'] == 'QUEUED'
    await started.wait()
    assert jobs.latest_job(VID)['status'] == 'PROCESSING'
    assert jobs.latest_job(VID)['progress'] == 65
    with pytest.raises(AudioMixError) as exc:
        jobs.start_job(VID, 'narration', None, {})
    assert exc.value.status_code == 409
    release.set()
    await jobs.drain_jobs()
    result = jobs.latest_job(VID)
    assert result['job_id'] == created['job_id']
    assert result['status'] == ('FAILED' if fail else 'COMPLETED')
    assert result['progress'] == (65 if fail else 100)
    assert result['error_status'] == (422 if fail else None)
    assert result['finished_at'] is not None


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
def test_ffmpeg_progress_timeout_stops_process():
    with pytest.raises(subprocess.TimeoutExpired):
        run_ffmpeg(['ffmpeg', '-re', '-f', 'lavfi', '-i', 'sine', '-f', 'null', '-'],
                   duration=30, timeout=0.2, progress_callback=lambda value: None)


async def test_batch_freezes_each_variant_and_keeps_completed_results_on_cancel(monkeypatch):
    from agent.services import editing
    from agent.services.export_cancel import check_cancelled
    ready, release = asyncio.Event(), asyncio.Event()
    recorded = []
    async def mix(vid, orientation, update, progress_callback, edit_export):
        progress_callback('saving', .5)
        if edit_export['preset'] == '16:9':
            ready.set()
            await release.wait()
            check_cancelled()
        return {'url': '/' + edit_export['preset'], 'revision': edit_export['revision']}
    monkeypatch.setattr(jobs, 'finish_audio_mix', mix)
    monkeypatch.setattr(editing, 'record_export', lambda vid, result: recorded.append(result))
    snapshot = {'variants': [{'preset': '9:16', 'revision': 3}, {'preset': '16:9', 'revision': 3}]}
    created = jobs.start_job(VID, 'edit', None, {}, edit_export=snapshot)
    snapshot['variants'][0]['revision'] = 999
    await ready.wait()
    await asyncio.sleep(0)
    with pytest.raises(AudioMixError):
        jobs.cancel_job(VID, 'old-job-id')
    assert jobs.latest_job(VID)['variant_index'] == 2
    assert jobs.latest_job(VID)['progress'] == 99
    assert recorded == [{'url': '/9:16', 'revision': 3}]
    assert jobs.cancel_job(VID, created['job_id'])['status'] == 'CANCELLING'
    release.set()
    await jobs.drain_jobs()
    result = jobs.latest_job(VID)
    assert result['status'] == 'CANCELLED' and result['results'] == recorded
    # Reload from disk after clearing the in-memory cache.
    jobs._JOBS.clear()
    assert jobs.latest_job(VID)['status'] == 'CANCELLED'


async def test_pending_cancel_and_restart_recovery(monkeypatch):
    from agent.services.speech_store import atomic_json
    monkeypatch.setattr(jobs, 'finish_audio_mix', lambda *args, **kwargs: pytest.fail('Cancelled queued job must not start'))
    created = jobs.start_job(VID, 'edit', None, {})
    jobs.cancel_job(VID, created['job_id'])
    await jobs.drain_jobs()
    assert jobs.latest_job(VID)['status'] == 'CANCELLED'
    jobs._JOBS.clear()
    atomic_json(jobs.ROOT / VID / 'job.json', {**created, 'status': 'PROCESSING'})
    recovered = jobs.latest_job(VID)
    assert recovered['status'] == 'FAILED' and 'khởi động lại' in recovered['error']


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg required')
async def test_cancellation_stops_real_ffmpeg_thread(monkeypatch):
    import os
    from agent.services.export_cancel import current_token
    started = asyncio.Event()
    child = []
    async def mix(*args, **kwargs):
        token = current_token.get()
        original = token.register
        def register(pid):
            original(pid)
            child.append(pid)
            loop.call_soon_threadsafe(started.set)
        token.register = register
        await asyncio.to_thread(run_ffmpeg,
            ['ffmpeg', '-re', '-f', 'lavfi', '-i', 'sine', '-f', 'null', '-'], 30, 30)
        pytest.fail('Cancelled FFmpeg must raise instead of publishing a result')
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(jobs, 'finish_audio_mix', mix)
    created = jobs.start_job(VID, 'edit', None, {})
    await asyncio.wait_for(started.wait(), 5)
    jobs.cancel_job(VID, created['job_id'])
    await asyncio.wait_for(jobs.drain_jobs(), 5)
    assert jobs.latest_job(VID)['status'] == 'CANCELLED'
    with pytest.raises(ProcessLookupError):
        os.kill(child[0], 0)


async def test_cancel_network_wait_closes_request():
    from agent.services.export_cancel import CancellationToken, ExportCancelled, cancellable_download, current_token
    started, closed = asyncio.Event(), asyncio.Event()
    async def slow_download():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            closed.set()
    token = CancellationToken()
    context = current_token.set(token)
    try:
        task = asyncio.create_task(cancellable_download(slow_download()))
        await started.wait()
        token.cancel()
        with pytest.raises(ExportCancelled):
            await asyncio.wait_for(task, 1)
        assert closed.is_set()
    finally:
        current_token.reset(context)


async def test_storage_error_cannot_block_cancel_or_leave_queued_token(monkeypatch):
    created = jobs.start_job(VID, 'edit', None, {})
    def disk_full(*args):
        raise OSError('No space left on device')
    monkeypatch.setattr(jobs, 'atomic_json', disk_full)
    jobs.cancel_job(VID, created['job_id'])
    await jobs.drain_jobs()
    assert jobs.latest_job(VID)['status'] == 'CANCELLED'
    assert VID not in jobs._TOKENS
    second = '24df8c1b-9291-4ae6-ae21-32ce11405aeb'
    with pytest.raises(OSError):
        jobs.start_job(second, 'edit', None, {})
    assert second not in jobs._JOBS and second not in jobs._TOKENS
