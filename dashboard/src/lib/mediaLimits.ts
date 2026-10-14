import limits from '../../../agent/media_limits.json' with { type: 'json' }

export const MAX_DURATION = limits.duration_seconds
export const MAX_TIMELINE_CLIPS = limits.timeline_clips
export const MAX_AUDIO_CLIPS = limits.audio_clips
export const MAX_SPEECH_CUES = limits.speech_cues
