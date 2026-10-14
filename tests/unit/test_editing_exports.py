"""Export deletion protects sources, saved edits and active consumers."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
from fastapi import FastAPI

from agent.api import editing as api, timeline as timeline_api
from agent.db import crud, schema
from agent.services import audio_mix_jobs, editing, editing_exports, media_jobs, media_library, pacing, speech_store, timeline
from agent.services.speech_store import atomic_json


@pytest.fixture
async def context(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path / 'db.sqlite')
    for module in [editing, timeline, media_library, media_jobs, audio_mix_jobs, speech_store, pacing]:
        monkeypatch.setattr(module, 'ROOT', tmp_path / module.__name__.split('.')[-1])
    monkeypatch.setattr(media_library, 'lock', asyncio.Lock())
    monkeypatch.setattr(editing_exports, 'video_export_lock', asyncio.Lock())
    monkeypatch.setattr(pacing, 'probe', AsyncMock(return_value=dict(width=160, height=90, duration=4., fps=30., has_audio=True)))
    await schema.init_db()
    project = await crud.create_project(name='Export deletion test', material='realistic')
    first = await crud.create_video(project['id'], 'First video')
    second = await crud.create_video(project['id'], 'Other edit')
    source = media_jobs.directory(first['id']) / 'source.mp4'
    source.parent.mkdir(parents=True); source.write_bytes(b'original source')
    app = FastAPI()
    app.include_router(api.router, prefix='/api'); app.include_router(timeline_api.router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield first, second, source, client
    await media_jobs.shutdown()
    await schema.close_db()


def export(vid, name='Saved export', *, legacy=False):
    eid = str(uuid.uuid4())
    path = editing.directory(vid) / 'exports' / f'{eid}.mp4'
    path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(eid.encode())
    result = dict(url=f'/api/videos/{vid}/editing/exports/{eid}', name=name, operation='timeline',
                  width=160, height=90, duration=4., revision=2, bytes=path.stat().st_size)
    if not legacy:
        result['export_id'] = eid
    editing.record_export(vid, result)
    return eid, path


async def use_export(vid, owner, eid, path):
    asset = await timeline.register(vid, owner, eid, 'Export as source', path, 'export')
    doc = timeline.initial(asset)
    timeline.save(vid, 0, doc)
    return asset, doc


async def check(client, vid, ids):
    return await client.post(f'/api/videos/{vid}/editing/exports/check-delete', json={'export_ids': ids})


async def remove(client, vid, ids):
    return await client.request('DELETE', f'/api/videos/{vid}/editing/exports', json={'export_ids': ids})


async def test_batch_deletes_files_caches_history_but_preserves_source_settings_and_draft(context):
    first, second, source, client = context
    vid = first['id']
    older, kept = export(vid, 'Keep this export')
    middle, deleted = export(vid, 'Delete older export', legacy=True)
    latest, last_path = export(vid, 'Delete latest export')
    source_asset, doc = await use_export(vid, vid, 'original', source)
    before_doc = (timeline.directory(vid) / 'document.json').read_bytes()
    state = editing.load(vid); state['revision'] = 27
    state['settings']['source_volume'] = .3
    atomic_json(editing.directory(vid) / 'settings.json', state)
    before_settings = (editing.directory(vid) / 'settings.json').read_bytes()
    caches = []
    for owner in [vid, second['id']]:
        registered = await timeline.register(owner, vid, latest, 'Cached export', last_path, 'export')
        root = timeline.directory(owner)
        proxy = root / 'proxies' / f"{registered['id']}.mp4"
        proxy.parent.mkdir(parents=True, exist_ok=True); proxy.write_bytes(b'proxy')
        visual = root / 'visuals' / registered['id']
        visual.mkdir(parents=True); (visual / 'sprite.jpg').write_bytes(b'cached thumbnail')
        caches.extend([proxy, visual, timeline.asset_file(owner, registered['id'])])
    response = await check(client, vid, [latest, middle, latest])
    assert response.status_code == 200, response.text
    assert [e['name'] for e in response.json()['items']] == ['Delete latest export', 'Delete older export']
    assert response.json()['size_bytes'] == 72 and not response.json()['blocked_reason']
    assert (editing.directory(vid) / 'settings.json').read_bytes() == before_settings
    response = await remove(client, vid, [latest, middle, latest])
    assert response.status_code == 200, response.text
    assert response.json() == {'deleted': [latest, middle], 'freed_bytes': 72}
    state = editing.load(vid)
    assert state['revision'] == 27 and state['settings']['source_volume'] == .3
    assert [e['export_id'] for e in state['exports']] == [older]
    assert state['last_export']['export_id'] == older
    assert kept.is_file() and not deleted.exists() and not last_path.exists()
    assert all(not p.exists() for p in caches)
    assert source.read_bytes() == b'original source'
    assert (timeline.directory(vid) / 'document.json').read_bytes() == before_doc
    assert timeline.asset_file(vid, source_asset['id']).is_file()
    assert (await client.get(f'/api/videos/{vid}/editing/exports/{latest}')).status_code == 404
    assert (await client.get(f'/api/videos/{vid}/editing/exports/{older}')).status_code == 200
    assert (await remove(client, vid, [older])).status_code == 200
    assert editing.load(vid)['exports'] == [] and editing.load(vid)['last_export'] is None


@pytest.mark.parametrize('consumer', ['self', 'other', 'trash'])
async def test_saved_timeline_use_blocks_entire_batch_until_removed_and_saved(context, consumer):
    first, second, source, client = context
    vid = first['id']; owner = vid if consumer == 'self' else second['id']
    eid, path = export(vid, 'Used export'); free, free_path = export(vid, 'Unused export')
    _, doc = await use_export(owner, vid, eid, path)
    if consumer == 'trash':
        await crud.update_video(owner, trashed_at='2026-10-10T00:00:00Z')
    report = (await check(client, vid, [eid, free])).json()
    assert report['items'][0]['uses'][0]['id'] == owner
    assert report['items'][0]['uses'][0]['trashed'] == (consumer == 'trash')
    assert (await remove(client, vid, [free, eid])).status_code == 409
    assert path.is_file() and free_path.is_file() and len(editing.load(vid)['exports']) == 2
    doc.clips = []
    timeline.save(owner, 1, doc)
    assert (await remove(client, vid, [eid, free])).status_code == 200
    assert timeline.load(owner)['revision'] == 2
    assert source.read_bytes() == b'original source'


async def test_check_is_revalidated_when_an_export_becomes_used_before_confirmation(context):
    first, second, _, client = context
    vid = first['id']; eid, path = export(vid)
    assert not (await check(client, vid, [eid])).json()['items'][0]['uses']
    await use_export(second['id'], vid, eid, path)
    assert (await remove(client, vid, [eid])).status_code == 409
    assert path.is_file()


async def test_unknown_foreign_invalid_ids_and_symlinks_never_remove_other_files(context, tmp_path):
    first, second, source, client = context
    vid = first['id']; eid, path = export(vid)
    foreign, foreign_path = export(second['id'])
    assert (await remove(client, vid, [eid, foreign])).status_code == 404
    for ids in [[], ['not-a-uuid'], [str(uuid.uuid4())] * 101]:
        assert (await remove(client, vid, ids)).status_code == 422
    path.unlink(); path.symlink_to(source)
    assert (await remove(client, vid, [eid])).status_code == 409
    assert source.read_bytes() == b'original source' and foreign_path.is_file()
    path.unlink()  # Missing files may still be removed from history.
    assert (await check(client, vid, [eid])).json()['size_bytes'] == 0
    assert (await remove(client, vid, [eid])).status_code == 200


@pytest.mark.parametrize('kind', ['media', 'mix', 'render'])
async def test_active_job_protects_saved_snapshot_even_after_clips_removed(context, monkeypatch, kind):
    first, second, _, client = context
    vid = first['id']; eid, path = export(vid)
    if kind == 'media':
        async def running(update):
            await asyncio.Event().wait()
        media_jobs.start(second['id'], 'timeline-render', running)
    elif kind == 'mix':
        monkeypatch.setattr('agent.api.speech.latest_job', lambda _: {'status': 'PROCESSING'})
    else:
        await editing_exports.video_export_lock.acquire()
    try:
        assert (await check(client, vid, [eid])).json()['blocked_reason']
        assert (await remove(client, vid, [eid])).status_code == 409
        assert path.is_file() and len(editing.load(vid)['exports']) == 1
    finally:
        if kind == 'render':
            editing_exports.video_export_lock.release()


@pytest.mark.parametrize('failure', ['file', 'metadata'])
async def test_interrupted_delete_resumes_on_reload_without_touching_unselected_exports(context, monkeypatch, failure):
    first, _, source, client = context
    vid = first['id']; kept, kept_path = export(vid); eid, path = export(vid)
    if failure == 'file':
        unlink = Path.unlink
        def fail_once(target, *args, **kwargs):
            if target == path:
                monkeypatch.setattr(Path, 'unlink', unlink)
                raise OSError('Temporary file lock')
            return unlink(target, *args, **kwargs)
        monkeypatch.setattr(Path, 'unlink', fail_once)
    else:
        write = editing_exports.atomic_json
        def fail_once(target, data):
            if target.name == 'settings.json':
                monkeypatch.setattr(editing_exports, 'atomic_json', write)
                raise OSError('Temporary write failure')
            return write(target, data)
        monkeypatch.setattr(editing_exports, 'atomic_json', fail_once)
    assert (await remove(client, vid, [eid])).status_code == 409
    assert (editing.directory(vid) / 'deleting-exports.json').is_file()
    state = editing.load(vid)
    assert [e['export_id'] for e in state['exports']] == [kept]
    assert not path.exists() and kept_path.is_file()
    assert not (editing.directory(vid) / 'deleting-exports.json').exists()
    assert source.read_bytes() == b'original source'


async def test_management_waits_for_the_shared_library_lock(context):
    first, _, _, client = context
    vid = first['id']; eid, path = export(vid)
    async with media_library.lock:
        task = asyncio.create_task(remove(client, vid, [eid]))
        await asyncio.sleep(.02)
        assert not task.done() and path.is_file()
    response = await task
    assert response.status_code == 200


async def test_pending_deletion_cannot_be_reused_in_a_new_draft(context):
    first, second, _, client = context
    vid = first['id']; eid, path = export(vid)
    asset = await timeline.register(second['id'], vid, eid, 'Cached export', path, 'export')
    doc = timeline.initial(asset)
    atomic_json(editing.directory(vid) / 'deleting-exports.json', {'export_ids': [eid]})
    response = await client.put(f"/api/videos/{second['id']}/timeline", json={'revision': 0, 'document': doc.model_dump(mode='json')})
    assert response.status_code == 422, response.text
    assert not path.exists() and not timeline.load(second['id'])
