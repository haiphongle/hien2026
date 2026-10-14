export interface NarrationPreferences {
  engine: 'omnivoice' | 'vieneu' | 'kokoro' | 'qwen3'
  qwenLanguage: string
  nativePresetId: string
  voiceSampleId: string
  template: string
  instruct: string
  speed: number
  sourceVolume: number
  narrationVolume: number
  burnSubtitles: boolean
  invalidatedSceneIds: string[]
  jobId: string | null
  autoApply?: boolean
  autoApplyJobId?: string | null
}

export const narrationDefaults: NarrationPreferences = {
  engine: 'omnivoice', qwenLanguage: 'en', nativePresetId: '',
  voiceSampleId: '', template: '', instruct: 'male, young adult, moderate pitch',
  speed: 1, sourceVolume: 0.35, narrationVolume: 1, burnSubtitles: true,
  invalidatedSceneIds: [], jobId: null,
}

export function parseNarrationPreferences(raw: string | null): NarrationPreferences | null {
  if (!raw) return null
  try {
    const data = JSON.parse(raw)
    if (!data || typeof data !== 'object' || Array.isArray(data)) return null
    const bounded = (key: keyof NarrationPreferences, min: number, max: number) =>
      typeof data[key] === 'number' && Number.isFinite(data[key]) && data[key] >= min && data[key] <= max
        ? data[key] : narrationDefaults[key] as number
    const voiceSampleId = typeof data.voiceSampleId === 'string' && /^[a-z0-9_-]{1,64}$/.test(data.voiceSampleId) ? data.voiceSampleId : ''
    return {
      engine: data.engine === 'vieneu' || data.engine === 'kokoro' || data.engine === 'qwen3' ? data.engine : 'omnivoice',
      qwenLanguage: typeof data.qwenLanguage === 'string' && /^(zh|en|ja|ko|de|fr|ru|pt|es|it)$/.test(data.qwenLanguage) ? data.qwenLanguage : 'en',
      nativePresetId: typeof data.nativePresetId === 'string' && /^(v3_[0-9a-f]{16}|kokoro_[ab][fm]_[a-z]+|qwen3_(zh|en|ja|ko|de|fr|ru|pt|es|it)_[a-z_]+)$/.test(data.nativePresetId) ? data.nativePresetId : '',
      voiceSampleId,
      template: !voiceSampleId && typeof data.template === 'string' && /^[a-zA-Z0-9_-]{1,64}$/.test(data.template) ? data.template : '',
      instruct: typeof data.instruct === 'string' ? data.instruct.slice(0, 200) : narrationDefaults.instruct,
      speed: bounded('speed', 0.5, 1.5), sourceVolume: bounded('sourceVolume', 0, 1), narrationVolume: bounded('narrationVolume', 0, 1),
      burnSubtitles: typeof data.burnSubtitles === 'boolean' ? data.burnSubtitles : true,
      invalidatedSceneIds: Array.isArray(data.invalidatedSceneIds) ? data.invalidatedSceneIds.filter((id: unknown) => typeof id === 'string') : [],
      jobId: typeof data.jobId === 'string' && /^[a-f0-9-]{36}$/i.test(data.jobId) ? data.jobId : null,
      autoApply: typeof data.autoApply === 'boolean' ? data.autoApply : true,
      autoApplyJobId: typeof data.autoApplyJobId === 'string' && data.autoApplyJobId === data.jobId ? data.jobId : null,
    }
  } catch { return null }
}

export function loadNarrationPreferences(videoId: string): NarrationPreferences | null {
  try { return parseNarrationPreferences(localStorage.getItem(`flowkit:narration:${videoId}`)) } catch { return null }
}

export function saveNarrationPreferences(videoId: string, preferences: NarrationPreferences) {
  if (!videoId) return
  try { localStorage.setItem(`flowkit:narration:${videoId}`, JSON.stringify(preferences)) } catch { /* Storage may be disabled. */ }
}
