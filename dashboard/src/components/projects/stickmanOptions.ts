export const emotions = { happy: 'Vui', sad: 'Buồn', thinking: 'Suy nghĩ', surprised: 'Ngạc nhiên' }
export const gestures = { rest: 'Đứng yên', explain: 'Giải thích', wave: 'Vẫy tay', point: 'Chỉ tay', think: 'Đưa tay suy nghĩ' }
export const MAX_STORY_SCENES = 200
export type Emotion = keyof typeof emotions
export type Gesture = keyof typeof gestures
export type Motion = 'zoom' | 'pan' | 'still' | 'ai' | 'talk'
export const characterRoles = ['A', 'B', 'C', 'D', 'E', 'F'] as const
export type CharacterRole = typeof characterRoles[number]
export interface StickmanScene {
  narration: string; action: string; speaker: 'A' | 'B'; emotion: Emotion; gesture: Gesture
  cast?: CharacterRole[] | null; motion_beats?: string[]; camera?: string
  shot_kind?: 'story' | 'environment' | 'detail' | 'metaphor'; extras?: string; assets?: string[]
  environment?: string; staging?: string; lighting?: string; continuity?: string; cue_start?: number | null; cue_end?: number | null
}
export interface StoryCharacter {
  role: CharacterRole; name: string; description: string; reference_id?: string | null
  gender?: 'unknown' | 'male' | 'female'; age_group?: 'unknown' | 'child' | 'teen' | 'adult' | 'elder'; clothing?: string
  story_role?: string; relationship?: string; reference_status?: 'unselected' | 'compatible' | 'unknown' | 'conflict'; reference_note?: string
}
export interface StoryAsset { key: string; kind: 'location' | 'prop'; name: string; description: string; reference_id?: string | null }
export interface StoryAnalysis { summary: string; environment: string; era: string; continuity: string }
export const emptyAnalysis: StoryAnalysis = { summary: '', environment: '', era: '', continuity: '' }
export function normalizeAnalysis(value: unknown): StoryAnalysis {
  if (!value || typeof value !== 'object') return { ...emptyAnalysis }
  const source = value as Record<string, unknown>
  return Object.fromEntries(Object.entries({ summary: 700, environment: 400, era: 120, continuity: 400 })
    .map(([key, limit]) => [key, typeof source[key] === 'string' ? source[key].slice(0, limit) : ''])) as unknown as StoryAnalysis
}

export const visualChoices = {
  anatomy: { preset: 'Theo mẫu', stick: 'Người que', human: 'Người hoạt hình đầy đặn', semi: 'Người bán thực', chibi: 'Chibi' },
  background: { preset: 'Theo mẫu', white: 'Nền trắng', black: 'Nền đen', scene: 'Bối cảnh có đồ vật, chiều sâu' },
  rendering: { preset: 'Theo mẫu', minimal: 'Tối giản / Documentary', flat: 'Vector phẳng', animation: 'Hoạt hình 2D', hand: 'Vẽ tay', cinematic: 'Minh họa điện ảnh', folk: 'Tranh dân gian Việt' },
  costume: { preset: 'Theo nhân vật / mẫu', simple: 'Tối giản', modern: 'Hiện đại', vietnamese: 'Đời thường Việt Nam', traditional: 'Truyền thống Việt Nam', custom: 'Theo hồ sơ nhân vật' },
} as const
export interface VisualLocks {
  anatomy: keyof typeof visualChoices.anatomy; background: keyof typeof visualChoices.background
  rendering: keyof typeof visualChoices.rendering; costume: keyof typeof visualChoices.costume
  notes: string; context: string
}
export const defaultVisualLocks: VisualLocks = { anatomy: 'preset', background: 'preset', rendering: 'preset', costume: 'preset', notes: '', context: '' }
export function normalizeVisualLocks(value: unknown): VisualLocks {
  const result = { ...defaultVisualLocks }
  if (!value || typeof value !== 'object') return result
  const source = value as Record<string, unknown>
  for (const key of Object.keys(visualChoices) as (keyof typeof visualChoices)[]) {
    if (typeof source[key] === 'string' && Object.hasOwn(visualChoices[key], source[key])) Object.assign(result, { [key]: source[key] })
  }
  result.notes = typeof source.notes === 'string' ? source.notes.slice(0, 500) : ''
  result.context = typeof source.context === 'string' ? source.context.slice(0, 700) : ''
  return result
}

export type ShotDirection = Pick<StickmanScene, 'action' | 'motion_beats' | 'camera' | 'environment' | 'staging' | 'lighting' | 'continuity'>
export const cameraPresets = [
  { id: 'wide', name: 'Toàn cảnh', prompt: 'Wide establishing shot. Locked camera showing the setting and action.' },
  { id: 'close', name: 'Cận cảnh', prompt: 'Close-up of the key action or object. Locked camera, clear foreground detail.' },
  { id: 'push', name: 'Tiến gần', prompt: 'Medium shot. Camera pushes in toward the action.' },
  { id: 'pull', name: 'Lùi xa', prompt: 'Medium shot. Camera pulls back to reveal the surrounding setting.' },
  { id: 'pan', name: 'Lia ngang', prompt: 'Wide shot. Camera pans left to right following the action.' },
  { id: 'fixed', name: 'Máy cố định', prompt: 'Medium shot. Locked camera; only subjects and props move.' },
] as const
export const cameraStrengths = { gentle: 'Nhẹ nhàng', normal: 'Vừa phải', strong: 'Rõ nét' }
export type CameraStrength = keyof typeof cameraStrengths

export function cameraDirection(id: string, strength: CameraStrength = 'gentle') {
  const preset = cameraPresets.find(p => p.id === id)
  if (!preset) return ''
  const motion = ['push', 'pull', 'pan'].includes(id)
  return preset.prompt + (motion ? ` ${strength === 'gentle' ? 'Gentle, slow' : strength === 'strong' ? 'Pronounced but smooth' : 'Moderate, smooth'} movement; keep subjects in frame.` : '')
}

export function shotDirection(scene: ShotDirection): ShotDirection {
  const result: ShotDirection = { action: scene.action, motion_beats: scene.motion_beats?.map(beat => beat.replace(/^\s*\d+\s*[-–—]\s*\d+\s*s\s*:\s*/i, '').trim()), camera: scene.camera }
  for (const field of ['environment', 'staging', 'lighting', 'continuity'] as const) if (typeof scene[field] === 'string') result[field] = scene[field]
  return result
}

export function applyShotDirection(scene: StickmanScene, proposed: ShotDirection): StickmanScene {
  return { ...scene, ...shotDirection(proposed) }
}

export function rememberDirection(history: ShotDirection[], scene: StickmanScene) {
  const previous = shotDirection(scene)
  return [...history.filter(v => JSON.stringify(v) !== JSON.stringify(previous)), previous].slice(-5)
}
