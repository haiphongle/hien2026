"""Limits shared by media import, editing models and the dashboard."""
import json
from pathlib import Path

_limits = json.loads(Path(__file__).with_suffix(".json").read_text())
MAX_DURATION = _limits["duration_seconds"]
MAX_UPLOAD_BYTES = _limits["upload_bytes"]
MAX_TIMELINE_CLIPS = _limits["timeline_clips"]
MAX_AUDIO_CLIPS = _limits["audio_clips"]
MAX_SPEECH_CUES = _limits["speech_cues"]
