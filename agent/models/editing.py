"""Output layout is independent of the orientation used to generate Flow clips."""
from agent.media_limits import MAX_DURATION
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FrameSettings(EditModel):
    preset: Literal["source", "9:16", "16:9", "1:1", "4:5", "custom"] = "source"
    width: int = Field(default=1080, ge=240, le=3840)
    height: int = Field(default=1920, ge=240, le=3840)
    fit: Literal["contain", "cover"] = "contain"
    background: Literal["color", "blur"] = "color"
    color: str = Field(default="#101827", pattern=r"^#[0-9a-fA-F]{6}$")
    zoom: float = Field(default=1, ge=1, le=3)
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def even_dimensions(self):
        if self.preset == "custom" and (self.width % 2 or self.height % 2):
            raise ValueError("Kích thước phải là số chẵn để xuất MP4.")
        if self.preset == "custom" and self.width * self.height > 3840 * 2160:
            raise ValueError("Khung tùy chỉnh tối đa 8.3 triệu pixel.")
        return self


class CaptionStyle(EditModel):
    font_id: str = Field(default="sans", max_length=30)
    # Percentage of output height; scales consistently between preview and export.
    size: float = Field(default=3.5, ge=1, le=10)
    color: str = Field(default="#ffffff", pattern=r"^#[0-9a-fA-F]{6}$")
    outline_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    outline: int = Field(default=1, ge=0, le=8)
    background_color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$")
    background_opacity: float = Field(default=0.7, ge=0, le=1)
    align: Literal["left", "center", "right"] = "center"
    position: Literal["top", "middle", "bottom"] = "bottom"
    margin: float = Field(default=7.5, ge=2, le=20)


class TextStyle(CaptionStyle):
    """Titles can be larger than spoken subtitles; use the same font renderer."""
    size: float = Field(default=7, ge=1, le=30)


class ColorSettings(EditModel):
    brightness: float = Field(default=0, ge=-0.5, le=0.5)
    contrast: float = Field(default=1, ge=0.25, le=2)
    saturation: float = Field(default=1, ge=0, le=2)


class EditRegion(EditModel):
    id: UUID
    kind: Literal['blur', 'color', 'image', 'delogo'] = 'blur'
    x: float = Field(default=.65, ge=0, le=1)
    y: float = Field(default=.75, ge=0, le=1)
    width: float = Field(default=.25, ge=.01, le=1)
    height: float = Field(default=.15, ge=.01, le=1)
    start: float = Field(default=0, ge=0, le=MAX_DURATION)
    end: float = Field(default=3, gt=0, le=MAX_DURATION)
    strength: int = Field(default=12, ge=1, le=40)
    color: str = Field(default='#000000', pattern=r'^#[0-9a-fA-F]{6}$')
    opacity: float = Field(default=1, ge=0, le=1)
    image_id: UUID | None = None

    @model_validator(mode='after')
    def bounds(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError('Vùng chọn phải nằm trong khung hình.')
        if self.end <= self.start:
            raise ValueError('Mốc kết thúc vùng phải lớn hơn mốc bắt đầu.')
        return self


class EditingSettings(EditModel):
    frame: FrameSettings = Field(default_factory=FrameSettings)
    captions: CaptionStyle = Field(default_factory=CaptionStyle)
    mode: Literal["voice_subtitles", "voice", "subtitles", "original"] = "original"
    voice_source: Literal["speech", "scenes"] = "speech"
    source_volume: float = Field(default=1, ge=0, le=2)
    narration_volume: float = Field(default=1, ge=0, le=2)
    use_music: bool = True
    colors: ColorSettings = Field(default_factory=ColorSettings)
    regions: list[EditRegion] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def unique_regions(self):
        if len({r.id for r in self.regions}) != len(self.regions):
            raise ValueError('ID vùng bị trùng.')
        return self


class SaveEditingRequest(EditModel):
    revision: int = Field(ge=0)
    settings: EditingSettings


class EditingExportRequest(EditModel):
    revision: int = Field(ge=0)
    presets: list[Literal['source', '9:16', '16:9', '1:1', '4:5', 'custom']] = Field(default_factory=list, max_length=5)


class EditingClipRequest(EditModel):
    settings: EditingSettings
    start: float = Field(default=0, ge=0, le=MAX_DURATION)
    duration: float = Field(default=3, ge=1, le=5)


class ExportNameRequest(EditModel):
    name: str = Field(min_length=1, max_length=120)


class DeleteExportsRequest(EditModel):
    export_ids: list[UUID] = Field(min_length=1, max_length=100)


class ProPainterTrialRequest(EditModel):
    settings: EditingSettings
    region_id: UUID
    start: float = Field(default=0, ge=0, le=MAX_DURATION)
    duration: float = Field(default=2, ge=1, le=3)
    dilation: int = Field(default=4, ge=1, le=8)
    max_side: Literal[192, 320] = 192
    source_fingerprint: str | None = Field(default=None, max_length=200)


class ProPainterClipsRequest(EditModel):
    revision: int = Field(ge=0)
    clip_ids: list[UUID] = Field(min_length=1, max_length=100)
    region: EditRegion
    dilation: int = Field(default=4, ge=1, le=8)
    max_side: Literal[192, 320] = 320


class EditingPreviewRequest(EditModel):
    settings: EditingSettings
    time: float = Field(default=0, ge=0, le=MAX_DURATION)
    text: str = Field(default="Bình yên bắt đầu từ những điều giản dị.", max_length=300)


class CaptionPreviewRequest(EditModel):
    """Lightweight transparent layer; no video decoding or export job."""
    style: CaptionStyle
    width: int = Field(ge=2, le=3840)
    height: int = Field(ge=2, le=3840)
    text: str = Field(max_length=300)

    @model_validator(mode="after")
    def pixel_budget(self):
        if self.width * self.height > 3840 * 2160:
            raise ValueError("Khung xem trước tối đa 8.3 triệu pixel.")
        return self
