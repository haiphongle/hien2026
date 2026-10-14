"""Downloads must drain all chunks while retaining the trusted-host/size checks."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from agent.api import flow

MEDIA_ID = '11111111-1111-1111-1111-111111111111'


class Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *args):
        return False


def mock_download(monkeypatch, chunks, host='flow-content.google'):
    async def iter_chunked(_):
        for chunk in chunks:
            yield chunk
    client = SimpleNamespace(connected=True, get_media=AsyncMock(return_value={
        'data': {'image': {'fifeUrl': f'https://{host}/image/{MEDIA_ID}?signed=ok'}}}))
    monkeypatch.setattr(flow, 'get_flow_client', lambda: client)
    response = SimpleNamespace(status=200, headers={'Content-Type': 'image/jpeg'}, content=SimpleNamespace(iter_chunked=iter_chunked))
    session = SimpleNamespace(get=lambda _: Context(response))
    monkeypatch.setattr(flow.aiohttp, 'ClientSession', lambda **kwargs: Context(session))


@pytest.mark.asyncio
async def test_download_keeps_every_network_chunk(monkeypatch):
    chunks = [b'jpeg-header', b'pixels'*10000, b'jpeg-footer']
    mock_download(monkeypatch, chunks)
    result = await flow.download_image(MEDIA_ID)
    assert result.body == b''.join(chunks)
    assert result.media_type == 'image/jpeg'


@pytest.mark.asyncio
async def test_download_still_bounds_size(monkeypatch):
    mock_download(monkeypatch, [b'x'*(16*1024*1024), b'x'*(16*1024*1024), b'excess'])
    with pytest.raises(HTTPException) as exc:
        await flow.download_image(MEDIA_ID)
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_download_does_not_fetch_untrusted_url(monkeypatch):
    mock_download(monkeypatch, [b'pixels'], host='attacker.example')
    with pytest.raises(HTTPException) as exc:
        await flow.download_image(MEDIA_ID)
    assert exc.value.status_code == 502
