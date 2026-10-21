"""Versioned edit decisions. Media paths are resolved by the server catalog."""
from agent.media_limits import MAX_DURATION, MAX_TIMELINE_CLIPS, MAX_AUDIO_CLIPS
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from agent.models.editing import TextStyle, ColorSettings, EditRegion, FrameSettings


class TimelineModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class ImageEffect(TimelineModel):
    preset: Literal['gentle', 'reflective', 'story', 'review', 'none'] = 'gentle'
    movement: Literal['auto', 'still', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right', 'drift_up', 'drift_down'] = 'auto'
    intensity: Literal['light', 'medium', 'strong'] = 'light'
    focus_x: float = Field(default=.5, ge=0, le=1)
    focus_y: float = Field(default=.5, ge=0, le=1)
    fit: Literal['cover', 'contain'] = 'cover'


class Track(TimelineModel):
    id: UUID
    name: str = Field(max_length=80)
    kind: Literal['video', 'overlay', 'audio', 'caption', 'text', 'effect']
    locked: bool = False
    muted: bool = False
    solo: bool = False
    role: Literal['original', 'music', 'voice', 'effects'] = 'original'
    purpose: Literal['standard', 'overlay'] = 'standard'


class GainPoint(TimelineModel):
    time: float = Field(ge=0, le=MAX_DURATION)
    value: float = Field(ge=0, le=2)


class Transition(TimelineModel):
    kind: Literal['dissolve', 'blur', 'black', 'slide', 'light'] = 'dissolve'
    duration: float = Field(default=.4, ge=.1, le=2)


class TimelineCaption(TextStyle):
    size: float = Field(default=3.5, ge=1, le=30)
    # Read old scaled titles without changing their appearance.
    layout_size: float | None = Field(default=None, ge=1, le=30)
    box_width: float | None = Field(default=None, ge=.02, le=1)
    box_height: float | None = Field(default=None, ge=.02, le=1)
    x: float = Field(default=.5, ge=0, le=1)
    y: float = Field(default=.85, ge=0, le=1)
    opacity: float = Field(default=1, ge=0, le=1)
    hop_interval: Literal[0, 3, 5, 10, 15] = 0
    hop_offset: float = Field(default=0, ge=0, le=MAX_DURATION * 2)

    @model_validator(mode='after')
    def text_box(self):
        if (self.box_width is None) != (self.box_height is None):
            raise ValueError('Khung chữ cần cả chiều rộng và chiều cao.')
        return self


class Ducking(TimelineModel):
    enabled: bool = False
    amount: float = Field(default=.25, ge=0, le=1)
    attack: float = Field(default=.2, ge=.01, le=2)
    release: float = Field(default=.35, ge=.01, le=3)
    original_amount: float = Field(default=1, ge=0, le=1)
    detect_pauses: bool = False


class VideoOverlay(TimelineModel):
    x: float = Field(default=.66, ge=0, le=1)
    y: float = Field(default=.61, ge=0, le=1)
    width: float = Field(default=.3, ge=.05, le=1)
    height: float = Field(default=.35, ge=.05, le=1)
    fit: Literal['contain', 'cover'] = 'cover'
    flip: bool = False
    hidden: bool = False
    layout: Literal['free', 'split'] = 'free'
    border: float = Field(default=0, ge=0, le=2)  # percent of output height
    border_color: str = Field(default='#ffffff', pattern=r'^#[0-9a-fA-F]{6}$')
    shape: Literal['rectangle', 'rounded', 'circle'] = 'rectangle'
    radius: float = Field(default=12, ge=0, le=50)
    entrance: float = Field(default=0, ge=0, le=2)
    entrance_offset: float = Field(default=0, ge=0, le=MAX_DURATION * 2)

    @model_validator(mode='after')
    def bounds(self):
        if self.x + self.width > 1.000001 or self.y + self.height > 1.000001:
            raise ValueError('Khung video chèn phải nằm trong khung hình.')
        return self


class Clip(TimelineModel):
    id: UUID
    asset_id: UUID | None = None
    track_id: UUID
    start: float = Field(ge=0, le=MAX_DURATION)
    source_in: float = Field(default=0, ge=0, le=MAX_DURATION)
    duration: float = Field(gt=0, le=MAX_DURATION)
    gain: float = Field(default=1, ge=0, le=2)
    muted: bool = False
    transition: Transition | None = None
    image_effect: ImageEffect | None = None
    image_span: float | None = Field(default=None, gt=0, le=MAX_DURATION)
    overlay: VideoOverlay | None = None
    group: UUID | None = None
    anchor: UUID | None = None
    speed: float = Field(default=1, ge=.5, le=2)
    fade_in: float = Field(default=0, ge=0, le=MAX_DURATION)
    fade_out: float = Field(default=0, ge=0, le=MAX_DURATION)
    envelope: list[GainPoint] = Field(default_factory=list, max_length=64)
    text: str = Field(default='', max_length=1500)
    caption: TimelineCaption = Field(default_factory=TimelineCaption)
    effect: EditRegion | None = None
    colors: ColorSettings | None = None
    origin: str = Field(default='', max_length=100)

    @model_validator(mode='after')
    def automation(self):
        if self.fade_in + self.fade_out > self.duration + 1e-6:
            raise ValueError('Hai đoạn fade không được dài hơn đoạn âm thanh.')
        if any(p.time > self.duration for p in self.envelope) or any(a.time >= b.time for a, b in zip(self.envelope, self.envelope[1:])):
            raise ValueError('Điểm âm lượng cần tăng theo thời gian và nằm trong đoạn.')
        return self


class TimelineDocument(TimelineModel):
    version: Literal[1] = 1
    fps: float = Field(default=30, ge=1, le=60)
    width: int = Field(default=1280, ge=2, le=3840)
    height: int = Field(default=720, ge=2, le=3840)
    tracks: list[Track] = Field(min_length=2, max_length=12)
    clips: list[Clip] = Field(default_factory=list, max_length=MAX_TIMELINE_CLIPS)
    ducking: Ducking = Field(default_factory=Ducking)
    frame: FrameSettings | None = None
    legacy_imported: bool = False

    @model_validator(mode='after')
    def structure(self):
        if self.width % 2 or self.height % 2 or self.width * self.height > 3840 * 2160:
            raise ValueError('Khung hình cần có kích thước chẵn, tối đa 8.3 triệu pixel.')
        tracks = {t.id: t for t in self.tracks}
        if len(tracks) != len(self.tracks) or len({c.id for c in self.clips}) != len(self.clips):
            raise ValueError('ID đoạn hoặc lớp bị trùng.')
        if self.tracks[0].kind != 'video' or sum(t.kind == 'video' for t in self.tracks) != 1:
            raise ValueError('Bản dựng cần đúng một lớp hình ảnh ở đầu.')
        if sum(t.kind == 'overlay' for t in self.tracks) > 1:
            raise ValueError('Bản dựng hỗ trợ một lớp video chèn; có thể đặt nhiều đoạn nối tiếp.')
        video_ids = {c.id for c in self.clips if c.track_id in tracks and tracks[c.track_id].kind == 'video'}
        groups = {}
        for clip in self.clips:
            if clip.track_id not in tracks or clip.start + clip.duration > MAX_DURATION + 1e-6:
                raise ValueError(f'Đoạn nằm ngoài lớp hoặc vượt giới hạn {MAX_DURATION // 60} phút.')
            kind = tracks[clip.track_id].kind
            if (clip.image_effect is not None or clip.image_span is not None) and kind != 'video':
                raise ValueError('Hiệu ứng ảnh chỉ dùng trên lớp hình ảnh chính.')
            if clip.transition and kind != 'video':
                raise ValueError('Chuyển cảnh chỉ dùng trên lớp hình ảnh chính.')
            if kind in {'video', 'overlay', 'audio'} and not clip.asset_id:
                raise ValueError('Đoạn hình/tiếng cần có tư liệu nguồn.')
            if (kind == 'overlay') != (clip.overlay is not None):
                raise ValueError('Thiết lập khung video chỉ dùng trên lớp video chèn.')
            if kind == 'caption' and (clip.asset_id or not clip.text.strip()):
                raise ValueError('Đoạn phụ đề cần có chữ và không có file nguồn.')
            if kind == 'text' and clip.asset_id:
                raise ValueError('Đoạn chữ không có file nguồn.')
            if kind == 'effect' and (clip.asset_id or not (clip.effect or clip.colors)):
                raise ValueError('Đoạn hiệu ứng cần có vùng che hoặc thiết lập màu.')
            if clip.anchor and clip.anchor not in video_ids:
                raise ValueError('Đoạn liên quan cần liên kết với một đoạn hình còn tồn tại.')
            for value in (clip.start, clip.source_in, clip.duration):
                if abs(value * self.fps - round(value * self.fps)) > .001:
                    raise ValueError('Mốc cắt phải trùng khung hình của bản dựng.')
            if clip.group:
                groups.setdefault(clip.group, []).append(clip)
        for linked in groups.values():
            if len({(c.start, c.source_in, c.duration, c.asset_id, c.speed) for c in linked}) != 1:
                raise ValueError('Hình và tiếng liên kết cần cùng mốc thời gian.')
        picture = sorted((c for c in self.clips if tracks[c.track_id].kind == 'video'), key=lambda c: c.start)
        for a, b in zip(picture, picture[1:]):
            if a.start + a.duration > b.start + 1e-6:
                raise ValueError('Hai đoạn hình đang chồng nhau. Chuyển đoạn sang khoảng trống hoặc dùng Sắp xếp liền nhau.')
        if sum(tracks[c.track_id].kind == 'audio' for c in self.clips) > MAX_AUDIO_CLIPS:
            raise ValueError(f'Bản dựng tối đa {MAX_AUDIO_CLIPS} đoạn âm thanh.')
        if sum(tracks[c.track_id].kind == 'effect' for c in self.clips) > 32:
            raise ValueError('Bản dựng tối đa 32 vùng/hiệu ứng màu.')
        overlays = sorted((c for c in self.clips if tracks[c.track_id].kind == 'overlay'), key=lambda c: c.start)
        if len(overlays) > 32:
            raise ValueError('Bản dựng tối đa 32 đoạn video chèn.')
        if any(a.start + a.duration > b.start + 1e-6 for a, b in zip(overlays, overlays[1:])):
            raise ValueError('Các khung video chèn cần đặt nối tiếp, không chồng thời gian.')
        return self


class SaveTimeline(TimelineModel):
    revision: int = Field(ge=0)
    document: TimelineDocument


class RenderTimeline(TimelineModel):
    revision: int = Field(ge=0)
    preview: bool = False
    start: float = Field(default=0, ge=0, le=MAX_DURATION)
    end: float | None = Field(default=None, gt=0, le=MAX_DURATION)


class ImportResults(TimelineModel):
    revision: int = Field(ge=0)
    captions: bool = True
    voices: bool = True
    effects: bool = True
    legacy: bool = False
    source: Literal['auto', 'speech', 'scenes'] = 'auto'


class CaptionPreview(TimelineModel):
    text: str = Field(max_length=1500)
    caption: TimelineCaption
    width: int = Field(ge=2, le=3840)
    height: int = Field(ge=2, le=3840)

    @model_validator(mode='after')
    def pixel_budget(self):
        if self.width * self.height > 3840 * 2160:
            raise ValueError('Khung xem trước tối đa 8.3 triệu pixel.')
        return self


class VisualizeAssets(TimelineModel):
    asset_ids: list[UUID] = Field(min_length=1, max_length=8)


class WaveformAssets(TimelineModel):
    # The job processes these sequentially and reuses existing summaries.
    asset_ids: list[UUID] = Field(min_length=1, max_length=MAX_AUDIO_CLIPS)
