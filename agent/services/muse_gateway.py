"""User-controlled local Muse installation and app-owned process lifecycle."""
import asyncio
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time

import httpx
import psutil

from scripts import muse_gateway as launcher
from agent.services import muse_settings


class MuseGateway:
    def __init__(self):
        self.task = None
        self.process = None
        self.installer = None
        self.job = {'status': 'IDLE', 'stage': '', 'error': None}
        self.health = False
        self.checked_at = 0
        self.health_lock = asyncio.Lock()

    def active(self):
        return self.task is not None and not self.task.done()

    def owned(self):
        return self.process is not None and self.process.returncode is None

    async def online(self, force=False):
        async with self.health_lock:
            if not force and time.monotonic() - self.checked_at < 3:
                return self.health
            try:
                async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                    response = await client.get(launcher.BASE_URL + '/healthz')
                    body = response.json()
                    self.health = response.status_code == 200 and body.get('ok') is True and body.get('version') == '0.1.0'
            except (httpx.HTTPError, ValueError, AttributeError):
                self.health = False
            self.checked_at = time.monotonic()
            return self.health

    async def status(self):
        settings = muse_settings.load()
        local = launcher.local_url(settings.base_url)
        if self.process is not None and self.process.returncode is not None and not self.active():
            self.job.update(status='FAILED', stage='Gateway đã dừng', error='Gateway Muse đã dừng ngoài dự kiến. Xem nhật ký rồi khởi động lại.')
        installed, chrome = launcher.installed(), launcher.find_chrome()
        running = await self.online() if local else False
        conflict = local and not running and await asyncio.to_thread(launcher.port_busy, launcher.PORT)
        state = ('EXTERNAL' if not local else 'RUNNING' if running else
                 'INSTALLING' if self.active() and self.installer else 'STARTING' if self.active() else
                 'BLOCKED' if conflict else 'FAILED' if self.job['status'] == 'FAILED' else
                 'NEEDS_CHROME' if installed and not chrome else 'STOPPED' if installed else 'MISSING')
        return {**self.job, 'state': state, 'busy': self.active(), 'installed': installed,
                'running': running, 'owned': self.owned(), 'local': local, 'chrome_available': bool(chrome),
                'chrome_path': chrome, 'source': launcher.PACKAGE, 'revision': launcher.REVISION,
                'path': str(launcher.STATE), 'log_path': str(launcher.STATE / 'gateway.log'),
                'has_log': (launcher.STATE / 'gateway.log').is_file(),
                'dashboard_url': (launcher.BASE_URL + '/dashboard' if running else None) if local else settings.base_url + '/dashboard'}

    def begin(self, operation):
        if self.active():
            return  # Repeated clicks share the existing job.
        if not launcher.local_url(muse_settings.load().base_url):
            raise ValueError('Gateway đang trỏ tới máy khác. Giữ cấu hình đó hoặc đổi về gateway nội bộ trước khi cài/khởi động.')
        self.job = {'status': 'PROCESSING', 'stage': 'Đang kiểm tra gateway Muse', 'error': None}
        self.task = asyncio.create_task(self.run(operation))

    async def terminate(self, process):
        if process is None or process.returncode is not None:
            return
        # Only handles created in this process are eligible; never trust stored PIDs.
        try:
            children = psutil.Process(process.pid).children(recursive=True)
        except (psutil.Error, OSError):
            children = []
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), 10)
        except (ProcessLookupError, TimeoutError):
            if process.returncode is None:
                process.kill()
            await process.wait()
        finally:
            for child in children:
                try:
                    child.terminate()
                except (psutil.Error, OSError):
                    pass
            _, alive = await asyncio.to_thread(psutil.wait_procs, children, timeout=3)
            for child in alive:
                try:
                    child.kill()
                except (psutil.Error, OSError):
                    pass

    async def spawn(self, *args, **kwargs):
        task = asyncio.create_task(asyncio.create_subprocess_exec(*args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await self.terminate(await task)
            raise

    async def stop(self):
        process, self.process = self.process, None
        await self.terminate(process)
        self.health, self.checked_at = False, 0

    async def prepare(self, log):
        self.job['stage'] = 'Đang tải và cài Muse · lần đầu có thể mất vài phút'
        self.installer = await self.spawn(sys.executable, str(launcher.ROOT / 'scripts/muse_gateway.py'),
            '--install', '--prepare-only', env=launcher.environment(muse_settings.load()),
            stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=asyncio.subprocess.STDOUT)
        try:
            await asyncio.wait_for(self.installer.wait(), 900)
            if self.installer.returncode or not launcher.installed():
                raise ValueError('Chưa cài được gateway Muse. Bấm Xem nhật ký, kiểm tra mạng/dung lượng rồi thử lại.')
        finally:
            await self.terminate(self.installer)
            self.installer = None

    async def run(self, operation):
        try:
            running = await self.online(True)
            if operation in {'restart', 'stop'}:
                if not self.owned():
                    raise ValueError('Gateway được mở từ phiên khác. FrameWeaver chỉ dừng/khởi động lại gateway do phiên này mở.')
                await self.stop()
            elif running:
                self.job.update(status='COMPLETED', stage='Gateway đã chạy từ phiên khác · dùng kết nối hiện có')
                return
            if operation == 'stop':
                self.job.update(status='COMPLETED', stage='Đã dừng gateway cho phiên này')
                return
            if await asyncio.to_thread(launcher.port_busy, launcher.PORT):
                raise ValueError('Cổng 18610 đang được ứng dụng khác sử dụng. Kiểm tra phiên Muse cũ trước khi thử lại.')
            launcher.STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
            if shutil.disk_usage(launcher.STATE).free < 512 * 1024**2:
                raise ValueError('Cần ít nhất 512 MB trống để cài/khởi động gateway Muse.')
            log_path = launcher.STATE / 'gateway.log'
            if log_path.exists() and log_path.stat().st_size > 5 * 1024**2:
                log_path.replace(log_path.with_suffix('.log.previous'))
            with os.fdopen(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600), 'ab') as log:
                if not launcher.installed():
                    if operation != 'install':
                        raise ValueError('Gateway chưa cài. Bấm Cài và khởi động Muse trước.')
                    await self.prepare(log)
                chrome = launcher.find_chrome()
                if not chrome:
                    raise ValueError('Gateway đã cài nhưng chưa tìm thấy Chrome/Chromium. Cài Chrome rồi bấm Khởi động Muse.')
                if await asyncio.to_thread(launcher.port_busy, launcher.CDP_PORT):
                    raise ValueError('Cổng trình duyệt Muse 19210 đang được dùng. Đóng phiên Muse cũ trước khi khởi động thêm.')
                config = muse_settings.load()
                if not launcher.local_url(config.base_url):
                    raise ValueError('Cấu hình gateway vừa đổi. Kiểm tra Cài đặt rồi thử lại.')
                values = {'enabled': True, 'api_key': config.api_key or secrets.token_urlsafe(32)}
                if operation == 'install':
                    values['auto_start'] = True
                config = muse_settings.save(values)
                self.job['stage'] = 'Đang khởi động Muse và trình duyệt riêng'
                self.process = await self.spawn(str(launcher.PYTHON),
                    str(launcher.ROOT / 'scripts/muse_gateway_runtime.py'), cwd=launcher.STATE,
                    env=launcher.environment(config, chrome), stdin=asyncio.subprocess.DEVNULL,
                    stdout=log, stderr=asyncio.subprocess.STDOUT)
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                if self.process.returncode is not None:
                    raise ValueError('Gateway Muse đã dừng khi khởi động. Bấm Xem nhật ký để kiểm tra Chrome và môi trường.')
                if await self.online(True):
                    self.job.update(status='COMPLETED', stage='Gateway đã chạy · kết nối tài khoản Muse rồi bấm Kiểm tra kết nối')
                    return
                await asyncio.sleep(1)
            raise ValueError('Gateway chưa mở kịp trong 90 giây. Kiểm tra Chrome và nhật ký rồi thử lại.')
        except asyncio.CancelledError:
            await self.stop()
            self.job.update(status='CANCELLED', stage='Đã hủy thao tác Muse', error=None)
            raise
        except (OSError, ValueError, TimeoutError) as exc:
            await self.stop()
            message = str(exc) if isinstance(exc, ValueError) else 'Không khởi động/cài được Muse. Kiểm tra quyền ghi, mạng, dung lượng và nhật ký.'
            self.job.update(status='FAILED', error=message, stage='Muse cần kiểm tra')

    def startup(self):
        settings = muse_settings.load()
        if settings.enabled and settings.auto_start and launcher.local_url(settings.base_url) and launcher.installed():
            self.begin('start')  # No downloading or blocking the app's startup.

    async def shutdown(self):
        if self.active():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        await self.stop()

    def open_log(self):
        path = launcher.STATE / 'gateway.log'
        if not path.is_file():
            raise ValueError('Chưa có nhật ký Muse. Hãy cài hoặc khởi động gateway trước.')
        command = ['open', str(path)] if sys.platform == 'darwin' else ['explorer', str(path)] if os.name == 'nt' else ['xdg-open', str(path)]
        subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


muse_gateway = MuseGateway()
