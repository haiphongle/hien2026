"""Model recovery must be scoped, visible, and preserve paid completed clips."""
import asyncio
import uuid
from unittest.mock import AsyncMock, Mock

import aiosqlite
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from agent import config
from agent.api import requests, stickman as api
from agent.db import crud, schema
from agent.models.request import RequestCreate
from agent.models.stickman import CreateRequest, RetryRequest
from agent.models.video_model import VIDEO_MODEL_NAMES
from agent.services import flow_batch, media_jobs, speech_store, stickman, stickman_models, timeline

LITE = 'veo_3_1_i2v_lite'
OMNI = 'abra_i2v_8s'


@pytest.fixture
def failed(monkeypatch, tmp_path):
    vid = uuid.uuid4()
    project = {'id': str(uuid.uuid4()), 'user_paygate_tier': 'PAYGATE_TIER_TWO'}
    body = CreateRequest(title='Ngày bình yên', preset_id='v3_A', motion='ai', video_model=LITE,
                         scenes=[{'narration': 'Nghỉ một chút nhé.', 'action': 'The protagonist sits down.'}])
    job = {'id': str(uuid.uuid4()), 'kind': 'stickman', 'status': 'FAILED', 'video_model': LITE, 'stage': 'Clip hoạt hình · 1/1',
           'error': 'Google Flow từ chối quyền dùng model video.', 'error_code': 'VIDEO_MODEL_ACCESS_DENIED'}
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path)
    monkeypatch.setattr(api, 'saved_video', AsyncMock(return_value=({'project_id': project['id']}, body)))
    monkeypatch.setattr(api, 'project', AsyncMock(return_value=project))
    monkeypatch.setattr(api, 'ensure_idle', Mock())
    monkeypatch.setattr(api, 'flow_ready', Mock())
    monkeypatch.setattr(stickman, 'validate', AsyncMock())
    monkeypatch.setattr(timeline, 'load', lambda _: None)
    monkeypatch.setattr(media_jobs, 'latest', lambda _: job)
    monkeypatch.setattr(crud, 'list_requests', AsyncMock(return_value=[]))
    start = Mock(return_value={'status': 'QUEUED'})
    monkeypatch.setattr(stickman, 'start', start)
    speech_store.atomic_json(stickman.directory(str(vid))/'plan.json', body.model_dump(mode='json'))
    return vid, body, job, start


@pytest.mark.asyncio
async def test_denied_retry_changes_only_saved_video_and_reports_model(failed):
    vid, body, job, start = failed
    global_before = repr(config.VIDEO_MODELS)
    shown = await api.job(vid)
    assert shown['model_recovery']['retry_model'] == OMNI
    assert shown['model_recovery']['auto_switch'] is True
    await api.retry(vid, RetryRequest(job_id=job['id']))
    saved = speech_store.read_json(stickman.directory(str(vid))/'plan.json')
    assert saved['video_model'] == OMNI
    assert saved['scenes'] == body.model_dump(mode='json')['scenes']
    assert 'Veo 3.1 Lite → Omni' in start.call_args.kwargs['model_notice']
    assert 'Tự chọn model' in start.call_args.kwargs['model_notice']
    assert repr(config.VIDEO_MODELS) == global_before


@pytest.mark.asyncio
@pytest.mark.parametrize('error', ['timeout', 'UNUSUAL_ACTIVITY', 'Máy chủ đã khởi động lại.'])
async def test_other_errors_keep_model(failed, error):
    vid, _, job, start = failed
    job.update(error=error, error_code=None)
    shown = await api.job(vid)
    assert shown['model_recovery']['retry_model'] == LITE
    assert shown['model_recovery']['auto_switch'] is False
    await api.retry(vid)
    assert start.call_args.args[1].video_model == LITE


@pytest.mark.asyncio
async def test_image_permission_error_never_switches_video_model(failed):
    vid, _, job, start = failed
    job.update(stage='Ảnh cảnh · 1/1', error='PUBLIC_ERROR_MODEL_ACCESS_DENIED', error_code=None)
    assert (await api.job(vid))['model_recovery']['auto_switch'] is False
    await api.retry(vid)
    assert start.call_args.args[1].video_model == LITE


@pytest.mark.asyncio
async def test_explicit_choice_and_keep_current_are_respected(failed):
    vid, _, _, start = failed
    await api.retry(vid, RetryRequest(video_model='veo_3_1_i2v_s_fast_ultra'))
    assert start.call_args.args[1].video_model == 'veo_3_1_i2v_s_fast_ultra'
    await api.retry(vid, RetryRequest(auto_switch=False))
    assert start.call_args.args[1].video_model == LITE


@pytest.mark.asyncio
async def test_legacy_denial_tries_ordinary_lite_without_claiming_old_model(failed):
    vid, _, job, start = failed
    job.pop('video_model')
    job.pop('error_code')
    job['error'] = 'RpcError: eb1hJf failed: PUBLIC_ERROR_MODEL_ACCESS_DENIED'
    await api.retry(vid)
    assert start.call_args.args[1].video_model == LITE
    assert '→' not in start.call_args.kwargs['model_notice']


