"""Bounded HTTP transport for muse2api. Never retry paid submissions implicitly."""
import asyncio
import base64
import io
import json
import time
from urllib.parse import urljoin, urlsplit, quote

import httpx
from PIL import Image
from agent.services import muse_settings


class MuseError(ValueError):
    pass


class MuseTaskFailed(MuseError):
    """A confirmed terminal result permits a new submission on explicit retry."""


def image_data(content: bytes) -> str:
    if len(content) > 32 * 1024 * 1024:
        raise MuseError('Ảnh tham chiếu quá 32 MB.')
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.width * image.height > 24_000_000:
                raise MuseError('Ảnh tham chiếu quá 24 triệu pixel.')
            image.verify()
            mime = Image.MIME.get(image.format, 'image/png')
    except (OSError, SyntaxError) as exc:
        raise MuseError('Tư liệu tham chiếu không phải ảnh hợp lệ.') from exc
    return f'data:{mime};base64,' + base64.b64encode(content).decode()


class MuseClient:
    def __init__(self, settings=None, *, transport=None):
        self.settings = settings or muse_settings.load()
        self.transport = transport

    def _check(self):
        if not self.settings.enabled or not self.settings.api_key:
            raise MuseError('Muse chưa kết nối. Mở Cài đặt → Muse để nhập URL gateway và khóa API.')

    async def request(self, method, path, *, payload=None, timeout=30):
        self._check()
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=timeout, follow_redirects=False) as client:
                response = await client.request(method, self.settings.base_url + path, json=payload,
                    headers={'Authorization': 'Bearer ' + self.settings.api_key})
            if response.status_code in {401, 403}:
                raise MuseError('Muse từ chối kết nối. Kiểm tra khóa gateway và phiên đăng nhập tài khoản.')
            if response.status_code in {402, 429}:
                raise MuseError('Muse hết hạn mức hoặc đang giới hạn yêu cầu. Kiểm tra tài khoản rồi tiếp tục phần còn thiếu.')
            if response.status_code >= 300:
                raise MuseError(f'Muse trả HTTP {response.status_code}. Kiểm tra gateway; chưa tự gửi lại để tránh tính phí hai lần.')
            data = response.json()
            if not isinstance(data, dict):
                raise MuseError('Gateway trả dữ liệu không hợp lệ; chưa áp dụng kết quả.')
            return data
        except httpx.TimeoutException as exc:
            raise MuseError('Muse quá thời gian chờ. Yêu cầu có thể vẫn đang chạy ở Muse; kiểm tra trước khi thử lại.') from exc
        except httpx.RequestError as exc:
            raise MuseError('Không kết nối được muse2api. Khởi động gateway và kiểm tra URL trong Cài đặt.') from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MuseError('Gateway không trả JSON. Kiểm tra URL muse2api trong Cài đặt.') from exc

    async def readiness(self):
        result = await self.request('GET', '/readyz')
        driver = result.get('driver')
        if isinstance(driver, dict):
            driver = driver.get('driver') or driver.get('name') or driver.get('type')
        if driver == 'mock':
            raise MuseError('Gateway đang dùng mock (kết quả giả). Chuyển sang browser và kết nối tài khoản Muse trước khi tạo.')
        if driver != 'browser':
            raise MuseError('Gateway chưa xác nhận driver browser. Kiểm tra /readyz và cập nhật muse2api.')
        if isinstance(result.get('driver'), dict) and result['driver'].get('ok') is False:
            raise MuseError('Trình duyệt của gateway Muse đã mất kết nối. Khởi động lại gateway rồi kiểm tra lại; chưa gửi tác vụ tạo.')
        if result.get('ready') is not True or result.get('status') in {'not_ready', 'error'}:
            raise MuseError('Gateway chưa sẵn sàng. Kiểm tra tài khoản Muse đã đăng nhập.')
        return result

    async def models(self):
        result = await self.request('GET', '/v1/models')
        return [{'id': row['id'], 'label': row['id'], 'kind': row.get('kind'), 'alias_of': row.get('alias_of')} for row in result.get('data', [])
                if isinstance(row, dict) and isinstance(row.get('id'), str)]

    async def validate_model(self, model, kind):
        catalog = await self.models()
        selected = next((m for m in catalog if m['id'] == model), None)
        if not selected or selected['kind'] != kind:
            raise MuseError(f'Model Muse không thuộc nhóm {kind} trong danh sách gateway. Kiểm tra model trong Cài đặt; chưa gửi yêu cầu tạo.')

    async def chat(self, prompt, schema, *, model=None, images=()):
        await self.readiness()
        model = model or self.settings.chat_model
        await self.validate_model(model, 'chat')
        content = [{'type': 'text', 'text': prompt + '\nReturn only JSON matching this schema:\n' + json.dumps(schema)}]
        for path in images:
            content.append({'type': 'image_url', 'image_url': {'url': image_data(await asyncio.to_thread(path.read_bytes))}})
        result = await self.request('POST', '/v1/chat/completions', timeout=self.settings.timeout_seconds,
            payload={'model': model, 'messages': [{'role': 'user', 'content': content}], 'stream': False})
        try:
            answer = result['choices'][0]['message']['content']
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError()
            return answer
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise MuseError('Muse chưa trả nội dung hợp lệ. Chưa áp dụng kết quả.') from exc

    async def wait(self, path, task_id, *, progress=None):
        deadline = time.monotonic() + self.settings.timeout_seconds
        while time.monotonic() < deadline:
            data = await self.request('GET', path + '/' + quote(task_id, safe=''))
            status = data.get('status')
            if status in {'succeeded', 'completed'}:
                return data.get('result') or data
            if status in {'failed', 'cancelled', 'interrupted', 'expired'}:
                raise MuseTaskFailed(f'Tác vụ Muse {status}. Mở Muse/gateway để xem lý do; kết quả hoàn tất trước đó được giữ.')
            if progress:
                await progress(status or 'processing')
            await asyncio.sleep(3)
        raise MuseError('Muse chưa hoàn tất trong thời gian chờ. Đã lưu mã tác vụ; bấm tiếp tục để kiểm tra lại mà không tạo trùng.')

    async def download(self, url, *, limit=256 * 1024 * 1024):
        url = urljoin(self.settings.base_url + '/', url)
        origin = urlsplit(self.settings.base_url)
        # Do not forward the gateway key to media CDNs, including on redirects.
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=90, follow_redirects=False) as client:
                for _ in range(5):
                    parsed = urlsplit(url)
                    same_origin = (parsed.scheme, parsed.netloc) == (origin.scheme, origin.netloc)
                    if parsed.scheme not in {'http', 'https'} or parsed.username or (not same_origin and parsed.scheme != 'https'):
                        raise MuseError('Muse trả địa chỉ tải tư liệu không hợp lệ.')
                    headers = {'Authorization': 'Bearer ' + self.settings.api_key} if same_origin else {}
                    async with client.stream('GET', url, headers=headers) as response:
                        if response.is_redirect:
                            url = urljoin(url, response.headers.get('location', ''))
                            continue
                        if response.status_code != 200:
                            raise MuseError(f'Không tải được tư liệu Muse (HTTP {response.status_code}).')
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > limit:
                                raise MuseError('Tư liệu Muse vượt giới hạn tải của một cảnh.')
                        return bytes(content)
            raise MuseError('Địa chỉ tư liệu Muse chuyển hướng quá nhiều lần.')
        except httpx.RequestError as exc:
            raise MuseError('Tải tư liệu Muse bị gián đoạn. Mã tác vụ vẫn được lưu để tải lại.') from exc
