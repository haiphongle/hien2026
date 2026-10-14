"""Small contracts for the existing image, narration and timeline services."""
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator
from agent.materials import STORY_PRESETS
from agent.models.timeline import TimelineModel
from agent.models.video_model import VideoModel

_STYLES = {p['id']: p for p in STORY_PRESETS}
MAX_STORY_SCENES = 200
Motion = Literal['zoom', 'pan', 'still', 'ai', 'talk']
Emotion = Literal['happy', 'sad', 'thinking', 'surprised']
Gesture = Literal['rest', 'explain', 'wave', 'point', 'think']
CharacterRole = Literal['A', 'B', 'C', 'D', 'E', 'F']


class SceneAction(TimelineModel):
    action: str = Field(min_length=3, max_length=1000)
    speaker: Literal['A', 'B'] = 'A'
    emotion: Emotion = 'thinking'
    gesture: Gesture = 'explain'
    cast: list[CharacterRole] | None = Field(default=None, max_length=6)
    shot_kind: Literal['story', 'environment', 'detail', 'metaphor'] = 'story'
    extras: str = Field(default='', max_length=200)
    assets: list[str] = Field(default_factory=list, max_length=2)
    motion_beats: list[str] = Field(default_factory=list, max_length=3)
    camera: str = Field(default='', max_length=180)
    environment: str = Field(default='', max_length=400)
    staging: str = Field(default='', max_length=400)
    lighting: str = Field(default='', max_length=200)
    continuity: str = Field(default='', max_length=400)

    @field_validator('cast')
    @classmethod
    def distinct_cast(cls, value):
        if value is None:
            return value
        if len(value) != len(set(value)):
            raise ValueError('Nhân vật trong cảnh bị trùng.')
        return value

    @field_validator('motion_beats')
    @classmethod
    def three_beats(cls, value):
        if value and (len(value) != 3 or any(not 3 <= len(s.strip()) <= 450 for s in value)):
            raise ValueError('Chuyển động cần ba nhịp hành động, mỗi nhịp 3–450 ký tự.')
        return [s.strip() for s in value]

    @field_validator('action', mode='before')
    @classmethod
    def clean(cls, value):
        return value.strip() if isinstance(value, str) else value


class StoryScene(SceneAction):
    narration: str = Field(min_length=3, max_length=450)
    cue_start: int | None = Field(default=None, ge=1, le=200)
    cue_end: int | None = Field(default=None, ge=1, le=200)

    @field_validator('narration', mode='before')
    @classmethod
    def clean_narration(cls, value):
        return value.strip() if isinstance(value, str) else value


class SRTScene(SceneAction):
    cue_start: int = Field(ge=1, le=200, strict=True)
    cue_end: int = Field(ge=1, le=200, strict=True)


class CharacterDesign(TimelineModel):
    role: CharacterRole
    reference_id: UUID | None = None
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=3, max_length=700)
    gender: Literal['unknown', 'male', 'female'] = 'unknown'
    age_group: Literal['unknown', 'child', 'teen', 'adult', 'elder'] = 'unknown'
    clothing: str = Field(default='', max_length=350)
    story_role: str = Field(default='', max_length=100)
    relationship: str = Field(default='', max_length=300)
    reference_status: Literal['unselected', 'compatible', 'unknown', 'conflict'] = 'unselected'
    reference_note: str = Field(default='', max_length=300)


class StoryAnalysis(TimelineModel):
    summary: str = Field(default='', max_length=700)
    environment: str = Field(default='', max_length=400)
    era: str = Field(default='', max_length=120)
    continuity: str = Field(default='', max_length=400)


