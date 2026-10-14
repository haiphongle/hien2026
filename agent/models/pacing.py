"""Non-destructive silence cuts and sound events on the resulting timeline."""
from agent.media_limits import MAX_DURATION
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class AnalyzeRequest(Model):
    source: str = 'original'
    method: Literal['level', 'speech'] = 'level'
    threshold: float = Field(default=-40, ge=-65, le=-15)
    minimum: float = Field(default=.7, ge=.2, le=5)
    padding: float = Field(default=.18, ge=.05, le=.8)

    @field_validator('source')
    @classmethod
    def source_id(cls, value):
        return value if value == 'original' else str(UUID(value))


class SoundEvent(Model):
    id: UUID
    asset_id: UUID
    start: float = Field(default=0, ge=0, le=MAX_DURATION)
    volume: float = Field(default=.5, ge=0, le=2)


class PacingDraft(Model):
    source: str = 'original'
    plan_id: UUID | None = None
    cut_ids: list[UUID] = Field(default_factory=list, max_length=300)
    effects: list[SoundEvent] = Field(default_factory=list, max_length=40)
    base_volume: float = Field(default=1, ge=0, le=2)

    _source_id = field_validator('source')(AnalyzeRequest.source_id.__func__)


class SaveRequest(Model):
    revision: int = Field(ge=0)
    draft: PacingDraft


class RenderRequest(Model):
    revision: int = Field(ge=0)
    preview: bool = False
