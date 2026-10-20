from pydantic import BaseModel, Field, model_validator
from typing import Optional, Literal
from agent.models.enums import RequestType, Orientation
from agent.models.video_model import VideoModel


class RequestCreate(BaseModel):
    provider: Literal["flow", "muse2api"] = "flow"
    provider_model: Optional[str] = Field(default=None, min_length=1, max_length=160, pattern=r'^\S+$')
    type: RequestType
    orientation: Optional[Orientation] = None
    scene_id: Optional[str] = None
    character_id: Optional[str] = None
    project_id: Optional[str] = None
    video_id: Optional[str] = None
    source_media_id: Optional[str] = None
    video_model: Optional[VideoModel] = None

    @model_validator(mode="after")
    def check_required_fields(self) -> "RequestCreate":
        req_type = self.type
        if self.provider == 'muse2api':
            from agent.services.muse_media import SUPPORTED
            if req_type not in SUPPORTED or self.video_model:
                raise ValueError('Muse chỉ hỗ trợ ảnh và video từ ảnh đầu; không dùng model Google Flow.')
        elif self.provider_model:
            raise ValueError('provider_model chỉ dùng cho Muse.')
        if self.video_model and req_type not in ('GENERATE_VIDEO', 'REGENERATE_VIDEO', 'GENERATE_VIDEO_OMNI'):
            raise ValueError('video_model chỉ dùng cho clip từ ảnh đầu.')
        if req_type == 'GENERATE_VIDEO_OMNI' and self.video_model not in (None, 'abra_i2v_8s'):
            raise ValueError('GENERATE_VIDEO_OMNI cần model Omni.')
        if req_type in ("GENERATE_CHARACTER_IMAGE", "REGENERATE_CHARACTER_IMAGE", "EDIT_CHARACTER_IMAGE"):
            if not self.character_id:
                raise ValueError(f"character_id is required for {req_type}")
            if not self.project_id:
                raise ValueError(f"project_id is required for {req_type}")
        elif req_type in ("GENERATE_IMAGE", "REGENERATE_IMAGE", "EDIT_IMAGE",
                          "GENERATE_VIDEO", "GENERATE_VIDEO_OMNI", "REGENERATE_VIDEO", "GENERATE_VIDEO_REFS", "UPSCALE_VIDEO"):
            if not self.scene_id:
                raise ValueError(f"scene_id is required for {req_type}")
            if not self.project_id:
                raise ValueError(f"project_id is required for {req_type}")
            if not self.video_id:
                raise ValueError(f"video_id is required for {req_type}")
        return self


class Request(BaseModel):
    id: str
    provider: Literal["flow", "muse2api"] = "flow"
    provider_model: Optional[str] = None
    project_id: Optional[str] = None
    video_id: Optional[str] = None
    scene_id: Optional[str] = None
    character_id: Optional[str] = None
    type: str
    orientation: Optional[str] = None
    status: str = "PENDING"
    request_id: Optional[str] = None
    media_id: Optional[str] = None
    output_url: Optional[str] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    source_media_id: Optional[str] = None
    video_model: Optional[VideoModel] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