@pytest.mark.asyncio
async def test_completed_and_stale_jobs_are_not_resubmitted(failed):
    vid, _, job, start = failed
    with pytest.raises(HTTPException, match='409'):
        await api.retry(vid, RetryRequest(job_id=uuid.uuid4(), video_model=OMNI))
    job['status'] = 'COMPLETED'
    assert await api.retry(vid, RetryRequest(video_model=OMNI)) is job
    start.assert_not_called()


@pytest.mark.asyncio
async def test_model_change_is_unavailable_for_local_export_retry(failed, monkeypatch):
    vid, _, _, start = failed
    monkeypatch.setattr(timeline, 'load', lambda _: {'revision': 1})
    assert 'model_recovery' not in await api.job(vid)
    with pytest.raises(HTTPException, match='422'):
        await api.retry(vid, RetryRequest(video_model=OMNI))
    start.assert_not_called()


@pytest.mark.asyncio
async def test_model_switch_cannot_abandon_running_google_render(failed, monkeypatch):
    vid, _, _, start = failed
    monkeypatch.setattr(crud, 'list_requests', AsyncMock(return_value=[{'type': 'GENERATE_VIDEO', 'status': 'PROCESSING'}]))
    with pytest.raises(HTTPException, match='409'):
        await api.retry(vid)
    start.assert_not_called()
    assert speech_store.read_json(stickman.directory(str(vid))/'plan.json')['video_model'] == LITE


def test_contract_rejects_unknown_models_and_non_video_overrides():
    assert set(VIDEO_MODEL_NAMES) == flow_batch.VIDEO_MODELS | {OMNI}
    for data in [dict(video_model='unknown'), dict(job_id='not-a-uuid')]:
        with pytest.raises(ValidationError):
            RetryRequest(**data)
    with pytest.raises(ValidationError):
        RequestCreate(type='GENERATE_IMAGE', project_id='p', video_id='v', scene_id='s', video_model=LITE)


@pytest.mark.asyncio
async def test_batch_passes_model_and_failure_isolated_from_image_recovery(failed, monkeypatch):
    vid, _, _, _ = failed
    row = {'id': 'request', 'scene_id': 's', 'status': 'FAILED', 'error_message': 'PUBLIC_ERROR_MODEL_ACCESS_DENIED'}
    submit = AsyncMock(return_value=[row])
    monkeypatch.setattr(requests, 'create_batch', submit)
    monkeypatch.setattr(requests, 'batch_status', AsyncMock())
    monkeypatch.setattr(crud, 'list_requests', AsyncMock(return_value=[row]))
    updates = []
    with pytest.raises(ValueError, match='Chọn model thay thế'):
        await stickman.generate_images(str(vid), 'p', ['s'], 'GENERATE_VIDEO', 'VERTICAL', lambda **d: updates.append(d), 45, 15, video_model=OMNI)
    assert submit.call_args.args[0].requests[0].video_model == OMNI
    assert {'error_code': 'VIDEO_MODEL_ACCESS_DENIED'} in updates
    updates.clear()
    with pytest.raises(ValueError, match='model ảnh'):
        await stickman.generate_images(str(vid), 'p', ['s'], 'GENERATE_IMAGE', 'VERTICAL', lambda **d: updates.append(d), 30, 15)
    assert not any('error_code' in u for u in updates)


@pytest.mark.asyncio
async def test_old_database_and_requests_survive_model_migration(monkeypatch, tmp_path):
    await schema.close_db()
    path = tmp_path/'old.db'
    monkeypatch.setattr(schema, 'DB_PATH', path)
    async with aiosqlite.connect(path) as db:
        await db.executescript(schema.SCHEMA)
        await db.execute("INSERT INTO request (id,type) VALUES ('old','GENERATE_IMAGE')")
        await db.commit()
    try:
        await schema.init_db()
        await schema.init_db()
        assert (await crud.get_request('old'))['video_model'] is None
        row = await crud.create_request('GENERATE_VIDEO', video_model=OMNI)
        assert (await crud.get_request(row['id']))['video_model'] == OMNI
    finally:
        await schema.close_db()


@pytest.mark.asyncio
async def test_notice_and_model_remain_after_job_failure_and_reload(monkeypatch, tmp_path):
    monkeypatch.setattr(media_jobs, 'ROOT', tmp_path)
    monkeypatch.setattr(timeline, 'load', lambda _: None)
    monkeypatch.setattr(crud, 'update_video', AsyncMock())
    monkeypatch.setattr(crud, 'list_scenes', AsyncMock(return_value=[]))
    monkeypatch.setattr(stickman, 'generate_voices', AsyncMock(side_effect=ValueError('test failure')))
    vid = str(uuid.uuid4())
    body = CreateRequest(title='Ngày bình yên', preset_id='v3_A', motion='ai', video_model=OMNI,
                         scenes=[{'narration': 'Nghỉ một chút nhé.', 'action': 'The protagonist sits down.'}])
    message = stickman_models.notice(LITE, OMNI, True)
    queued = stickman.start(vid, body, {}, model_notice=message)
    assert queued['video_model'] == OMNI and queued['model_notice'] == message
    await asyncio.wait_for(media_jobs._tasks[vid], 5)
    loaded = speech_store.read_json(media_jobs.directory(vid)/'job.json')
    assert loaded['status'] == 'FAILED'
    assert loaded['video_model'] == OMNI and loaded['model_notice'] == message
