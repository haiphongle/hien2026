"""Muse connection management. Never returns credentials to the dashboard."""
import json
import asyncio
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from agent.api.codex_auth import require_local
from agent.services import muse_settings
from agent.services.muse_client import MuseClient, MuseError

router = APIRouter(prefix='/api/muse', tags=['muse'])
SESSION_COOKIES = ('hatch_sess', 'hatch_gw', 'hatch_vml', 'hatch_native_auth_device')


def local_session_only(request: Request, response: Response):
    try:
        require_local(request, response)
    except HTTPException as exc:
        raise HTTPException(403, 'Kết nối phiên Muse tại Cài đặt FrameWeaver trên máy này.') from exc


@router.post('/session', dependencies=[Depends(local_session_only)])
async def connect_session(request: Request):
    # Parse manually: FastAPI validation errors otherwise echo secret inputs.
    try:
        raw = await request.body()
        if len(raw) > 40_000:
            raise ValueError()
        body = json.loads(raw)
        if not isinstance(body, dict) or body.get('consent') is not True:
            raise ValueError()
        cookies = body.get('cookies')
        if not isinstance(cookies, dict) or set(cookies) != set(SESSION_COOKIES):
            raise ValueError()
        if any(not isinstance(v, str) or not 1 <= len(v) <= 8192 or
               any(ord(c) < 33 or ord(c) > 126 or c == ';' for c in v) for v in cookies.values()):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(422, 'Nhập đủ bốn giá trị phiên Muse và xác nhận lưu trên máy. Không nhập mật khẩu Gmail.') from None
    try:
        client = MuseClient()
        # This convenience endpoint only forwards to the installed local gateway.
        url = urlsplit(client.settings.base_url)
        if url.hostname not in {'127.0.0.1', 'localhost', '::1'} or url.port != 18610 or url.path:
            raise HTTPException(400, 'Nhập phiên chỉ hỗ trợ gateway nội bộ cổng 18610. Không gửi phiên tới máy chủ ngoài.')
        label = 'FrameWeaver local Muse'
        accounts = await client.request('GET', '/admin/accounts')
        matches = [a for a in accounts.get('data', []) if a.get('label') in {label, 'FlowKit local Muse'}]
        if len(matches) > 1:
            raise HTTPException(409, 'Có nhiều tài khoản FrameWeaver trong gateway. Kiểm tra Accounts trước khi cập nhật phiên.')
        if matches:
            from urllib.parse import quote
            await client.request('PATCH', '/admin/accounts/' + quote(str(matches[0]['id']), safe=''),
                                 payload={'cookies': cookies, 'enabled': True})
        else:
            await client.request('POST', '/admin/accounts', payload={'label': label, 'cookies': cookies})
        return {'saved': True, 'message': 'Đã lưu phiên trong gateway trên máy. Bấm kiểm tra kết nối, rồi thử một tác vụ nhỏ để xác nhận phiên còn hiệu lực.'}
    except MuseError as exc:
        raise HTTPException(503, str(exc)) from exc


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool | None = None
    auto_start: bool | None = None
    base_url: str | None = None
    api_key: str | None = None
    chat_model: str | None = None
    image_model: str | None = None
    video_model: str | None = None
    timeout_seconds: int | None = None


@router.get('')
async def settings():
    return muse_settings.public()


@router.patch('')
async def save(body: SettingsUpdate, request: Request, response: Response):
    if body.auto_start is not None:
        local_session_only(request, response)
    try:
        result = muse_settings.save(body.model_dump(exclude_none=True))
    except ValueError as exc:
        # Pydantic errors can include the submitted secret in `input`.
        raise HTTPException(422, 'Cấu hình Muse không hợp lệ. Dùng URL gateway (không phải muse.ai), tên model và khóa không có khoảng trắng.') from exc
    from agent.services.cli_providers import _catalog_cache
    _catalog_cache.pop('muse2api', None)
    return muse_settings.public(result)


@router.get('/gateway', dependencies=[Depends(local_session_only)])
async def gateway_status():
    from agent.services.muse_gateway import muse_gateway
    return await muse_gateway.status()


@router.post('/gateway/{operation}', dependencies=[Depends(local_session_only)], status_code=202)
async def gateway_action(operation: Literal['install', 'start', 'restart', 'stop', 'cancel', 'open-log']):
    from agent.services.muse_gateway import muse_gateway
    try:
        if operation == 'open-log':
            await asyncio.to_thread(muse_gateway.open_log)
        elif operation == 'cancel':
            await muse_gateway.shutdown()
        else:
            muse_gateway.begin(operation)
        return await muse_gateway.status()
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post('/check')
async def check():
    try:
        client = MuseClient()
        state = await client.readiness()
        models = await client.models()
        for kind in ('chat', 'image', 'video'):
            chosen = getattr(client.settings, kind + '_model')
            if not any(m['id'] == chosen and m.get('kind') == kind for m in models):
                raise MuseError(f'Model {kind} chưa đúng danh sách gateway. Kiểm tra lại trong Cài đặt.')
        return {'ready': True, 'models': models, 'accounts_available': state.get('accounts', {}).get('available'),
                'capabilities': {'text': True, 'image': True, 'image_references': 4, 'video_first_frame': True,
                                 'video_end_frame': False, 'upscale': False, 'tts': False},
                'quota': None,
                'message': 'Gateway sẵn sàng. Đây là model/alias do gateway công bố; quyền tạo và hạn mức thực tế cần kiểm tra bằng một tác vụ.'}
    except (MuseError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc


@router.get('/media/{filename}')
async def media(filename: str):
    from uuid import UUID
    from fastapi.responses import FileResponse
    from agent.services import muse_media
    try:
        stem, extension = filename.rsplit('.', 1)
        if extension not in {'png', 'mp4'} or str(UUID(stem)) != stem:
            raise ValueError()
    except ValueError as exc:
        raise HTTPException(404, 'Không tìm thấy tư liệu Muse.') from exc
    path = muse_media.local_file(stem, '.' + extension)
    if not path:
        raise HTTPException(404, 'Không tìm thấy tư liệu Muse.')
    return FileResponse(path, media_type='image/png' if extension == 'png' else 'video/mp4')
