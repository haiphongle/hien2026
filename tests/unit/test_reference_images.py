import base64
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from PIL import Image

from agent.api import projects as api
from agent.services import reference_images as images


class Response:
    def __init__(self, content=b'', status=200, headers=None):
        self.data, self.status, self.headers = content, status, headers or {}
        self.content_length = None
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        yield self.data


class Session(Response):
    def __init__(self, responses):
        self.get = Mock(side_effect=responses)


def session(monkeypatch, *responses):
    client = Session(responses)
    monkeypatch.setattr(images.aiohttp, 'ClientSession', lambda **kwargs: client)
    return client


def png():
    stream = io.BytesIO()
    Image.new('RGB', (4, 4), 'blue').save(stream, 'PNG')
    return stream.getvalue()


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'ftp://example.com/a.png', 'https://name:secret@example.com/a', 'http://example.com:8100/a', 'https://'])
def test_url_rejects_unsupported_schemes_credentials_and_private_ports(url):
    with pytest.raises(ValueError):
        images.public_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize('host', ['127.0.0.1', '[::1]', '169.254.169.254', '192.168.1.1', '[::ffff:127.0.0.1]'])
async def test_private_numeric_addresses_are_never_requested(monkeypatch, host):
    client = session(monkeypatch)
    with pytest.raises(ValueError, match='công khai'):
        await images.download(f'http://{host}/photo.png')
    client.get.assert_not_called()


@pytest.mark.asyncio
async def test_dns_resolver_rejects_mixed_public_and_private_answers(monkeypatch):
    resolver = images.PublicResolver()
    monkeypatch.setattr(resolver.resolver, 'resolve', AsyncMock(return_value=[{'host': '8.8.8.8'}, {'host': '10.1.1.1'}]))
    try:
        with pytest.raises(ValueError, match='công khai'):
            await resolver.resolve('image.example')
    finally:
        await resolver.close()


@pytest.mark.asyncio
async def test_redirect_is_validated_and_valid_image_is_returned(monkeypatch):
    client = session(monkeypatch, Response(status=302, headers={'Location': 'http://127.0.0.1/private'}))
    with pytest.raises(ValueError, match='công khai'):
        await images.download('https://example.com/image')
    assert client.get.call_count == 1
    photo = png()
    session(monkeypatch, Response(status=302, headers={'Location': '/photo.png'}), Response(photo))
    assert await images.download('https://example.com/image') == photo


@pytest.mark.asyncio
async def test_stream_limit_and_non_image_response_are_rejected(monkeypatch):
    monkeypatch.setattr(images, 'LIMIT', 100)
    session(monkeypatch, Response(b'x'*101))
    with pytest.raises(ValueError, match='10 MB'):
        await images.download('https://example.com/photo')
    session(monkeypatch, Response(b'<html>not a photo</html>'))
    with pytest.raises(ValueError, match='không trả về ảnh'):
        await images.download('https://example.com/photo')


@pytest.mark.asyncio
async def test_url_endpoint_reuses_existing_photo_uploader(monkeypatch):
    monkeypatch.setattr(api.crud, 'get_project', AsyncMock(return_value={'id': 'project'}))
    monkeypatch.setattr(api, 'get_flow_client', lambda: SimpleNamespace(connected=True))
    photo = png()
    monkeypatch.setattr(images, 'download', AsyncMock(return_value=photo))
    upload = AsyncMock(return_value='saved-character')
    monkeypatch.setattr(api, 'upload_character_photo', upload)
    body = api.CharacterPhotoURL(name='Nhân vật A', url='https://example.com/photo.png')
    assert await api.import_character_url('project', body) == 'saved-character'
    assert base64.b64decode(upload.await_args.args[1].image_base64) == photo
    monkeypatch.setattr(api, 'get_flow_client', lambda: SimpleNamespace(connected=False))
    with pytest.raises(HTTPException) as error:
        await api.import_character_url('project', body)
    assert error.value.status_code == 503 and upload.await_count == 1
