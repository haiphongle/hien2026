"""Bounded public-URL image import for the existing character uploader."""
import io
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import aiohttp
from PIL import Image, UnidentifiedImageError

LIMIT = 10 * 1024 * 1024


def public_url(url):
    value = urlsplit(url)
    if value.scheme not in {'https', 'http'} or not value.hostname or value.username or value.password or value.port not in {None, 80, 443}:
        raise ValueError('Dán URL ảnh trực tiếp bằng HTTP/HTTPS, không kèm tài khoản hoặc cổng riêng.')
    return url


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Validate the same resolved addresses the connector will actually use."""
    def __init__(self):
        self.resolver = aiohttp.resolver.DefaultResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        addresses = await self.resolver.resolve(host, port, family)
        if not addresses or any(not ipaddress.ip_address(a['host']).is_global for a in addresses):
            raise ValueError('Chỉ nhập ảnh từ địa chỉ Internet công khai.')
        return addresses

    async def close(self):
        await self.resolver.close()


async def download(url):
    public_url(url)
    resolver = PublicResolver()
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=resolver),
                                        timeout=aiohttp.ClientTimeout(total=25), cookie_jar=aiohttp.DummyCookieJar()) as session:
            for _ in range(4):
                # Numeric IPs bypass aiohttp's resolver, so check them here too.
                host = urlsplit(public_url(url)).hostname
                try:
                    address = ipaddress.ip_address(host)
                except ValueError:
                    address = None
                if address and not address.is_global:
                    raise ValueError('Chỉ nhập ảnh từ địa chỉ Internet công khai.')
                async with session.get(url, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.headers.get('Location')
                        if not location:
                            raise ValueError('Liên kết ảnh chuyển hướng không hợp lệ.')
                        url = urljoin(url, location)
                        continue
                    if response.status != 200:
                        raise ValueError(f'Không tải được ảnh (HTTP {response.status}). Dùng link ảnh trực tiếp hoặc tải từ máy.')
                    if response.content_length and response.content_length > LIMIT:
                        raise ValueError('Ảnh tối đa 10 MB.')
                    content = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        content.extend(chunk)
                        if len(content) > LIMIT:
                            raise ValueError('Ảnh tối đa 10 MB.')
                    try:
                        with Image.open(io.BytesIO(content)) as image:
                            if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width * image.height > 25_000_000:
                                raise ValueError('Dùng ảnh PNG/JPEG/WebP tối đa 25 triệu điểm ảnh.')
                            image.verify()
                    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
                        raise ValueError('URL không trả về ảnh PNG/JPEG/WebP hợp lệ.') from exc
                    return bytes(content)
            raise ValueError('URL chuyển hướng quá nhiều lần. Dùng link ảnh trực tiếp.')
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise ValueError('Không tải được ảnh từ URL. Kiểm tra link ảnh hoặc tải từ máy.') from exc
    finally:
        await resolver.close()