class VisualLocks(TimelineModel):
    anatomy: Literal['preset', 'stick', 'human', 'semi', 'chibi'] = 'preset'
    background: Literal['preset', 'white', 'black', 'scene'] = 'preset'
    rendering: Literal['preset', 'minimal', 'flat', 'animation', 'hand', 'cinematic', 'folk'] = 'preset'
    costume: Literal['preset', 'simple', 'modern', 'vietnamese', 'traditional', 'custom'] = 'preset'
    notes: str = Field(default='', max_length=500)
    context: str = Field(default='', max_length=700)

    @field_validator('notes', 'context', mode='before')
    @classmethod
    def clean_notes(cls, value):
        return value.strip() if isinstance(value, str) else value


class StoryAsset(TimelineModel):
    key: str = Field(pattern=r'^[LP][1-4]$')
    kind: Literal['location', 'prop']
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(min_length=3, max_length=700)
    reference_id: UUID | None = None


class StoryCharacters(TimelineModel):
    characters: list[CharacterDesign] = Field(default_factory=list, max_length=6)
    story_assets: list[StoryAsset] = Field(default_factory=list, max_length=4)
    analysis: StoryAnalysis = Field(default_factory=StoryAnalysis)

    @field_validator('characters')
    @classmethod
    def distinct_roles(cls, value):
        if len({s.role for s in value}) != len(value):
            raise ValueError('Vai nhân vật bị trùng.')
        return value

    @model_validator(mode='after')
    def assigned_references(self):
        assets = {a.key for a in self.story_assets}
        if len(assets) != len(self.story_assets):
            raise ValueError('Mã bối cảnh / đạo cụ bị trùng.')
        roles = {c.role for c in self.characters} | {'A', 'B'}  # Legacy plans may omit the bible.
        for scene in getattr(self, 'scenes', []):
            if not set(scene.cast or []) <= roles:
                raise ValueError('Nhân vật trong cảnh chưa có hồ sơ.')
            if not set(scene.assets) <= assets or len(scene.assets) != len(set(scene.assets)):
                raise ValueError('Bối cảnh / đạo cụ trong cảnh chưa có hồ sơ hoặc bị trùng.')
            if scene.shot_kind in {'environment', 'detail'} and (scene.cast != [] or scene.extras):
                raise ValueError('Cảnh chỉ bối cảnh / đồ vật không được gán người. Chọn cảnh câu chuyện nếu có người.')
        return self


class SRTStoryboard(StoryCharacters):
    title: str = Field(min_length=3, max_length=120)
    scenes: list[SRTScene] = Field(min_length=1, max_length=MAX_STORY_SCENES)


class Storyboard(StoryCharacters):
    title: str = Field(min_length=3, max_length=120)
    scenes: list[StoryScene] = Field(min_length=1, max_length=MAX_STORY_SCENES)

    @field_validator('title', mode='before')
    @classmethod
    def clean_title(cls, value):
        return value.strip() if isinstance(value, str) else value


class PlanRequest(StoryCharacters):
    source: str = Field(min_length=3, max_length=12000)
    scene_count: int | None = Field(default=None, ge=1, le=MAX_STORY_SCENES)
    language: Literal['vi', 'en'] = 'vi'
    dialogue: bool = False
    style: str = 'stickman_chalk'
    orientation: Literal['VERTICAL', 'HORIZONTAL'] = 'VERTICAL'
    style_profile_id: UUID | None = None
    character_id: UUID | None = None
    second_character_id: UUID | None = None
    auto_timing: bool = False
    visual_locks: VisualLocks = Field(default_factory=VisualLocks)

    @field_validator('style')
    @classmethod
    def known_style(cls, value):
        if value not in _STYLES:
            raise ValueError('Chọn phong cách có trong thư viện Studio.')
        return value

    @field_validator('source', mode='before')
    @classmethod
    def clean(cls, value):
        return value.strip() if isinstance(value, str) else value


