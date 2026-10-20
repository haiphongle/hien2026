"""Private, optional connection to the muse2api gateway (not the Muse website)."""
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator
from agent.config import BASE_DIR

SETTINGS_FILE = BASE_DIR / '.flowkit' / 'muse.json'


class MuseSettings(BaseModel):
    enabled: bool = False
    auto_start: bool = False
    base_url: str = 'http://127.0.0.1:18610'
    api_key: str = Field(default='', max_length=2048, repr=False)
    chat_model: str = 'muse-chat'
    image_model: str = 'muse-image'
    video_model: str = 'muse-video'
    timeout_seconds: int = Field(default=900, ge=60, le=3600)

    @field_validator('base_url')
    @classmethod
    def gateway_url(cls, value):
        value = value.strip().rstrip('/')
        if value.endswith('/v1'):
            value = value[:-3]
        url = urlsplit(value)
        if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('Nhập URL HTTP(S) của muse2api, không kèm mật khẩu hoặc tham số.')
        if url.hostname == 'muse.ai' or url.hostname.endswith('.muse.ai'):
            raise ValueError('muse.ai là trang đăng nhập. Nhập URL muse2api, ví dụ http://127.0.0.1:18610.')
        if url.scheme == 'http' and url.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('Gateway ngoài máy cần HTTPS để bảo vệ khóa API.')
        return value

    @field_validator('chat_model', 'image_model', 'video_model')
    @classmethod
    def model_name(cls, value):
        value = value.strip()
        if not value or len(value) > 160 or any(c.isspace() for c in value):
            raise ValueError('Tên model phải có 1–160 ký tự, không có khoảng trắng.')
        return value

    @field_validator('api_key')
    @classmethod
    def key(cls, value):
        value = value.strip()
        if any(c.isspace() for c in value):
            raise ValueError('Khóa API không được chứa khoảng trắng.')
        return value


def load() -> MuseSettings:
    if not SETTINGS_FILE.exists():
        return MuseSettings()
    try:
        return MuseSettings.model_validate_json(SETTINGS_FILE.read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('Không đọc được cấu hình Muse. Kiểm tra .flowkit/muse.json.') from exc


def configured() -> bool:
    try:
        settings = load()
        return settings.enabled and bool(settings.api_key)
    except ValueError:
        return False


def public(settings=None):
    settings = settings or load()
    return {**settings.model_dump(exclude={'api_key'}), 'has_key': bool(settings.api_key),
            'configured': settings.enabled and bool(settings.api_key)}


def save(values: dict):
    # An omitted key retains it. An explicitly empty key removes it.
    settings = MuseSettings.model_validate({**load().model_dump(), **values})
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.muse-', dir=SETTINGS_FILE.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(settings.model_dump(), handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, SETTINGS_FILE)
    finally:
        Path(name).unlink(missing_ok=True)
    return settings
