"""Optional, pinned Codex CLI in user data. Never runs a downloaded install script."""
import asyncio
import hashlib
import os
from pathlib import Path
import platform
import shutil
import tarfile
import tempfile

import httpx

from agent.paths import DATA_DIR

VERSION = '0.163.0'
SOURCE = f'https://github.com/openai/codex/releases/tag/rust-v{VERSION}'
ROOT = DATA_DIR / '.flowkit' / 'tools' / 'codex'
# Official OpenAI GitHub release asset digests, pinned rather than resolving latest.
ASSETS = {
    'aarch64-apple-darwin': (99551880, '61c4d9a92521bdb42b0c1d9f87c673f6da628a06de5945cbdd9a0010632f6d10'),
    'x86_64-apple-darwin': (108640069, '562a1aea0e5686283a467687d268ae33705f02fd4d40f1bc4110a415c5c1c7b1'),
    'aarch64-unknown-linux-musl': (104950609, 'a7261e65f4a96d8f4ca65a069c6cf2dc2c33defc341e48078acefda9461fffe4'),
    'x86_64-unknown-linux-musl': (112589966, 'beb339e8ecd9ba057a6bb2dc81ad8ca474812edfbf0fc5056ca818797648dde4'),
    'aarch64-pc-windows-msvc': (104421866, 'cb23c25cb7e8efa945d66ea3d061405ea418cf386d54719d694cce6c4d002a65'),
    'x86_64-pc-windows-msvc': (113807177, 'dbdd5ab5ecce1358e69fac8db1cb3538659c48e99498f3d2ea0e3e7ae878ef8c'),
}
MAX_BINARY = 512 * 1024**2


def target():
    arch = {'arm64': 'aarch64', 'aarch64': 'aarch64', 'amd64': 'x86_64', 'x86_64': 'x86_64'}.get(platform.machine().lower())
    system = {'Darwin': 'apple-darwin', 'Linux': 'unknown-linux-musl', 'Windows': 'pc-windows-msvc'}.get(platform.system())
    return f'{arch}-{system}' if arch and system else None


def binary_path():
    triple = target()
    return ROOT / VERSION / (triple or 'unsupported') / ('codex.exe' if triple and 'windows' in triple else 'codex')


def executable():
    path = binary_path()
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None


def unpack(archive, destination, name):
    # Read just the expected regular file; never extract paths, links or permissions.
    with tarfile.open(archive, 'r:gz') as source:
        for member in source:
            if member.name not in {name.removesuffix('.tar.gz'), 'codex', 'codex.exe'}:
                continue
            if not member.isfile() or not 0 < member.size <= MAX_BINARY:
                raise ValueError('Gói Codex không chứa file thực thi hợp lệ.')
            with source.extractfile(member) as incoming, destination.open('wb') as output:
                shutil.copyfileobj(incoming, output, 1024 * 1024)
            destination.chmod(0o755)
            return
    raise ValueError('Không tìm thấy Codex trong gói chính thức.')


