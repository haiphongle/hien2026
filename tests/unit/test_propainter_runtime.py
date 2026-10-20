"""Exercise real child lifetime/protocol without downloading or loading models."""
import asyncio
import sys

import psutil
import pytest

from agent.services import propainter_trial as trial
from agent.services.propainter_runtime import ProPainterSession
from tools.propainter_worker import cache_model


@pytest.fixture(autouse=True)
def isolated_weights(tmp_path, monkeypatch):
    monkeypatch.setattr(trial, 'CACHE', tmp_path/'cache')
    root = trial.weights_directory()
    root.mkdir(parents=True)
    for name in trial.WEIGHTS:
        (root/name).write_bytes(b'isolated checkpoint')


def test_cached_weights_preserve_upstream_explicit_super_and_initialize_once():
    class Parent:
        def __init__(self):
            self.loads = 1
    class Model(Parent):
        def __init__(self, path):
            super(Model, self).__init__()
            self.path = path
    cache = {}
    cache_model(Model, cache)
    first = Model('weights/model.pth')
    first.loads += 1
    assert Model('weights/model.pth') is first
    assert first.loads == 2 and len(cache) == 1


def worker(code):
    session = ProPainterSession('', '', '')
    session.command = [sys.executable, '-u', '-c', code]
    return session


async def test_session_reuses_one_process_for_multiple_windows_and_closes():
    session = worker("import sys, json, os\nfor line in sys.stdin:\n print('FLOWKIT_STAGE:flow', flush=True)\n print('FLOWKIT_DONE:' + json.dumps({'ok':True}), flush=True)")
    updates = []
    try:
        await session.run('/tmp', 32, 32, 4, updates.append)
        pid = session.process.pid
        await session.run('/tmp', 64, 32, 4, updates.append)
        assert session.process.pid == pid
        assert updates == ['FLOWKIT_STAGE:flow', 'FLOWKIT_STAGE:flow']
    finally:
        await session.close()
    assert session.process is None and not psutil.pid_exists(pid)


@pytest.mark.parametrize('failure', ['timeout', 'cancel', 'exit', 'model-error'])
async def test_bad_or_cancelled_request_always_reaps_child(failure):
    action = {'exit': 'sys.exit(2)', 'model-error': "print('FLOWKIT_DONE:' + json.dumps({'ok':False, 'error':'test failure'}), flush=True)"}.get(failure, 'time.sleep(10)')
    session = worker(f"import sys, time, json\nsys.stdin.readline()\nprint('READY', flush=True)\n{action}")
    ready = asyncio.Event()
    pids = []
    def started(_):
        pids.append(session.process.pid)
        ready.set()
    task = asyncio.create_task(session.run('/tmp', 32, 32, 4, started, timeout=.3 if failure == 'timeout' else 5))
    await asyncio.wait_for(ready.wait(), 3)
    pid = pids[0]
    if failure == 'cancel':
        task.cancel()
    error = asyncio.CancelledError if failure == 'cancel' else TimeoutError if failure == 'timeout' else RuntimeError
    with pytest.raises(error):
        await task
    assert session.process is None and not psutil.pid_exists(pid)


async def test_nested_task_session_is_shared_but_next_task_is_fresh(monkeypatch):
    monkeypatch.setattr(trial, 'LOCK', asyncio.Lock())
    async with trial.inference_session() as first:
        assert trial.LOCK.locked()
        async with trial.inference_session() as nested:
            assert nested is first
    assert trial._session.get() is None and not trial.LOCK.locked()
    async with trial.inference_session() as second:
        assert second is not first and second.process is None


async def test_cancelling_queued_task_does_not_close_active_worker(monkeypatch):
    monkeypatch.setattr(trial, 'LOCK', asyncio.Lock())
    entered, release = asyncio.Event(), asyncio.Event()
    async def active():
        async with trial.inference_session() as session:
            session.command = [sys.executable, '-u', '-c', "import sys\nfor line in sys.stdin: print('FLOWKIT_DONE:{\"ok\":true}', flush=True)"]
            await session.run('/tmp', 32, 32, 4, lambda _: None)
            pid = session.process.pid
            entered.set()
            await release.wait()
            assert psutil.pid_exists(pid)
            await session.run('/tmp', 32, 32, 4, lambda _: None)
    async def queued():
        async with trial.inference_session():
            pytest.fail('Queued session acquired a busy worker')
    task = asyncio.create_task(active())
    await asyncio.wait_for(entered.wait(), 3)
    waiter = asyncio.create_task(queued())
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await task
