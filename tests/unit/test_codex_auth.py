"""Account lifecycle checks; no real sign-in or credential changes."""
import asyncio
import json
import signal
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI

from agent.api import codex_auth as api
from agent.services import codex_auth as service


class Session:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.account = {"type": "chatgpt", "email": "private@example.com", "accessToken": "secret"}
        self.failure = None
        self.url = "https://auth.openai.com/oauth/authorize?state=private-state"
        self.finish = asyncio.Event()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed += 1

    async def request(self, method, params=None, **kwargs):
        self.calls.append((method, params))
        if method == "account/read":
            return {"account": self.account}
        if method == "account/rateLimits/read" and self.failure:
            raise service.CodexRPCError(self.failure)
        if method == "account/login/start":
            return {"loginId": "upstream-id", "authUrl": self.url}
        return {}

    async def wait_login(self, login_id):
        assert login_id == "upstream-id"
        await self.finish.wait()


@pytest.fixture
def auth(monkeypatch):
    instance = service.CodexAuth()
    session = Session()
    monkeypatch.setattr(service, "executable", lambda provider: "/same/codex")
    monkeypatch.setattr(service, "CodexSession", lambda binary: session)
    return instance, session


@pytest.mark.asyncio
async def test_connected_checks_account_without_inference_or_exposing_secrets(auth):
    instance, session = auth
    state = await instance.status()
    assert state["state"] == "connected"
    assert [method for method, _ in session.calls] == ["account/read", "account/rateLimits/read"]
    assert "secret" not in json.dumps(state) and "private@example.com" not in json.dumps(state)
    await asyncio.gather(instance.status(), instance.status())
    assert session.closed == 1  # cached and coalesced, no subprocess per poll


@pytest.mark.asyncio
@pytest.mark.parametrize("account,expected", [(None, "signed_out"), ({"type": "apiKey"}, "api_key")])
async def test_no_false_chatgpt_badge(auth, account, expected):
    instance, session = auth
    session.account = account
    assert (await instance.status())["state"] == expected
    assert len(session.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    ("401 Unauthorized secret-token", "expired"),
    ("refresh_token_reused secret-token", "expired"),
    ("connection timeout secret-token", "unknown"),
    ("429 rate limit exceeded secret-token", "unknown"),
])
async def test_auth_failures_are_distinct_from_network_or_quota(auth, error, expected):
    instance, session = auth
    session.failure = error
    state = await instance.status()
    assert state["state"] == expected
    assert "secret-token" not in json.dumps(state)


@pytest.mark.asyncio
async def test_missing_cli(auth, monkeypatch):
    instance, session = auth
    monkeypatch.setattr(service, "executable", lambda provider: None)
    assert (await instance.status())["state"] == "missing"
    with pytest.raises(ValueError):
        await instance.start()
    assert not session.calls


@pytest.mark.asyncio
async def test_repeated_start_reuses_login_and_cancel_preserves_saved_session(auth):
    instance, session = auth
    first, second = await asyncio.gather(instance.start(), instance.start())
    assert first["login"]["id"] == second["login"]["id"]
    assert first["login"]["status"] == "pending"
    assert sum(method == "account/login/start" for method, _ in session.calls) == 1
    await instance.status(refresh=True)
    assert len(session.calls) == 1  # no refresh racing with an active sign-in
    with pytest.raises(ValueError):
        await instance.cancel("stale-login")
    result = await instance.cancel(first["login"]["id"])
    assert result["login"]["status"] == "cancelled"
    assert result["login"]["auth_url"] is None
    assert result["state"] == "connected"
    assert ("account/login/cancel", {"loginId": "upstream-id"}) in session.calls
    assert all(method != "account/logout" for method, _ in session.calls)
    assert session.closed == 2


@pytest.mark.asyncio
async def test_login_success_removes_url_and_rechecks_account(auth):
    instance, session = auth
    await instance.start()
    session.finish.set()
    await instance.task
    result = await instance.status()
    assert result["login"]["status"] == "completed"
    assert result["login"]["auth_url"] is None
    assert result["state"] == "connected"
    assert all(method != "account/login/cancel" for method, _ in session.calls)


