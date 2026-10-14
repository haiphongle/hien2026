import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from agent.services import story_schedule as schedule

PID = str(uuid.uuid4())


def story(**changes):
    return schedule.SavedStory(source='Giữ lại những điều bình yên.', settings={
        'title': 'Ngày bình yên', 'preset_id': 'v3_test', 'style': 'srt_folk',
        'preview_first': True, 'music_filename': 'piano.mp3',
        'scenes': [{'narration': 'Giữ lại những điều bình yên.', 'action': 'Character A puts a cup down.'}],
    }, **changes)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(schedule, 'ROOT', tmp_path)
    monkeypatch.setattr(schedule, 'lock', asyncio.Lock())
    monkeypatch.setattr(schedule.media_library, 'ROOT', tmp_path/'library')
    monkeypatch.setattr(schedule.media_library, 'lock', asyncio.Lock())
    monkeypatch.setattr(schedule.media_jobs, 'ROOT', tmp_path/'jobs')
    monkeypatch.setattr(schedule.stickman, 'OUTPUT_DIR', tmp_path/'output')
    monkeypatch.setattr(schedule.media_library, 'OUTPUT_DIR', tmp_path/'output')
    music = tmp_path/'output'/'test'/'music'/'piano.mp3'
    music.parent.mkdir(parents=True)
    music.write_bytes(b'music')
    videos = {}
    monkeypatch.setattr(schedule.crud, 'get_project', AsyncMock(return_value={'id': PID, 'name': 'Test', 'status': 'ACTIVE'}))
    monkeypatch.setattr(schedule.crud, 'get_video', AsyncMock(side_effect=lambda vid, **kw: videos.get(vid)))
    monkeypatch.setattr(schedule.crud, 'update_video', AsyncMock())
    monkeypatch.setattr(schedule.media_jobs, 'busy', lambda: False)
    monkeypatch.setattr(schedule.media_jobs, 'latest', lambda vid: None)
    monkeypatch.setattr(schedule.stickman, 'validate', AsyncMock())
    async def created(project, body, *, video_id):
        videos[video_id] = {'id': video_id, 'project_id': PID, 'trashed_at': None}
        schedule.speech_store.atomic_json(schedule.stickman.directory(video_id)/'plan.json', body.model_dump(mode='json'))
        return {'job': {'id': 'job'}}
    create = AsyncMock(side_effect=created)
    monkeypatch.setattr(schedule.stickman, 'create_story', create)
    client = SimpleNamespace(connected=True)
    monkeypatch.setattr(schedule, 'get_flow_client', lambda: client)
    return client, create


