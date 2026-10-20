"""Background descriptions are copied into stories, never resolved from a live template."""
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from agent.models.timeline import TimelineModel

TextProvider = Literal['configured', 'codex', 'claude', 'agy', 'muse2api']


class BackgroundDescription(TimelineModel):
    name: str = Field(min_length=3, max_length=80)
    description: str = Field(min_length=10, max_length=1400)

    @field_validator('name', 'description', mode='before')
    @classmethod
    def clean(cls, value):
        return value.strip() if isinstance(value, str) else value


class BackgroundSnapshot(BackgroundDescription):
    id: str = Field(min_length=3, max_length=80)
    revision: str = Field(min_length=1, max_length=64)
    image_id: UUID | None = None


class BackgroundWrite(BackgroundDescription):
    image_id: UUID | None = None
    revision: str | None = Field(default=None, max_length=64)


class BackgroundAnalyze(TimelineModel):
    name: str = Field(default='', max_length=80)
    description: str = Field(default='', max_length=1400)
    image_base64: str | None = Field(default=None, max_length=14_000_000)
    url: str | None = Field(default=None, max_length=4096)
    image_id: UUID | None = None
    text_provider: TextProvider = 'configured'
    force: bool = False

    @field_validator('name', 'description', 'url', mode='before')
    @classmethod
    def clean(cls, value):
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode='after')
    def source(self):
        if sum(bool(value) for value in (self.image_base64, self.url, self.image_id)) > 1:
            raise ValueError('Chọn một ảnh từ máy hoặc một URL ảnh.')
        if not (self.image_base64 or self.url or self.image_id or len(self.description) >= 3):
            raise ValueError('Thêm ảnh hoặc nhập mô tả bối cảnh trước khi phân tích.')
        return self


class BackgroundFavorite(TimelineModel):
    favorite: bool
