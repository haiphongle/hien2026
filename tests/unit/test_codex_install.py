"""CLI installation is local, cancellable, verified and independent of credentials."""
import asyncio
import hashlib
import io
import tarfile
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from agent.api import codex_auth as api
from agent.services import cli_providers, codex_install as service


@pytest.mark.asyncio
async def test_slow_first_launch_is_reported_and_process_is_reaped(monkeypatch):
    class Process:
        returncode = None
        killed = False
        waited = False

        async def communicate(self):
            raise TimeoutError

        def kill(self):
            self.killed = True

        async def wait(self):
            self.waited = True

    process = Process()
    monkeypatch.setattr(service.asyncio, 'create_subprocess_exec', AsyncMock(return_value=process))
    with pytest.raises(ValueError, match='60s'):
        await service.probe('downloaded-codex')
    assert process.killed and process.waited


def archive_bytes(name='codex-aarch64-apple-darwin', *, symlink=False):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as archive:
        member = tarfile.TarInfo(name)
        if symlink:
            member.type = tarfile.SYMTYPE
            member.linkname = '/outside/codex'
            archive.addfile(member)
        else:
            member.size = 7
            archive.addfile(member, io.BytesIO(b'fixture'))
    return output.getvalue()


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(service, 'ROOT', tmp_path / 'Dữ liệu có dấu - Codex')
    monkeypatch.setattr(service.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(service.platform, 'machine', lambda: 'arm64')
    content = archive_bytes()
    monkeypatch.setitem(service.ASSETS, service.target(), (len(content), hashlib.sha256(content).hexdigest()))
    client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=content))
    monkeypatch.setattr(service.httpx, 'AsyncClient', lambda **kwargs: client(**({'transport': transport} | kwargs)))
    probe = AsyncMock()
    monkeypatch.setattr(service, 'probe', probe)
    return service.CodexInstaller(), probe


@pytest.mark.asyncio
async def test_installs_only_after_hash_extract_and_probe_and_coalesces_clicks(setup):
    installer, probe = setup
    installer.start()
    task = installer.task
    installer.start()
    assert installer.task is task
    await task
    assert installer.status()['status'] == 'COMPLETED'
    assert service.binary_path().read_bytes() == b'fixture'
    probe.assert_awaited_once()
    assert not list(service.ROOT.glob('.install-*'))
    installer.start()
    assert installer.task is task  # no redundant download on repeated install


@pytest.mark.asyncio
async def test_checksum_mismatch_never_executes_or_publishes(setup, monkeypatch):
    installer, probe = setup
    size, _ = service.ASSETS[service.target()]
    monkeypatch.setitem(service.ASSETS, service.target(), (size, '0' * 64))
    installer.start()
    await installer.task
    assert installer.status()['status'] == 'FAILED'
    assert 'Checksum' in installer.status()['error']
    assert not service.executable() and not list(service.ROOT.glob('.install-*'))
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_version_does_not_publish_and_retry_succeeds(setup):
    installer, probe = setup
    probe.side_effect = ValueError('Không đúng phiên bản')
    installer.start()
    await installer.task
    assert installer.status()['status'] == 'FAILED' and not service.executable()
    probe.side_effect = None
    installer.start()
    await installer.task
    assert installer.status()['status'] == 'COMPLETED'


@pytest.mark.asyncio
async def test_cancel_removes_partial_download_and_can_retry(setup, monkeypatch):
    installer, probe = setup
    entered = asyncio.Event()
    original = installer.download

    async def partial(path, update):
        path.write_bytes(b'partial')
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(installer, 'download', partial)
    installer.start()
    await entered.wait()
    await installer.cancel()
    assert installer.status()['status'] == 'CANCELLED' and not service.executable()
    assert not list(service.ROOT.glob('.install-*'))
    probe.assert_not_awaited()
    monkeypatch.setattr(installer, 'download', original)
    installer.start()
    await installer.task
    assert installer.status()['status'] == 'COMPLETED'