@pytest.mark.asyncio
async def test_login_deadline_and_shutdown_close_callback_listener(auth, monkeypatch):
    instance, session = auth
    monkeypatch.setattr(service, "LOGIN_TIMEOUT", 0.02)
    await instance.start()
    await instance.task
    assert instance.snapshot()["login"]["status"] == "expired"
    assert session.closed == 1
    await instance.start()
    await instance.shutdown()
    assert not instance.active()
    assert instance.snapshot()["login"]["auth_url"] is None
    assert session.closed == 2


@pytest.mark.parametrize("url", [
    "http://auth.openai.com/oauth", "https://auth.openai.com.evil.test/oauth",
    "https://chatgpt.com@evil.test/", "https://evil@chatgpt.com/", "javascript:alert(1)",
    "https://auth.openai.com:444/", "https://[broken", None,
])
def test_auth_url_rejects_untrusted_destinations(url):
    assert not service.trusted_auth_url(url)


@pytest.mark.asyncio
async def test_bad_login_response_does_not_escape_to_frontend(auth):
    instance, session = auth
    session.url = "https://evil.test/secret"
    result = await instance.start()
    assert result["login"]["status"] == "failed"
    assert result["login"]["auth_url"] is None
    assert "evil.test" not in json.dumps(result)
    assert session.closed == 1


@pytest.mark.asyncio
async def test_rpc_notification_before_response_and_process_cleanup(monkeypatch):
    proc = MagicMock(pid=987654, returncode=None)
    proc.stdout = asyncio.StreamReader()
    proc.stdin.drain = AsyncMock()
    proc.wait = AsyncMock()
    # Completion can race ahead of the login/start response.
    for message in [
        {"id": 1, "result": {}},
        {"method": "account/login/completed", "params": {"loginId": "id", "success": True}},
        {"id": 2, "result": {"loginId": "id"}},
    ]:
        proc.stdout.feed_data((json.dumps(message) + "\n").encode())
    spawn = AsyncMock(return_value=proc)
    kill = MagicMock()
    monkeypatch.setattr(service.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(service.os, "killpg", kill)
    async with service.CodexSession("/exact/codex") as client:
        await client.request("account/login/start", {"type": "chatgpt"})
        await asyncio.wait_for(client.wait_login("id"), 0.1)
    assert spawn.call_args.args[:2] == ("/exact/codex", "app-server")
    kill.assert_called_once_with(proc.pid, signal.SIGTERM)
    proc.stdin.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [
    {"Origin": "https://evil.test"}, {"Origin": "http://localhost.evil.test"},
    {"Origin": "null"}, {"Origin": "https://flowkit-phuc-studio.web.app"},
    {"X-Forwarded-For": "8.8.8.8"}, {"Forwarded": "for=8.8.8.8"},
    {"CF-Connecting-IP": "8.8.8.8"}, {"Referer": "https://evil.test"},
])
async def test_remote_pages_cannot_read_or_change_local_account(auth, monkeypatch, extra):
    instance, _ = auth
    monkeypatch.setattr(api, "codex_auth", instance)
    app = FastAPI()
    app.include_router(api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)), base_url="http://127.0.0.1:8100") as client:
        response = await client.post("/api/providers/codex/login", headers={"X-FlowKit-Local-Auth": "1", **extra})
    assert response.status_code == 403
    assert instance.task is None


@pytest.mark.asyncio
async def test_local_status_no_cache_and_missing_intent_header(auth, monkeypatch):
    instance, _ = auth
    monkeypatch.setattr(api, "codex_auth", instance)
    app = FastAPI()
    app.include_router(api.router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 123)), base_url="http://127.0.0.1:8100") as client:
        response = await client.get("/api/providers/codex/auth", headers={"X-FlowKit-Local-Auth": "1", "Referer": "http://localhost:5173/settings"})
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert response.json()["state"] == "connected"
        assert (await client.post("/api/providers/codex/login")).status_code == 403