async def due():
    item = await schedule.save(PID, story(run_at=datetime.now(timezone.utc)+timedelta(days=1)))
    schedule.write(item, run_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
    return item


def test_dates_require_timezone_and_future():
    for time in [datetime.now()+timedelta(days=1), datetime.now(timezone.utc)-timedelta(days=1)]:
        with pytest.raises(ValidationError):
            story(run_at=time)
    time = datetime.now(timezone(timedelta(hours=7)))+timedelta(days=1)
    assert story(run_at=time).run_at == time.astimezone(timezone.utc)


@pytest.mark.asyncio
async def test_multiple_drafts_keep_full_settings_and_can_be_loaded_or_deleted(runtime):
    first, second = await schedule.save(PID, story()), await schedule.save(PID, story())
    assert first['id'] != second['id'] and len(schedule.entries(PID)) == 2
    saved = schedule.read(PID, first['id'])
    assert saved['source'] == story().source and saved['settings']['music_filename'] == 'piano.mp3'
    assert saved['settings']['preview_first'] and 'settings' not in schedule.summary(saved)
    await schedule.remove(PID, second['id'])
    assert len(schedule.entries(PID)) == 1
    runtime[1].assert_not_called()


@pytest.mark.asyncio
async def test_offline_waits_then_creates_once_with_reserved_uuid(runtime):
    client, create = runtime
    item = await due()
    client.connected = False
    await schedule.tick()
    assert schedule.read(PID, item['id'])['status'] == 'WAITING'
    create.assert_not_called()
    client.connected = True
    await schedule.tick()
    saved = schedule.read(PID, item['id'])
    assert saved['status'] == 'RUNNING' and str(uuid.UUID(saved['video_id'])) == saved['video_id']
    args = create.await_args
    assert args.kwargs['video_id'] == saved['video_id']
    assert args.args[1].model_dump(mode='json') == item['settings']
    # Lost worker after a restart must fail visibly rather than submit again.
    await schedule.tick()
    assert schedule.read(PID, item['id'])['status'] == 'FAILED'
    create.assert_awaited_once()


@pytest.mark.asyncio
async def test_serial_queue_waits_for_preview_approval_and_monitors_retry(runtime, monkeypatch):
    first, second = await due(), await due()
    await schedule.tick()
    saved = next(row for row in schedule.entries(PID) if row['video_id'])
    pending = next(row for row in schedule.entries(PID) if not row['video_id'])
    job = {'kind': 'stickman', 'status': 'COMPLETED', 'progress': 100, 'result': {'preview_url': '/preview'}}
    monkeypatch.setattr(schedule.media_jobs, 'latest', lambda vid: job)
    await schedule.tick()
    assert schedule.read(PID, saved['id'])['status'] == 'REVIEW'
    assert schedule.read(PID, pending['id'])['status'] == 'SCHEDULED'
    runtime[1].assert_awaited_once()
    job.update(status='PROCESSING', progress=40)
    await schedule.tick()
    assert schedule.read(PID, saved['id'])['status'] == 'RUNNING'
    job.update(status='COMPLETED', result={'url': '/final'})
    await schedule.tick()
    assert schedule.read(PID, saved['id'])['status'] == 'COMPLETED'
    assert runtime[1].await_count == 2
    assert {first['id'], second['id']} == {saved['id'], pending['id']}


@pytest.mark.asyncio
async def test_failed_validation_is_not_retried_and_paused_draft_never_starts(runtime, monkeypatch):
    item = await due()
    monkeypatch.setattr(schedule.stickman, 'validate', AsyncMock(side_effect=ValueError('Giọng không còn trong thư viện')))
    await schedule.tick()
    await schedule.tick()
    assert schedule.read(PID, item['id'])['status'] == 'FAILED'
    runtime[1].assert_not_called()
    await schedule.reschedule(PID, item['id'], schedule.ScheduleTime())
    await schedule.tick()
    assert schedule.read(PID, item['id'])['status'] == 'DRAFT'


@pytest.mark.asyncio
async def test_interrupted_start_and_existing_media_job_do_not_duplicate(runtime, monkeypatch):
    item = await due()
    monkeypatch.setattr(schedule.media_jobs, 'busy', lambda: True)
    await schedule.tick()
    runtime[1].assert_not_called()
    schedule.write(item, status='STARTING', video_id=str(uuid.uuid4()))
    monkeypatch.setattr(schedule.media_jobs, 'busy', lambda: False)
    await schedule.tick()
    assert schedule.read(PID, item['id'])['status'] == 'FAILED'
    runtime[1].assert_not_called()


@pytest.mark.asyncio
async def test_scheduled_entry_must_be_paused_before_delete_and_running_cannot_reschedule(runtime):
    item = await due()
    with pytest.raises(ValueError, match='Hủy lịch'):
        await schedule.remove(PID, item['id'])
    await schedule.tick()
    with pytest.raises(ValueError, match='đã bắt đầu'):
        await schedule.reschedule(PID, item['id'], schedule.ScheduleTime())


@pytest.mark.asyncio
async def test_failed_start_releases_reserved_id(runtime):
    item = await due()
    runtime[1].side_effect = RuntimeError('Không tạo được cảnh')
    await schedule.tick()
    saved = schedule.read(PID, item['id'])
    assert saved['status'] == 'FAILED' and saved['video_id'] is None
    await schedule.tick()
    runtime[1].assert_awaited_once()


@pytest.mark.asyncio
async def test_cancelled_start_does_not_leave_running_entry(runtime):
    item = await due()
    runtime[1].side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await schedule.tick()
    saved = schedule.read(PID, item['id'])
    assert saved['status'] == 'FAILED' and saved['video_id'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('trashed', [True, False])
async def test_removed_preview_releases_queue_and_keeps_script_reusable(runtime, monkeypatch, trashed):
    await due()
    await schedule.tick()
    first = next(item for item in schedule.entries(PID) if item['video_id'])
    second = await due()
    preview = {'kind': 'stickman', 'status': 'COMPLETED', 'result': {'preview_url': '/preview'}}
    monkeypatch.setattr(schedule.media_jobs, 'latest', lambda vid: preview)
    original_get = schedule.crud.get_video
    async def video(vid, **kwargs):
        if vid == first['video_id']:
            return {'id': vid, 'trashed_at': '2026-01-01'} if trashed else None
        return await original_get(vid, **kwargs)
    monkeypatch.setattr(schedule.crud, 'get_video', AsyncMock(side_effect=video))
    await schedule.tick()
    saved = schedule.read(PID, first['id'])
    assert saved['status'] == 'FAILED' and saved['video_id'] is None
    assert saved['source'] == first['source'] and saved['settings'] == first['settings']
    assert schedule.read(PID, second['id'])['status'] == 'RUNNING'
    assert runtime[1].await_count == 2
    await schedule.reschedule(PID, first['id'], schedule.ScheduleTime())
    await schedule.remove(PID, first['id'])


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['reschedule', 'remove'])
async def test_partial_startup_recovers_even_before_next_scheduler_tick(runtime, monkeypatch, action):
    item = await schedule.save(PID, story())
    vid = str(uuid.uuid4())
    schedule.write(item, status='FAILED', video_id=vid, progress=35, stage='Old stage')
    monkeypatch.setattr(schedule.crud, 'get_video', AsyncMock(return_value={'id': vid, 'trashed_at': None}))
    delete = AsyncMock()
    monkeypatch.setattr(schedule.crud, 'delete_video', delete)
    if action == 'reschedule':
        await schedule.reschedule(PID, item['id'], schedule.ScheduleTime(run_at=datetime.now(timezone.utc)+timedelta(days=1)))
        saved = schedule.read(PID, item['id'])
        assert saved['video_id'] is None and saved['status'] == 'SCHEDULED'
        assert saved['progress'] == 0 and saved['stage'] == ''
    else:
        await schedule.remove(PID, item['id'])
        assert not schedule.path(PID, item['id']).exists()
    schedule.crud.update_video.assert_awaited_once_with(vid, status='FAILED')
    delete.assert_not_called()
    runtime[1].assert_not_called()


@pytest.mark.asyncio
async def test_initialized_video_survives_restart_for_manual_retry(runtime):
    item = await due()
    await schedule.tick()
    vid = schedule.read(PID, item['id'])['video_id']
    await schedule.tick()
    saved = schedule.read(PID, item['id'])
    assert saved['status'] == 'FAILED' and saved['video_id'] == vid
    assert (schedule.stickman.directory(vid)/'plan.json').is_file()
    with pytest.raises(ValueError, match='đã bắt đầu'):
        await schedule.reschedule(PID, item['id'], schedule.ScheduleTime())
    runtime[1].assert_awaited_once()
    schedule.crud.update_video.assert_not_called()


def test_all_html_recipes_are_valid_and_have_consistent_dimensions():
    from agent.story_presets import HTML_PRESETS, COSTUMES
    from agent.materials import get_material, story_category
    from agent.services import stickman_direction as direction
    assert len(HTML_PRESETS) == 12
    for preset in HTML_PRESETS:
        body = story().settings.model_copy(update={'style': preset['id']})
        assert get_material(preset['id']) and story_category(preset['id']) in {'stick', '2d', 'drawing'}
        _, _, reference = direction.reference_recipe(body, 'A', PID)
        assert COSTUMES[preset['costume']] in reference
        context = dict(direction.shot_context(body, body.scenes[0]))
        if preset['background_type'] in {'white', 'black'}:
            assert 'do not depict scenery' in context['ENVIRONMENT']


@pytest.mark.asyncio
async def test_api_save_reschedule_load_and_delete_returns_json(runtime, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from agent.api import stickman as api
    monkeypatch.setattr(api, 'project', AsyncMock(return_value={'id': PID, 'status': 'ACTIVE'}))
    app = FastAPI()
    app.include_router(api.router, prefix='/api')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        url = f'/api/projects/{PID}/stickman/schedule'
        response = await client.post(url, json=story().model_dump(mode='json'))
        assert response.status_code == 201
        sid = response.json()['id']
        saved_url = f'{url}/{sid}'
        assert (await client.get(saved_url)).json()['settings'] == story().settings.model_dump(mode='json')
        future = (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
        assert (await client.patch(saved_url, json={'run_at': future})).json()['status'] == 'SCHEDULED'
        assert (await client.delete(saved_url)).status_code == 409
        assert (await client.patch(saved_url, json={'run_at': None})).json()['status'] == 'DRAFT'
        assert (await client.delete(saved_url)).json() == {'deleted': True}
        assert (await client.get(url)).json() == []
        assert (await client.get(saved_url)).status_code == 404
    runtime[1].assert_not_called()


@pytest.mark.asyncio
async def test_shared_creation_saves_reserved_identity_without_changing_project(tmp_path, monkeypatch):
    from agent.db import crud, schema
    from agent.services import media_jobs, speech_store, stickman
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path/'schedule.db')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path/'jobs')
    monkeypatch.setattr(stickman, 'start', lambda *args: {'id': 'job', 'status': 'QUEUED'})
    await schema.init_db()
    try:
        project = await crud.create_project('Original', material='realistic')
        vid = str(uuid.uuid4())
        result = await stickman.create_story(project, story().settings, video_id=vid)
        assert result['video_id'] == vid
        assert (await crud.get_video(vid))['project_id'] == project['id']
        assert len(await crud.list_scenes(vid)) == 1
        assert speech_store.read_json(stickman.directory(vid)/'plan.json')['style'] == 'srt_folk'
        assert (await crud.get_project(project['id']))['material'] == 'realistic'
        assert not await crud.list_requests(video_id=vid)
    finally:
        await schema.close_db()


@pytest.mark.asyncio
async def test_restart_with_partial_database_video_preserves_data_and_allows_rescheduling(tmp_path, monkeypatch):
    from agent.db import crud, schema
    from agent.services import media_jobs, media_library, stickman
    monkeypatch.setattr(schema, 'DB_PATH', tmp_path/'restart.db')
    monkeypatch.setattr(schedule, 'ROOT', tmp_path/'schedule')
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path/'jobs')
    monkeypatch.setattr(schedule, 'lock', asyncio.Lock())
    monkeypatch.setattr(media_library, 'lock', asyncio.Lock())
    start = AsyncMock()
    monkeypatch.setattr(stickman, 'start', start)
    await schema.init_db()
    try:
        project = await crud.create_project('Interrupted project')
        video = await crud.create_video(project['id'], 'Partial video')
        scene = await crud.create_scene(video['id'], 1, 'A cup rests on the table.')
        body = story()
        body.settings.music_filename = None
        saved = await schedule.save(project['id'], body)
        schedule.write(saved, status='STARTING', video_id=video['id'])
        await schedule.tick()
        recovered = schedule.read(project['id'], saved['id'])
        assert recovered['status'] == 'FAILED' and recovered['video_id'] is None
        assert (await crud.get_video(video['id']))['status'] == 'FAILED'
        assert [row['id'] for row in await crud.list_scenes(video['id'])] == [scene['id']]
        assert not await crud.list_requests(video_id=video['id'])
        await schedule.reschedule(project['id'], saved['id'], schedule.ScheduleTime(
            run_at=datetime.now(timezone.utc)+timedelta(days=1)))
        assert schedule.read(project['id'], saved['id'])['status'] == 'SCHEDULED'
        await schedule.reschedule(project['id'], saved['id'], schedule.ScheduleTime())
        await schedule.remove(project['id'], saved['id'])
        assert await crud.get_video(video['id'])
        start.assert_not_called()
    finally:
        await schema.close_db()
