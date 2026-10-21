"""Use Codex's account protocol; credentials stay in Codex's own auth store."""
import asyncio
import json
import os
import signal
import tempfile
import time
import uuid
from collections import deque
from contextlib import suppress
from urllib.parse import urlsplit

from agent.services.cli_providers import executable
from agent.services.process_control import spawn_options

LOGIN_TIMEOUT = 300
RPC_TIMEOUT = 20


class CodexRPCError(RuntimeError):
    pass


def trusted_auth_url(value):
    if not isinstance(value, str) or len(value) > 16384:
        return False
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and url.hostname in {"auth.openai.com", "chatgpt.com"}
                and url.port in {None, 443} and not url.username and not url.password)
    except ValueError:
        return False


def auth_error(exc):
    # Never return raw CLI errors: they can contain credentials or OAuth URLs.
    message = str(exc).lower()
    return any(marker in message for marker in (
        "401", "unauthorized", "not authenticated", "not logged in", "login required",
        "invalid_grant", "refresh_token_expired", "refresh_token_reused", "refresh_token_invalidated",
        "refresh token has expired", "refresh token has been revoked", "please sign in again",
    ))


class CodexSession:
    """One stdio client, with sequential RPCs and a bounded process lifetime."""

    def __init__(self, binary):
        self.binary = binary
        self.proc = None
        self.directory = None
        self.sequence = 0
        self.events = deque()

    async def __aenter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix="frameweaver-codex-auth-")
        try:
            self.proc = await asyncio.create_subprocess_exec(
                self.binary, "app-server", "--listen", "stdio://",
                "-c", 'model_provider="openai"', "-c", 'approval_policy="never"',
                "-c", 'sandbox_mode="read-only"',
                cwd=self.directory.name, **spawn_options(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=512 * 1024,
            )
            await self.request("initialize", {"clientInfo": {
                "name": "frameweaver", "title": "FrameWeaver", "version": "1.3.1",
            }})
            await self.send({"method": "initialized"})
            return self
        except BaseException:
            await self.close()
            raise

    async def __aexit__(self, *args):
        await self.close()

    async def close(self):
        if self.proc:
            if self.proc.returncode is None:
                with suppress(ProcessLookupError):
                    if os.name == 'nt':
                        self.proc.terminate()
                    else:
                        os.killpg(self.proc.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(self.proc.wait(), 3)
                except TimeoutError:
                    with suppress(ProcessLookupError):
                        if os.name == 'nt':
                            self.proc.kill()
                        else:
                            os.killpg(self.proc.pid, signal.SIGKILL)
                    await self.proc.wait()
            if self.proc.stdin:
                self.proc.stdin.close()
        if self.directory:
            self.directory.cleanup()

    async def send(self, message):
        self.proc.stdin.write((json.dumps(message) + "\n").encode())
        await self.proc.stdin.drain()

    async def read(self):
        while True:
            line = await self.proc.stdout.readline()
            if not line:
                raise CodexRPCError("Codex account service stopped")
            message = json.loads(line)
            if not isinstance(message, dict):
                raise CodexRPCError("Invalid Codex account response")
            if "method" in message and "id" in message:
                # This client only manages accounts. It never executes tools.
                await self.send({"id": message["id"], "error": {
                    "code": -32601, "message": "Unsupported client method",
                }})
                continue
            return message

    async def request(self, method, params=None, timeout=RPC_TIMEOUT):
        self.sequence += 1
        request_id = self.sequence
        async with asyncio.timeout(timeout):
            await self.send({"id": request_id, "method": method, "params": params or {}})
            while True:
                message = await self.read()
                if message.get("method") == "account/login/completed":
                    self.events.append(message)
                if message.get("id") == request_id:
                    if "error" in message:
                        raise CodexRPCError(str(message["error"]))
                    result = message.get("result")
                    if not isinstance(result, dict):
                        raise CodexRPCError("Invalid Codex account result")
                    return result

    async def wait_login(self, login_id):
        while True:
            message = self.events.popleft() if self.events else await self.read()
            params = message.get("params") or {}
            if message.get("method") == "account/login/completed" and params.get("loginId") == login_id:
                if params.get("success") is not True:
                    raise CodexRPCError("ChatGPT login did not complete")
                return


class CodexAuth:
    def __init__(self):
        self.account = {"state": "unknown", "method": None, "checked_at": None,
                        "message": "Chưa kiểm tra kết nối Codex."}
        self.login = None
        self.task = None
        self.ready = None
        self.expires = 0
        self.check_lock = asyncio.Lock()
        self.login_lock = asyncio.Lock()

    def snapshot(self):
        return {**self.account, "login": dict(self.login) if self.login else None}

    def active(self):
        return self.task is not None and not self.task.done()

    async def status(self, refresh=False):
        if not self.active():
            async with self.check_lock:
                if not self.active() and (refresh or time.monotonic() >= self.expires):
                    await self.check()
        return self.snapshot()

    async def check(self):
        state, method = "unknown", None
        message = "Chưa xác nhận được kết nối. Kiểm tra mạng rồi thử lại."
        binary = executable("codex")
        if not binary:
            state, message = "missing", "Chưa tìm thấy Codex CLI. Cài Codex trên máy chạy FrameWeaver trước."
        else:
            try:
                async with CodexSession(binary) as session:
                    result = await session.request("account/read", {"refreshToken": False})
                    account = result.get("account") or {}
                    method = account.get("type")
                    if method == "chatgpt":
                        # This checks the signed-in account without generating content.
                        # Codex owns token renewal; do not force token rotation on every poll.
                        await session.request("account/rateLimits/read")
                        state, message = "connected", "Đã xác nhận kết nối bằng tài khoản ChatGPT."
                    elif method == "apiKey":
                        state, message = "api_key", "Codex đang dùng API key. Bấm Kiểm tra AI ở tác vụ để xác nhận quyền dùng model."
                    elif not account:
                        state, message = "signed_out", "Đăng nhập bằng ChatGPT để dùng Codex cho các tác vụ AI."
            except Exception as exc:
                if auth_error(exc):
                    state, message = "expired", "Phiên Codex không còn hợp lệ. Hãy đăng nhập lại bằng ChatGPT."
        self.account = {"state": state, "method": method, "checked_at": time.time(), "message": message}
        self.expires = time.monotonic() + 30

    async def start(self):
        async with self.login_lock:
            if not self.active():
                binary = executable("codex")
                if not binary:
                    raise ValueError("Chưa tìm thấy Codex CLI trên máy chạy FrameWeaver.")
                self.ready = asyncio.Event()
                self.login = {"id": str(uuid.uuid4()), "status": "starting", "auth_url": None,
                              "expires_at": time.time() + LOGIN_TIMEOUT,
                              "message": "Đang mở kết nối đăng nhập…"}
                self.task = asyncio.create_task(self.run_login(binary, self.login, self.ready))
            ready = self.ready
        # Repeated clicks/tabs reuse the same callback listener and login URL.
        await asyncio.wait_for(asyncio.shield(ready.wait()), RPC_TIMEOUT * 2 + 5)
        return self.snapshot()

    async def run_login(self, binary, login, ready):
        try:
            async with asyncio.timeout(LOGIN_TIMEOUT):
                async with CodexSession(binary) as session:
                    upstream_id = None
                    completed = False
                    try:
                        result = await session.request("account/login/start", {"type": "chatgpt"})
                        upstream_id = result.get("loginId")
                        if not upstream_id or not trusted_auth_url(result.get("authUrl")):
                            raise CodexRPCError("Invalid ChatGPT login response")
                        login.update(status="pending", auth_url=result["authUrl"],
                                     message="Hoàn tất đăng nhập trên trang ChatGPT. FrameWeaver sẽ tự cập nhật.")
                        ready.set()
                        await session.wait_login(upstream_id)
                        completed = True
                    finally:
                        if upstream_id and not completed:
                            with suppress(Exception):
                                await session.request("account/login/cancel", {"loginId": upstream_id}, timeout=2)
            login.update(status="completed", message="Đã hoàn tất đăng nhập Codex bằng ChatGPT.")
        except asyncio.CancelledError:
            login.update(status="cancelled", message="Đã hủy lượt đăng nhập.")
        except TimeoutError:
            login.update(status="expired", message="Lượt đăng nhập đã hết thời gian chờ. Bấm đăng nhập lại.")
        except Exception:
            login.update(status="failed", message="Chưa hoàn tất đăng nhập. Đóng lượt đăng nhập Codex khác nếu có, rồi thử lại.")
        finally:
            login["auth_url"] = None
            self.expires = 0
            ready.set()

    async def cancel(self, login_id):
        async with self.login_lock:
            if not self.login or self.login["id"] != login_id:
                raise ValueError("Lượt đăng nhập đã thay đổi. Hãy kiểm tra lại trạng thái.")
            if self.active():
                self.task.cancel()
                await self.task
        return await self.status()

    async def shutdown(self):
        if self.active():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)


codex_auth = CodexAuth()
