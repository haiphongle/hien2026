"""Explicit model recovery for Studio; never changes global model settings."""
from agent import config
from agent.services.image_effects import uses_ai
from agent.models.video_model import VIDEO_MODEL_NAMES
from agent.services import flow_batch, speech_store, timeline


def options():
    # These describe FrameWeaver's first-frame transport, not account entitlements.
    return [{'id': key, 'name': name, 'duration_seconds': 8, 'input': 'first_frame',
             'orientations': ['VERTICAL', 'HORIZONTAL'], 'camera_control': 'prompt',
             'end_frame': False, 'access': 'unverified', 'credits': None}
            for key, name in VIDEO_MODEL_NAMES.items()]


def selected(body, project):
    return body.video_model or default_model(project, body.orientation)


def default_model(project, orientation):
    aspect = 'VIDEO_ASPECT_RATIO_PORTRAIT' if orientation == 'VERTICAL' else 'VIDEO_ASPECT_RATIO_LANDSCAPE'
    key = config.VIDEO_MODELS.get(project.get('user_paygate_tier', 'PAYGATE_TIER_TWO'), {}).get('frame_2_video', {}).get(aspect)
    return flow_batch.resolve_video_model(key)


def needs_clips(vid, body, root):
    return uses_ai(body) and needs_media(vid, body, root)


def needs_media(vid, body, root):
    pending = speech_store.read_json(root/'pending.json', {})
    return body.motion != 'talk' and (not timeline.load(vid) or bool(pending and not pending.get('ready')))


def recovery(job, body, project):
    model = job.get('video_model') if job.get('video_model') in VIDEO_MODEL_NAMES else selected(body, project)
    error = (job.get('error') or '').lower()
    clip_error = 'clip hoạt hình' in (job.get('stage') or '').lower() or 'model video' in error
    denied = job.get('error_code') == 'VIDEO_MODEL_ACCESS_DENIED' or clip_error and (
        'model_access_denied' in error or 'google flow từ chối quyền dùng model' in error)
    # Legacy failures did not record a model: first try the current ordinary Lite.
    target = ('abra_i2v_8s' if model == 'veo_3_1_i2v_lite' else 'veo_3_1_i2v_lite') if job.get('video_model') else 'veo_3_1_i2v_lite'
    return {'current_model': model, 'retry_model': target if denied else model, 'auto_switch': denied,
            'models': [{'id': key, 'name': name} for key, name in VIDEO_MODEL_NAMES.items()] if uses_ai(body) and body.video_provider == 'flow' else []}


def notice(previous, model, automatic):
    old = VIDEO_MODEL_NAMES.get(previous)
    name = VIDEO_MODEL_NAMES[model]
    return (f'Đã đổi model: {old} → {name}.' if old and previous != model else f'Thử lại bằng {name}.') + (
        ' Tự chọn model thay thế vì Google từ chối quyền truy cập.' if automatic else '') + ' Chỉ tạo tiếp clip còn thiếu; ảnh, giọng và clip hoàn tất được giữ lại.'
