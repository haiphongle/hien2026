"""Library lifecycle, source identity, references and deletion isolation."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
import uuid

import httpx
import pytest
from fastapi import FastAPI

from agent.api import media_library as api, music, pacing as pacing_api, timeline as timeline_api, videos as videos_api
from agent.db import crud, schema
from agent.services import audio_mix, audio_mix_jobs, editing, media_jobs, media_library as library, pacing, speech_store, stickman, story_schedule, timeline, video_assembly
from agent.services.speech_store import atomic_json
from agent.utils.slugify import slugify


@pytest.fixture
async def context(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path / 'db.sqlite')
    for module in [library, timeline, pacing, editing, media_jobs, speech_store, audio_mix_jobs, story_schedule]:
        monkeypatch.setattr(module, 'ROOT', tmp_path / module.__name__.split('.')[-1])
    for module in [library, timeline_api, video_assembly, audio_mix, stickman]:
        monkeypatch.setattr(module, 'OUTPUT_DIR', tmp_path)
    monkeypatch.setattr(library, 'lock', asyncio.Lock())
    monkeypatch.setattr(story_schedule, 'lock', asyncio.Lock())
    async def probe(path):
        return dict(width=160 if path.suffix == '.mp4' else 0, height=90 if path.suffix == '.mp4' else 0,
                    duration=4., fps=30., has_audio=True)
    monkeypatch.setattr(pacing, 'probe', probe)
    monkeypatch.setattr(timeline_api, 'original_path', lambda key: media_jobs.directory(key) / 'source.mp4')
    monkeypatch.setattr(pacing_api, 'original_path', timeline_api.original_path)
    await schema.init_db()
    project = await crud.create_project(name='Library test', material='realistic')
    first = await crud.create_video(project['id'], 'Source A')
    second = await crud.create_video(project['id'], 'Source B')
    for row in [first, second]:
        path = media_jobs.directory(row['id']) / 'source.mp4'
        path.parent.mkdir(parents=True); path.write_bytes(row['id'].encode())
    music_dir = tmp_path / slugify(project['name']) / 'music'
    music_dir.mkdir(parents=True); (music_dir / 'saved.wav').write_bytes(b'background music')
    monkeypatch.setattr(music, '_music_directory', AsyncMock(return_value=music_dir))
    effect = str(uuid.uuid4()); sound = pacing.asset_path(first['id'], effect)
    sound.parent.mkdir(parents=True); sound.write_bytes(b'uploaded sound')
    atomic_json(sound.with_suffix('.json'), dict(id=effect, name='My effect', duration=2, builtin=False))
    app = FastAPI()
    for router in [api.router, timeline_api.router, music.router, videos_api.router, pacing_api.router]:
        app.include_router(router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        yield project, first, second, music_dir, sound, client
    await media_jobs.shutdown()
    await schema.close_db()


async def state(client, vid):
    response = await client.get(f'/api/videos/{vid}/timeline')
    assert response.status_code == 200, response.text
    return response.json()


def saved_story():
    return story_schedule.SavedStory(source='Giữ lại những điều bình yên.', settings={
        'title': 'Kịch bản hẹn giờ', 'preset_id': 'test', 'music_filename': 'saved.wav',
        'scenes': [{'narration': 'Giữ lại những điều bình yên.', 'action': 'Character A puts a cup down.'}],
    })


@pytest.mark.parametrize('status', ['DRAFT', 'SCHEDULED', 'WAITING', 'REVIEW', 'FAILED', 'CANCELLED'])
async def test_saved_script_music_blocks_trash_and_purge_until_removed(context, status):
    project, first, _, directory, _, client = context
    audio = next(a for a in (await state(client, first['id']))['assets'] if a['category'] == 'music')
    pid = project['id']
    story = await story_schedule.save(pid, saved_story())
    story_schedule.write(story, status=status)
    url = f"/api/projects/{pid}/library/items/{audio['management_id']}"
    info = (await client.get(url)).json()
    assert [use['id'] for use in info['uses']] == [story['id']]
    assert 'Kịch bản đã lưu' in info['uses'][0]['name']
    assert (await client.post(url+'/trash')).status_code == 409
    # A trashed file from an older version is protected from permanent deletion too.
    records = library.audio_records(pid)
    records[audio['management_id']]['trashed_at'] = '2026-01-01'
    atomic_json(library.manifest(pid), records)
    assert (await client.request('DELETE', url, json={'name': audio['name']})).status_code == 409
    assert (directory/'saved.wav').exists()
    story_schedule.write(story, status='DRAFT')
    await story_schedule.remove(pid, story['id'])
    assert (await client.request('DELETE', url, json={'name': audio['name']})).status_code == 200


async def test_completed_and_other_project_stories_do_not_pin_music(context):
    project, first, _, _, _, client = context
    audio = next(a for a in (await state(client, first['id']))['assets'] if a['category'] == 'music')
    story = await story_schedule.save(project['id'], saved_story())
    story_schedule.write(story, status='COMPLETED')
    other = await crud.create_project('Other')
    body = saved_story().model_dump(mode='json')
    story_schedule.write(dict(id=str(uuid.uuid4()), project_id=other['id'], created_at='2026-01-01',
                              status='DRAFT', video_id=None, **body))
    url = f"/api/projects/{project['id']}/library/items/{audio['management_id']}"
    assert (await client.get(url)).json()['uses'] == []
    assert (await client.post(url+'/trash')).status_code == 200


async def test_saving_waits_for_library_change_and_rejects_deleted_music(context):
    project, first, _, _, _, client = context
    audio = next(a for a in (await state(client, first['id']))['assets'] if a['category'] == 'music')
    async with library.lock:
        saving = asyncio.create_task(story_schedule.save(project['id'], saved_story()))
        try:
            done, _ = await asyncio.wait({saving}, timeout=.05)
            assert not done
            records = library.audio_records(project['id'])
            records[audio['management_id']]['trashed_at'] = '2026-01-01'
            atomic_json(library.manifest(project['id']), records)
        except BaseException:
            saving.cancel()
            await asyncio.gather(saving, return_exceptions=True)
            raise
    with pytest.raises(ValueError, match='thùng rác'):
        await asyncio.wait_for(saving, 2)
    assert story_schedule.entries(project['id']) == []


async def test_missing_music_cannot_be_saved_or_rescheduled_but_draft_can_be_cancelled(context):
    from datetime import datetime, timedelta, timezone
    project, _, _, directory, _, _ = context
    story = await story_schedule.save(project['id'], saved_story())
    (directory/'saved.wav').unlink()
    with pytest.raises(ValueError, match='Không tìm thấy nhạc'):
        await story_schedule.save(project['id'], saved_story())
    with pytest.raises(ValueError, match='Không tìm thấy nhạc'):
        await story_schedule.reschedule(project['id'], story['id'], story_schedule.ScheduleTime(
            run_at=datetime.now(timezone.utc)+timedelta(days=1)))
    await story_schedule.reschedule(project['id'], story['id'], story_schedule.ScheduleTime())
    await story_schedule.remove(project['id'], story['id'])


async def test_trashing_preview_releases_schedule_and_restore_does_not_resume_it(context):
    project, first, _, _, _, client = context
    pid, vid = project['id'], first['id']
    saved = await story_schedule.save(pid, saved_story())
    story_schedule.write(saved, status='REVIEW', video_id=vid)
    atomic_json(media_jobs.directory(vid)/'job.json', dict(
        id=str(uuid.uuid4()), kind='stickman', status='COMPLETED', result={'preview_url': '/preview'}))
    original = (media_jobs.directory(vid)/'source.mp4').read_bytes()
    url = f'/api/projects/{pid}/library/items/{vid}'
    assert (await client.post(url+'/trash')).status_code == 200
    await story_schedule.tick()
    recovered = story_schedule.read(pid, saved['id'])
    assert recovered['status'] == 'FAILED' and recovered['video_id'] is None
    assert 'Thùng rác' in recovered['error']
    assert (await client.post(url+'/restore')).status_code == 200
    await story_schedule.tick()
    assert story_schedule.read(pid, saved['id'])['status'] == 'FAILED'
    assert (media_jobs.directory(vid)/'source.mp4').read_bytes() == original


async def test_rename_trash_restore_keeps_source_and_timeline_identity(context):
    project, first, _, _, _, client = context
    vid = first['id']; prefix = f"/api/projects/{project['id']}/library/items/{vid}"
    before = await state(client, vid)
    path = media_jobs.directory(vid) / 'source.mp4'
    original = path.read_bytes(), path.stat().st_mtime_ns
    assert (await client.patch(prefix, json={'name': 'Phúc · bản mới'})).status_code == 200
    renamed = await state(client, vid)
    source = next(a for a in renamed['assets'] if a['owner'] == vid and a['ref'] == 'original')
    assert source['name'] == 'Phúc · bản mới' and source['management_id'] == vid
    assert renamed['document'] == before['document']
    assert (await client.request('DELETE', prefix, json={'name': source['name']})).status_code == 409
    assert (await client.post(prefix + '/trash')).status_code == 200
    assert await crud.get_video(vid) is None
    assert vid not in [v['id'] for v in await crud.list_videos(project['id'])]
    assert (await client.get(f'/api/videos/{vid}/timeline')).status_code == 404
    trash = (await client.get(f"/api/projects/{project['id']}/library/trash")).json()
    assert [v['id'] for v in trash] == [vid] and 'path' not in trash[0]
    assert (path.read_bytes(), path.stat().st_mtime_ns) == original
    assert (await client.post(prefix + '/restore')).status_code == 200
    restored = await state(client, vid)
    assert restored['document'] == before['document']
    assert (path.read_bytes(), path.stat().st_mtime_ns) == original


async def test_cross_draft_reference_blocks_deletion_even_when_draft_is_trashed(context):
    project, first, second, _, _, client = context
    draft = await state(client, second['id'])
    source = next(a for a in draft['assets'] if a['owner'] == first['id'] and a['ref'] == 'original')
    for clip in draft['document']['clips']:
        clip['asset_id'] = source['id']
    saved = await client.put(f"/api/videos/{second['id']}/timeline", json={k: draft[k] for k in ['revision', 'document']})
    assert saved.status_code == 200, saved.text
    prefix = f"/api/projects/{project['id']}/library/items/"
    info = (await client.get(prefix + first['id'])).json()
    assert [use['id'] for use in info['uses']] == [second['id']]
    assert (await client.post(prefix + first['id'] + '/trash')).status_code == 409
    assert (await client.post(prefix + second['id'] + '/trash')).status_code == 200
    blocked = await client.post(prefix + first['id'] + '/trash')
    assert blocked.status_code == 409 and second['title'] in blocked.text


async def test_audio_alias_trash_restore_and_permanent_delete_are_shared(context):
    project, first, second, directory, _, client = context
    initial = await state(client, first['id'])
    audio = next(a for a in initial['assets'] if a['category'] == 'music')
    prefix = f"/api/projects/{project['id']}/library/items/{audio['management_id']}"
    path = directory / 'saved.wav'; content = path.read_bytes()
    assert (await client.patch(prefix, json={'name': 'Nhạc thư giãn'})).status_code == 200
    other = await state(client, second['id'])
    shared = next(a for a in other['assets'] if a['category'] == 'music')
    assert shared['name'] == 'Nhạc thư giãn' and shared['management_id'] == audio['management_id']
    assert (await client.post(prefix + '/trash')).status_code == 200
    assert not any(a['category'] == 'music' for a in (await state(client, first['id']))['assets'])
    assert (await client.get(f"/api/music/files/{project['id']}")).json() == []
    assert (await client.get(f"/api/videos/{first['id']}/timeline/assets/{audio['id']}/file")).status_code == 422
    assert path.read_bytes() == content
    assert (await client.post(prefix + '/restore')).status_code == 200
    restored = next(a for a in (await state(client, first['id']))['assets'] if a['category'] == 'music')
    assert restored['id'] == audio['id']
    assert (await client.post(prefix + '/trash')).status_code == 200
    assert (await client.request('DELETE', prefix, json={'name': 'wrong'})).status_code == 409
    assert (await client.request('DELETE', prefix, json={'name': 'Nhạc thư giãn'})).status_code == 200
    assert not path.exists() and await crud.get_video(first['id'])


async def test_audio_in_current_timeline_blocks_trash_and_stale_save_is_rejected(context):
    project, first, _, _, _, client = context
    vid = first['id']; draft = await state(client, vid)
    audio = next(a for a in draft['assets'] if a['category'] == 'music')
    clip = {**draft['document']['clips'][0], 'id': str(uuid.uuid4()), 'asset_id': audio['id'],
            'track_id': draft['document']['tracks'][2]['id'], 'group': None}
    draft['document']['clips'].append(clip)
    url = f'/api/videos/{vid}/timeline'
    assert (await client.put(url, json={k: draft[k] for k in ['revision', 'document']})).status_code == 200
    prefix = f"/api/projects/{project['id']}/library/items/{audio['management_id']}"
    assert (await client.post(prefix + '/trash')).status_code == 409
    draft['document']['clips'].pop()
    assert (await client.put(url, json={'revision': 1, 'document': draft['document']})).status_code == 200
    assert (await client.post(prefix + '/trash')).status_code == 200
    draft['document']['clips'].append(clip)
    assert (await client.put(url, json={'revision': 2, 'document': draft['document']})).status_code == 422


async def test_purge_removes_only_owned_files_and_preserves_shared_music(context):
    project, first, second, music_dir, _, client = context
    vid = first['id']; await state(client, vid)
    for path in [timeline.directory(vid) / 'proxies' / 'test.mp4', editing.directory(vid) / 'exports' / 'test.mp4',
                 speech_store.video_dir(vid) / 'audio' / 'test.wav', video_assembly.assembled_path(vid)]:
        path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'owned file')
    prefix = f"/api/projects/{project['id']}/library/items/{vid}"
    assert (await client.post(prefix + '/trash')).status_code == 200
    assert (await client.request('DELETE', prefix, json={'name': first['title']})).status_code == 200
    assert await crud.get_video(vid, include_trashed=True) is None
    assert not any(path.exists() for path in library.video_paths(project, vid))
    assert (media_jobs.directory(second['id']) / 'source.mp4').exists()
    assert (music_dir / 'saved.wav').exists()
    music_asset = next(a for a in (await state(client, second['id']))['assets'] if a['category'] == 'music')
    assert (await client.get(f"/api/projects/{project['id']}/library/items/{music_asset['management_id']}")).status_code == 200
    assert (await client.get(f"/api/projects/{project['id']}/library/trash")).json() == []


async def test_uploaded_effect_lifecycle_and_foreign_project_isolation(context):
    project, first, _, _, sound, client = context
    assets = (await state(client, first['id']))['assets']
    effect = next(a for a in assets if a['ref'] == sound.stem)
    assert all(not a.get('management_id') for a in assets if a['category'] == 'sfx' and a['ref'] != sound.stem)
    prefix = f"/api/projects/{project['id']}/library/items/{effect['management_id']}"
    other = await crud.create_project(name='Other', material='realistic')
    foreign = f"/api/projects/{other['id']}/library/items/{first['id']}"
    assert (await client.patch(foreign, json={'name': 'not allowed'})).status_code == 404
    assert (await client.post(prefix + '/trash')).status_code == 200
    assert (await client.post(prefix + '/restore')).status_code == 200
    assert (await client.post(prefix + '/trash')).status_code == 200
    assert (await client.request('DELETE', prefix, json={'name': effect['name']})).status_code == 200
    assert not sound.exists() and not sound.with_suffix('.json').exists()


async def test_migration_and_active_job_guard(context, monkeypatch):
    project, first, _, _, _, client = context
    db = await schema.get_db()
    await db.execute('ALTER TABLE video DROP COLUMN trashed_at'); await db.commit()
    await schema.init_db()
    assert (await crud.get_video(first['id']))['trashed_at'] is None
    from agent.api import speech
    def busy(_):
        from fastapi import HTTPException
        raise HTTPException(409, 'Đang xuất video')
    monkeypatch.setattr(speech, 'ensure_idle', busy)
    response = await client.post(f"/api/projects/{project['id']}/library/items/{first['id']}/trash")
    assert response.status_code == 409
    assert await crud.get_video(first['id'])


async def test_old_delete_endpoint_is_recoverable_and_purge_failure_stays_in_trash(context, monkeypatch):
    project, first, _, _, _, client = context
    vid = first['id']
    assert (await client.delete(f'/api/videos/{vid}')).status_code == 200
    assert await crud.get_video(vid, include_trashed=True)
    assert (media_jobs.directory(vid) / 'source.mp4').exists()
    def fail(_):
        raise PermissionError('test permission failure')
    monkeypatch.setattr(library.shutil, 'rmtree', fail)
    url = f"/api/projects/{project['id']}/library/items/{vid}"
    response = await client.request('DELETE', url, json={'name': first['title']})
    assert response.status_code == 409
    assert (await crud.get_video(vid, include_trashed=True))['trashed_at']
    assert (media_jobs.directory(vid) / 'source.mp4').exists()


async def test_audio_symlink_and_missing_file_do_not_delete_external_data(context):
    project, first, _, directory, _, client = context
    asset = next(a for a in (await state(client, first['id']))['assets'] if a['category'] == 'music')
    url = f"/api/projects/{project['id']}/library/items/{asset['management_id']}"
    path = directory / 'saved.wav'; path.unlink()
    assert (await client.post(url + '/trash')).status_code == 200
    assert (await client.post(url + '/restore')).status_code == 409
    outside = directory.parent.parent / 'external.wav'; outside.write_bytes(b'private')
    path.symlink_to(outside)
    response = await client.request('DELETE', url, json={'name': asset['name']})
    assert response.status_code == 422 and outside.read_bytes() == b'private'


async def test_long_existing_title_and_invalid_rename(context):
    project, first, _, _, _, client = context
    vid = first['id']; name = ' ' + 'Long existing filename ' * 10 + ' '
    await crud.update_video(vid, title=name)
    url = f"/api/projects/{project['id']}/library/items/{vid}"
    for invalid in ['   ', 'a\nb', 'a' * 121]:
        assert (await client.patch(url, json={'name': invalid})).status_code == 422
    assert (await client.post(url + '/trash')).status_code == 200
    assert (await client.request('DELETE', url, json={'name': name})).status_code == 200


async def test_shared_music_survives_deletion_of_its_original_catalog_owner(context):
    project, first, second, directory, _, client = context
    draft = await state(client, second['id'])
    music_asset = await timeline.register(second['id'], first['id'], 'music:saved.wav', 'Music', directory / 'saved.wav', 'music')
    draft['document']['clips'].append({**draft['document']['clips'][0], 'id': str(uuid.uuid4()),
        'asset_id': music_asset['id'], 'track_id': draft['document']['tracks'][2]['id'], 'group': None})
    url = f"/api/videos/{second['id']}/timeline"
    assert (await client.put(url, json={k: draft[k] for k in ['revision', 'document']})).status_code == 200
    prefix = f"/api/projects/{project['id']}/library/items/{first['id']}"
    assert (await client.post(prefix + '/trash')).status_code == 200
    assert (await client.request('DELETE', prefix, json={'name': first['title']})).status_code == 200
    assert (await client.get(f"{url}/assets/{music_asset['id']}/file")).content == b'background music'
    assert (await client.put(url, json={'revision': 1, 'document': draft['document']})).status_code == 200


async def test_cleared_legacy_music_does_not_break_usage_checks(context):
    project, first, _, directory, _, client = context
    audio = next(a for a in (await state(client, first['id']))['assets'] if a['category'] == 'music')
    atomic_json(directory.parent / 'audio_mix' / f"{first['id']}.json", {'music': None})
    url = f"/api/projects/{project['id']}/library/items/{audio['management_id']}"
    assert (await client.get(url)).json()['uses'] == []
    assert (await client.post(url + '/trash')).status_code == 200


async def test_partial_purge_cannot_restore_and_can_retry(context, monkeypatch):
    project, first, second, directory, _, client = context
    vid = first['id']; await state(client, vid)
    url = f"/api/projects/{project['id']}/library/items/{vid}"
    assert (await client.post(url + '/trash')).status_code == 200
    remove = library.shutil.rmtree
    def fail_after_source(path):
        if path == timeline.directory(vid):
            raise PermissionError('test interrupted deletion')
        remove(path)
    monkeypatch.setattr(library.shutil, 'rmtree', fail_after_source)
    assert (await client.request('DELETE', url, json={'name': first['title']})).status_code == 409
    assert not media_jobs.directory(vid).exists()
    assert (await client.post(url + '/restore')).status_code == 409
    assert (await client.get(url)).json()['restore_available'] is False
    monkeypatch.setattr(library.shutil, 'rmtree', remove)
    assert (await client.request('DELETE', url, json={'name': first['title']})).status_code == 200
    assert await crud.get_video(vid, include_trashed=True) is None
    assert (media_jobs.directory(second['id']) / 'source.mp4').exists()
    assert (directory / 'saved.wav').exists()


async def test_corrupt_library_metadata_fails_closed(context):
    project, first, _, directory, _, client = context
    audio = next(a for a in (await state(client, first['id']))['assets'] if a['category'] == 'music')
    url = f"/api/projects/{project['id']}/library/items/{audio['management_id']}"
    assert (await client.post(url + '/trash')).status_code == 200
    library.manifest(project['id']).write_text('{broken')
    response = await client.get(f"/api/music/files/{project['id']}/saved.wav")
    assert response.status_code == 409
    assert (directory / 'saved.wav').exists()
    assert library.manifest(project['id']).read_text() == '{broken'


async def test_old_audio_tools_cannot_reuse_trashed_music_or_effects(context):
    project, first, _, _, sound, client = context
    assets = (await state(client, first['id']))['assets']
    for asset in [a for a in assets if a['category'] == 'music' or a['ref'] == sound.stem]:
        url = f"/api/projects/{project['id']}/library/items/{asset['management_id']}/trash"
        assert (await client.post(url)).status_code == 200
    pacing_url = f"/api/videos/{first['id']}/pacing"
    draft = (await client.get(pacing_url)).json()
    draft['draft']['effects'] = [dict(id=str(uuid.uuid4()), asset_id=sound.stem)]
    response = await client.put(pacing_url, json={k: draft[k] for k in ['revision', 'draft']})
    assert response.status_code == 422 and 'thùng rác' in response.text
    with pytest.raises(audio_mix.AudioMixError, match='thùng rác'):
        await audio_mix.finish_audio_mix(first['id'], None, {'music': {'filename': 'saved.wav'}})


async def test_job_setup_and_trash_cannot_interleave(context, monkeypatch):
    project, first, _, _, _, client = context
    asset = next(a for a in (await state(client, first['id']))['assets'] if a['category'] == 'music')
    validated, release, finish = asyncio.Event(), asyncio.Event(), asyncio.Event()
    original = timeline_api.checked_asset
    async def checked(vid, aid):
        data = await original(vid, aid)
        validated.set()
        await release.wait()
        return data
    async def visualize(*args):
        await finish.wait()
    monkeypatch.setattr(timeline_api, 'checked_asset', checked)
    monkeypatch.setattr(timeline, 'visualize', visualize)
    job = asyncio.create_task(client.post(f"/api/videos/{first['id']}/timeline/visuals", json={'asset_ids': [asset['id']]}))
    deletion = None
    try:
        await asyncio.wait_for(validated.wait(), 2)
        deletion = asyncio.create_task(client.post(f"/api/projects/{project['id']}/library/items/{asset['management_id']}/trash"))
        done, _ = await asyncio.wait({deletion}, timeout=.05)
        assert not done
        release.set()
        assert (await asyncio.wait_for(job, 2)).status_code == 202
        assert (await asyncio.wait_for(deletion, 2)).status_code == 409
        assert not library.audio_records(project['id'])[asset['management_id']]['trashed_at']
    finally:
        release.set(); finish.set()
        await asyncio.gather(job, *([deletion] if deletion else []), return_exceptions=True)
        await media_jobs.shutdown()


async def test_audio_restore_waits_for_its_parent_video(context):
    project, first, _, _, sound, client = context
    asset = next(a for a in (await state(client, first['id']))['assets'] if a['ref'] == sound.stem)
    prefix = f"/api/projects/{project['id']}/library/items/"
    assert (await client.post(prefix + asset['management_id'] + '/trash')).status_code == 200
    assert (await client.post(prefix + first['id'] + '/trash')).status_code == 200
    assert (await client.post(prefix + asset['management_id'] + '/restore')).status_code == 409
    assert (await client.post(prefix + first['id'] + '/restore')).status_code == 200
    assert (await client.post(prefix + asset['management_id'] + '/restore')).status_code == 200


async def test_purge_preserves_music_in_a_shared_legacy_project_directory(context):
    project, first, _, directory, _, client = context
    audio = next(a for a in (await state(client, first['id']))['assets'] if a['category'] == 'music')
    await crud.create_project(name='Library TEST', material='realistic')
    url = f"/api/projects/{project['id']}/library/items/{audio['management_id']}"
    assert (await client.post(url + '/trash')).status_code == 200
    response = await client.request('DELETE', url, json={'name': audio['name']})
    assert response.status_code == 409 and 'dùng chung' in response.text
    assert (directory / 'saved.wav').read_bytes() == b'background music'