async def probe(binary):
    proc = await asyncio.create_subprocess_exec(str(binary), '--version',
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        # macOS may validate the downloaded executable on its first launch.
        output, _ = await asyncio.wait_for(proc.communicate(), 60)
        if proc.returncode or output.decode(errors='replace').strip() != f'codex-cli {VERSION}':
            raise ValueError('Codex vừa tải không khởi động đúng phiên bản. Chưa áp dụng bộ cài.')
    except TimeoutError as exc:
        raise ValueError('Codex đã tải xong nhưng chưa khởi động kịp (60s). Đợi hệ điều hành kiểm tra ứng dụng rồi thử lại.') from exc
    finally:
        if proc.returncode is None:
            proc.kill()
        await proc.wait()


class CodexInstaller:
    def __init__(self):
        self.task = None
        self.job = {'status': 'IDLE', 'progress': 0, 'stage': '', 'error': None}

    def active(self):
        return self.task is not None and not self.task.done()

    def status(self):
        triple = target()
        suffix = '.exe' if triple and 'windows' in triple else ''
        name = f'codex-{triple}{suffix}.tar.gz'
        size = ASSETS.get(triple, (0, ''))[0]
        return {**self.job, 'supported': triple in ASSETS, 'version': VERSION, 'target': triple,
            'source': SOURCE, 'download_url': f'https://github.com/openai/codex/releases/download/rust-v{VERSION}/{name}',
            'size_bytes': size, 'path': str(binary_path()), 'installed': bool(executable())}

    def start(self):
        if self.active():
            return self.status()
        if executable():
            self.job.update(status='COMPLETED', progress=100, stage='Thành phần đã được cài', error=None)
            return self.status()
        if target() not in ASSETS:
            raise ValueError('Chưa có bộ cài tự động cho hệ điều hành/CPU này. Dùng hướng dẫn Codex chính thức.')
        self.job = {'status': 'PROCESSING', 'progress': 0, 'stage': 'Chuẩn bị tải Codex', 'error': None}
        self.task = asyncio.create_task(self.run())
        return self.status()

    async def cancel(self):
        if self.active():
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.job.update(status='CANCELLED', stage='Đã dừng cài đặt', error=None)
        return self.status()

    async def download(self, archive, update):
        info = self.status()
        size, expected = ASSETS[info['target']]
        count, digest = 0, hashlib.sha256()
        async with httpx.AsyncClient(follow_redirects=True, trust_env=False,
                timeout=httpx.Timeout(60, connect=15)) as client:
            async with client.stream('GET', info['download_url']) as response:
                response.raise_for_status()
                if response.url.scheme != 'https' or response.url.host not in {
                    'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com',
                }:
                    raise ValueError('Nguồn tải Codex không thuộc kho phát hành chính thức.')
                with archive.open('wb') as output:
                    async for block in response.aiter_bytes(1024 * 1024):
                        count += len(block)
                        if count > size:
                            raise ValueError('Dung lượng gói Codex không khớp; chưa cài đặt.')
                        output.write(block)
                        digest.update(block)
                        update(progress=min(90, round(90 * count / size)), stage='Đang tải Codex',
                            bytes_downloaded=count, bytes_total=size)
        if count != size or digest.hexdigest() != expected:
            raise ValueError('Checksum Codex không khớp; chưa cài đặt. Thử tải lại từ nguồn chính thức.')

    async def run(self):
        try:
            ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
            if shutil.disk_usage(ROOT).free < self.status()['size_bytes'] + MAX_BINARY + 64 * 1024**2:
                raise ValueError('Cần thêm dung lượng trống để tải và giải nén Codex (khoảng 700 MB).')
            with tempfile.TemporaryDirectory(prefix='.install-', dir=ROOT) as directory:
                stage = Path(directory)
                staged_binary = stage / binary_path().name
                await asyncio.wait_for(self.download(stage / 'archive.tar.gz', self.job.update), 900)
                self.job.update(progress=92, stage='Đã xác minh checksum · đang giải nén')
                worker = asyncio.create_task(asyncio.to_thread(unpack, stage / 'archive.tar.gz', staged_binary,
                    self.status()['download_url'].rsplit('/', 1)[-1]))
                try:
                    await asyncio.shield(worker)
                except asyncio.CancelledError:
                    await asyncio.gather(worker, return_exceptions=True)
                    raise
                self.job.update(progress=97, stage='Kiểm tra thành phần vừa cài')
                await probe(staged_binary)
                destination = binary_path()
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged_binary, destination)
            self.job.update(status='COMPLETED', progress=100, stage='Đã cài · tiếp tục đăng nhập ChatGPT')
        except asyncio.CancelledError:
            self.job.update(status='CANCELLED', stage='Đã dừng cài đặt', error=None)
            raise
        except (httpx.HTTPError, TimeoutError):
            self.job.update(status='FAILED', error='Không tải được Codex. Kiểm tra mạng rồi bấm Thử lại.')
        except ValueError as exc:
            self.job.update(status='FAILED', error=str(exc))
        except (OSError, tarfile.TarError):
            # Path/permission failures and corrupt archives never publish a partial executable.
            import logging
            logging.getLogger(__name__).exception('Codex installation failed')
            self.job.update(status='FAILED', error='Không cài được Codex. Kiểm tra dung lượng, quyền ghi và nguồn tải rồi thử lại.')


codex_installer = CodexInstaller()
