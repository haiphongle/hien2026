"""Contracts for the reusable Studio style library."""
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from agent.models.timeline import TimelineModel

StyleCategory = Literal['2d', '3d', 'drawing', 'stick']
SAMPLE_ACTION = 'Character A places a cup on a wooden table beside a window. A small plant, a chair and a shelf create depth. Medium-wide composition.'


class VisualStyle(TimelineModel):
    name: str = Field(min_length=3, max_length=80)
    style_instruction: str = Field(min_length=10, max_length=1400)
    negative_prompt: str = Field(min_length=3, max_length=400)
    character_design: str = Field(min_length=3, max_length=700)

    @field_validator('name', 'style_instruction', 'negative_prompt', 'character_design', mode='before')
    @classmethod
    def strip(cls, value):
        return value.strip() if isinstance(value, str) else value


class StyleUpload(TimelineModel):
    name: str = Field(min_length=3, max_length=80)
    image_base64: str
    voice_description: str = ''
    draft: bool = False  # Existing upload clients still save directly.


class StylePreviewUpload(TimelineModel):
    image_base64: str | None = Field(default=None, max_length=14_000_000)
    url: str | None = Field(default=None, max_length=4096)

    @field_validator('url', mode='before')
    @classmethod
    def strip_url(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode='after')
    def one_source(self):
        if bool(self.image_base64) == bool(self.url and self.url.strip()):
            raise ValueError('Chọn một ảnh từ máy hoặc dán một URL ảnh.')
        return self


class StyleSettings(VisualStyle):
    description: str = Field(default='', max_length=300)
    category: StyleCategory = 'drawing'
    default_character_id: UUID | None = None
    scene_action: str = Field(default=SAMPLE_ACTION, min_length=3, max_length=1000)
    orientation: Literal['VERTICAL', 'HORIZONTAL'] = 'VERTICAL'

    @field_validator('description', 'scene_action', mode='before')
    @classmethod
    def strip_scene(cls, value):
        return value.strip() if isinstance(value, str) else value


class StylePublish(TimelineModel):
    preview_signature: str = Field(min_length=10, max_length=64)
    use_preview_character: bool = False
