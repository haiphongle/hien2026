"""Local account controls for the same Codex CLI used by FrameWeaver's AI jobs."""
import ipaddress
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from agent.services.codex_auth import codex_auth
from agent.services.codex_install import codex_installer


def loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_local(request: Request, response: Response):
    # The general API permits remote dashboards. Account changes affect the Mac's
    # shared Codex session, so this surface is restricted to the local dashboard.
    allowed = request.client and loopback(request.client.host) and loopback(request.url.hostname)
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value:
            try:
                url = urlsplit(value)
                allowed = allowed and url.scheme in {"http", "https"} and loopback(url.hostname)
            except ValueError:
                allowed = False
    for header in ("forwarded", "x-forwarded-for", "x-forwarded-host", "cf-connecting-ip"):
        if request.headers.get(header):
            allowed = False
    if not allowed or request.headers.get("x-flowkit-local-auth") != "1":
        raise HTTPException(403, "Mở http://127.0.0.1:5173/settings trên máy chạy FrameWeaver để kết nối Codex.")
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(prefix="/api/providers/codex", tags=["providers"], dependencies=[Depends(require_local)])


@router.get("/auth")
async def status(refresh: bool = False):
    installation = codex_installer.status()
    # Installation must immediately invalidate a cached "missing CLI" account state.
    refresh = refresh or (installation['installed'] and codex_auth.account.get('state') == 'missing')
    return {**await codex_auth.status(refresh), 'installation': installation}


class InstallCodex(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirm: Literal[True]


@router.post('/installation', status_code=202)
async def install(body: InstallCodex):
    try:
        if codex_auth.active():
            raise ValueError('Đợi hoàn tất hoặc hủy đăng nhập trước khi cài thành phần.')
        return codex_installer.start()
    except (ValueError, OSError) as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete('/installation')
async def cancel_install():
    return await codex_installer.cancel()


@router.post("/login")
async def start_login():
    try:
        return await codex_auth.start()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(504, "Codex chưa mở được trang đăng nhập. Kiểm tra lại trạng thái rồi thử lại.") from exc


class CancelLogin(BaseModel):
    login_id: str


@router.post("/login/cancel")
async def cancel_login(body: CancelLogin):
    try:
        return await codex_auth.cancel(body.login_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