@pytest.mark.parametrize('name,link', [('../../escape', False), ('/absolute', False), ('codex-aarch64-apple-darwin', True)])
def test_archive_cannot_write_outside_stage(tmp_path, name, link):
    archive = tmp_path / 'archive.tar.gz'
    archive.write_bytes(archive_bytes(name, symlink=link))
    with pytest.raises(ValueError):
        service.unpack(archive, tmp_path / 'codex', 'codex-aarch64-apple-darwin.tar.gz')
    assert not (tmp_path / 'codex').exists()


@pytest.mark.parametrize('system,arch,expected', [
    ('Darwin', 'arm64', 'aarch64-apple-darwin'), ('Darwin', 'x86_64', 'x86_64-apple-darwin'),
    ('Windows', 'AMD64', 'x86_64-pc-windows-msvc'), ('Windows', 'ARM64', 'aarch64-pc-windows-msvc'),
    ('Linux', 'aarch64', 'aarch64-unknown-linux-musl'), ('Linux', 'x86_64', 'x86_64-unknown-linux-musl'),
    ('Darwin', 'unknown', None),
])
def test_platform_selection(monkeypatch, system, arch, expected):
    monkeypatch.setattr(service.platform, 'system', lambda: system)
    monkeypatch.setattr(service.platform, 'machine', lambda: arch)
    assert service.target() == expected
    assert service.CodexInstaller().status()['supported'] == (expected in service.ASSETS)


def test_discovery_preserves_existing_cli_and_other_providers(setup, monkeypatch):
    service.binary_path().parent.mkdir(parents=True)
    service.binary_path().write_bytes(b'managed')
    service.binary_path().chmod(0o755)
    monkeypatch.setattr(cli_providers.shutil, 'which', lambda _: '/existing/codex')
    assert cli_providers.executable('codex') == '/existing/codex'
    monkeypatch.setattr(cli_providers.shutil, 'which', lambda _: None)
    monkeypatch.setattr(cli_providers, '_CODEX_APP', service.ROOT / 'absent')
    assert cli_providers.executable('codex') == str(service.binary_path())
    assert cli_providers.executable('claude') is None and cli_providers.executable('muse2api') is None


@pytest.mark.asyncio
async def test_install_api_requires_local_consent_and_rejects_custom_url(setup, monkeypatch):
    installer, _ = setup
    monkeypatch.setattr(api, 'codex_installer', installer)
    monkeypatch.setattr(api.codex_auth, 'active', lambda: False)
    app = FastAPI()
    app.include_router(api.router)
    headers = {'X-FlowKit-Local-Auth': '1'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://localhost') as client:
        assert (await client.post('/api/providers/codex/installation', json={'confirm': True})).status_code == 403
        assert (await client.post('/api/providers/codex/installation', headers={**headers, 'Origin': 'https://evil.example'}, json={'confirm': True})).status_code == 403
        for body in [{}, {'confirm': False}, {'confirm': True, 'url': 'https://evil.example'}]:
            assert (await client.post('/api/providers/codex/installation', headers=headers, json=body)).status_code == 422
        assert (await client.post('/api/providers/codex/installation', headers=headers, json={'confirm': True})).status_code == 202
        await installer.task
        assert (await client.delete('/api/providers/codex/installation', headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_installed_cli_invalidates_missing_account_cache(setup, monkeypatch):
    installer, _ = setup
    installer.start()
    await installer.task
    monkeypatch.setattr(api, 'codex_installer', installer)
    monkeypatch.setattr(api.codex_auth, 'account', {'state': 'missing'})
    status = AsyncMock(return_value={'state': 'signed_out'})
    monkeypatch.setattr(api.codex_auth, 'status', status)
    assert (await api.status())['installation']['status'] == 'COMPLETED'
    status.assert_awaited_once_with(True)
