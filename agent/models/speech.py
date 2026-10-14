"""Editable, versioned dialogue/SRT documents, separate from scene narration."""
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from agent.media_limits import MAX_SPEECH_CUES


class Cue(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    id: UUID = Field(default_factory=uuid4)
    start: float = Field(ge=0, le=86400)
    end: float = Field(gt=0, le=86400)
    text: str = Field(min_length=1, max_length=1500)
    speaker: str = Field(default="Người kể", min_length=1, max_length=80)
    voice_id: str = Field(default="", max_length=80)
    speed: float = Field(default=1, ge=0.5, le=2)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.end <= self.start:
            raise ValueError("Mốc kết thúc phải sau mốc bắt đầu.")
        return self


class SpeechDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(default=0, ge=0)
    mode: Literal["dialogue", "srt"] = "dialogue"
    timeline: str = ""
    edit_signature: str = Field(default='', max_length=64)
    cues: list[Cue] = Field(default_factory=list, max_length=MAX_SPEECH_CUES)

    @model_validator(mode="after")
    def valid_cues(self):
        ids = [cue.id for cue in self.cues]
        if len(ids) != len(set(ids)):
            raise ValueError("ID câu thoại bị trùng.")
        for before, after in zip(self.cues, self.cues[1:]):
            if after.start < before.end - 0.0001:
                raise ValueError("Các câu phải xếp theo thời gian và không chồng lên nhau.")
        return self


class ParseSpeechRequest(BaseModel):
    mode: Literal["dialogue", "srt"]
    text: str = Field(min_length=1, max_length=1000000)


class GenerateSpeechRequest(BaseModel):
    revision: int = Field(ge=1)
    cue_ids: list[UUID] | None = Field(default=None, max_length=MAX_SPEECH_CUES)
    force: bool = False
    automatic: bool = False
    language: Literal["vi", "en", "zh", "ja", "ko", "de", "fr", "ru", "pt", "es", "it"] = "vi"
    provider: Literal["configured", "codex", "claude", "agy"] = "configured"
    synopsis: str = Field(default="", max_length=4000)
    glossary: str = Field(default="", max_length=4000)
