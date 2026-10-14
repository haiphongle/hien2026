export interface SpeechCue {
  id: string; start: number; end: number; text: string; speaker: string; voice_id: string; speed: number
}
export interface SpeechDocument {
  revision: number; mode: 'dialogue' | 'srt'; timeline: string; edit_signature?: string; cues: SpeechCue[]
}
export interface SpeechAudio {
  id: string; duration: number | null; status: string; audio_url: string | null; error: string | null
}
export interface SpeechJob {
  job_id: string; status: string; stage: string; total: number; completed: number
  current: string | null; error: string | null; results: Record<string, { ok: boolean; error?: string }>
  cancel_requested?: boolean
  mode?: 'automatic'; progress?: number
}
export interface SpeechState {
  document: SpeechDocument; timeline: string; duration: number; audio: SpeechAudio[]; job: SpeechJob | null
  edit_signature?: string
  characters: { id: string; name: string }[]
  automatic?: { original: SpeechDocument; revision: number; round: number } | null
}
export interface SpeechVoice {
  id: string; name: string; audio_url: string | null; gender?: string; region?: string
  status?: string; reference_url?: string; job?: SpeechJob | null
  languages?: string[]
}
export const SPEECH_LANGUAGES = { vi: 'Tiếng Việt', en: 'Tiếng Anh', zh: 'Tiếng Trung', ja: 'Tiếng Nhật',
  ko: 'Tiếng Hàn', de: 'Tiếng Đức', fr: 'Tiếng Pháp', ru: 'Tiếng Nga',
  pt: 'Tiếng Bồ Đào Nha', es: 'Tiếng Tây Ban Nha', it: 'Tiếng Ý' } as const
export const speechBusy = (job?: SpeechJob | null) => job?.status === 'QUEUED' || job?.status === 'PROCESSING'
export const speechStyle = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

export function speechError(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : String(cause)
  try {
    const detail = JSON.parse(message.slice(message.indexOf('{'))).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.slice(0, 3).map(item => `${item.loc?.slice(1).join(' → ') ?? ''}: ${item.msg}`).join('\n')
  } catch { /* Preserve non-API errors. */ }
  return message
}

export function loadSpeechMix(videoId: string) {
  const defaults = { narrationVolume: 1, sourceVolume: 0.35, burn: true }
  try {
    const data = JSON.parse(localStorage.getItem(`flowkit:speech-mix:${videoId}`) ?? '{}')
    const volume = (v: unknown, fallback: number) => typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 2 ? v : fallback
    return { narrationVolume: volume(data.narrationVolume, 1), sourceVolume: volume(data.sourceVolume, 0.35), burn: typeof data.burn === 'boolean' ? data.burn : true }
  } catch { return defaults }
}
