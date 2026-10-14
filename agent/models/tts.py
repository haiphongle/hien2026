"""Pydantic models for TTS endpoints."""
from pydantic import BaseModel, Field
from typing import Optional, Literal
from uuid import UUID


class ShortenSceneRequest(BaseModel):
    scene_id: UUID
    frame: float = Field(gt=0, le=10800, allow_inf_nan=False)


class TTSGenerateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
    instruct: Optional[str] = Field(None, max_length=200)
    ref_audio: Optional[str] = Field(None, max_length=500)
    ref_text: Optional[str] = None
    speed: float = Field(1.0, ge=0.5, le=3.0)


class TTSGenerateResponse(BaseModel):
    audio_path: str
    duration: Optional[float] = None
    sample_rate: int = 24000


class NarrateVideoRequest(BaseModel):
    project_id: str
    engine: Literal["omnivoice", "vieneu", "kokoro", "qwen3"] = "omnivoice"
    preset_id: Optional[str] = Field(None, pattern=r"^(v3_[0-9a-f]{16}|kokoro_[ab][fm]_[a-z]+|qwen3_(zh|en|ja|ko|de|fr|ru|pt|es|it)_[a-z_]+)$")
    orientation: Literal["HORIZONTAL", "VERTICAL"] = "HORIZONTAL"
    speed: float = Field(1.0, ge=0.5, le=3.0)
    instruct: Optional[str] = Field(None, max_length=200)
    ref_audio: Optional[str] = Field(None, max_length=500)  # Path to voice template WAV
    ref_text: Optional[str] = None   # Transcript of ref_audio (auto-resolved from template)
    template: Optional[str] = Field(None, pattern=r"^[a-zA-Z0-9_-]{1,64}$")  # Voice template name
    voice_sample_id: Optional[str] = Field(None, pattern=r"^[a-z0-9_-]{1,64}$")  # Bundled VieNeu preview voice
    mix: bool = True
    force: bool = False
    sfx_volume: float = Field(0.4, ge=0.0, le=2.0)
    from_scene: Optional[int] = Field(None, ge=0)  # Start display_order (inclusive)
    to_scene: Optional[int] = Field(None, ge=0)    # End display_order (inclusive)


class NarrationJobRequest(NarrateVideoRequest):
    scene_ids: Optional[list[str]] = Field(None, max_length=100)


class NarrationJobCreated(BaseModel):
    job_id: str
    status: str


class SceneNarrationResult(BaseModel):
    scene_id: str
    display_order: int
    narrator_text: Optional[str] = None
    audio_path: Optional[str] = None
    audio_url: Optional[str] = None
    duration: Optional[float] = None
    status: str  # COMPLETED, SKIPPED, FAILED
    error: Optional[str] = None
    voice: Optional[dict] = None


class NarrationJobStatus(BaseModel):
    job_id: str
    video_id: str
    status: str
    total: int
    completed: int
    stage: str = "queued"
    engine: str = "omnivoice"
    voice_name: Optional[str] = None
    current_scene_id: Optional[str] = None
    scenes: list[SceneNarrationResult] = Field(default_factory=list)
    error: Optional[str] = None


class NarrateVideoResponse(BaseModel):
    video_id: str
    project_id: str
    scenes: list[SceneNarrationResult]
    scenes_narrated: int
    scenes_skipped: int
    scenes_failed: int
    total_narration_duration: Optional[float] = None


class VoiceTemplateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)  # Sample text to generate voice template
    instruct: str = Field(..., max_length=200)  # Voice design: "male, low pitch, young adult"
    name: str = Field("voice_template_1", pattern=r"^[a-zA-Z0-9_-]{1,64}$")  # Template name for saving
    speed: float = Field(1.0, ge=0.5, le=3.0)


class VoiceTemplateResponse(BaseModel):
    name: str
    audio_path: str
    text: str
    instruct: str
    duration: Optional[float] = None
    sample_rate: int = 24000
    source: str = "designed"


class VoiceTemplateListItem(BaseModel):
    name: str
    audio_path: str
    duration: Optional[float] = None
    source: str = "designed"