class CreateRequest(Storyboard):
    style: str = 'stickman_chalk'
    orientation: Literal['VERTICAL', 'HORIZONTAL'] = 'VERTICAL'
    language: Literal['vi', 'en'] = 'vi'
    character_id: UUID | None = None
    engine: Literal['vieneu', 'kokoro', 'qwen3'] = 'vieneu'
    preset_id: str = Field(min_length=3, max_length=100)
    speed: float = Field(default=1, ge=.8, le=1.3)
    music_filename: str | None = Field(default=None, max_length=120)
    music_gain: float = Field(default=.25, ge=0, le=1)
    motion: Motion = 'zoom'
    dialogue: bool = False
    second_preset_id: str | None = Field(default=None, min_length=3, max_length=100)
    second_character_id: UUID | None = None
    speech_check: bool = False
    video_model: VideoModel | None = None
    style_profile_id: UUID | None = None
    preview_first: bool = False
    preview_all: bool = False
    visual_locks: VisualLocks = Field(default_factory=VisualLocks)

    @field_validator('style')
    @classmethod
    def known_style(cls, value):
        if value not in _STYLES:
            raise ValueError('Chọn phong cách có trong thư viện Studio.')
        return value

    @model_validator(mode='after')
    def voice_language(self):
        if self.motion == 'talk' and (any(c.role not in {'A', 'B'} for c in self.characters)
                or any(s.extras or s.assets or s.cast == [] for s in self.scenes)):
            raise ValueError('Câu chuyện nhiều nhân vật / bối cảnh cần ảnh hoặc Hoạt hình AI; mẫu vector chỉ hỗ trợ A/B.')
        if self.motion == 'talk' and not _STYLES[self.style].get('local_actor', True):
            raise ValueError('Phong cách này cần ảnh hoặc Hoạt hình AI · Google Flow; không dùng nhân vật vector thử nghiệm.')
        if self.motion == 'talk' and self.visual_locks.model_dump(exclude_defaults=True):
            raise ValueError('Khóa phong cách dùng với ảnh hoặc Hoạt hình AI; mẫu vector chỉ dùng tạo hình có sẵn.')
        if self.engine == 'vieneu' and self.language != 'vi' or self.engine in {'kokoro', 'qwen3'} and self.language != 'en':
            raise ValueError('Chọn bộ giọng phù hợp với ngôn ngữ lời đọc.')
        if any(s.speaker == 'B' for s in self.scenes) and (not self.dialogue or not self.second_preset_id):
            raise ValueError('Chọn giọng B trước khi tạo cảnh nhân vật B nói.')
        if self.dialogue and not self.second_preset_id:
            raise ValueError('Chọn giọng cho cả hai nhân vật.')
        return self


class SceneSuggestionRequest(PlanRequest):
    scene: StoryScene


class PromptPreviewRequest(PlanRequest):
    scenes: list[StoryScene] = Field(min_length=1, max_length=MAX_STORY_SCENES)
    motion: Motion = 'ai'


class ProductionTemplate(TimelineModel):
    name: str = Field(min_length=3, max_length=80)
    settings: CreateRequest


class RetryRequest(TimelineModel):
    video_model: VideoModel | None = None
    auto_switch: bool = True
    job_id: UUID | None = None
    approve_preview: bool = False
    regenerate_preview: bool = False
    preview_media_id: UUID | None = None
    preview_scene_id: UUID | None = None

    @model_validator(mode='after')
    def preview_action(self):
        if self.preview_scene_id and not self.regenerate_preview:
            raise ValueError('Chọn cảnh cần tạo lại ảnh, rồi duyệt toàn bộ ảnh để tiếp tục.')
        if self.regenerate_preview and (self.approve_preview or self.preview_media_id):
            raise ValueError('Chọn tạo ảnh mẫu khác hoặc duyệt ảnh, mỗi lần một thao tác.')
        if self.preview_media_id and not self.approve_preview:
            raise ValueError('Duyệt ảnh mẫu đã chọn để tiếp tục.')
        return self


class ActorChange(TimelineModel):
    asset_id: UUID
    emotion: Emotion
    gesture: Gesture
