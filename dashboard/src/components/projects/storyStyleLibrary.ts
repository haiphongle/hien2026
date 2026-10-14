import type { StoryCharacter, Motion } from './stickmanOptions'

export type StyleCategory = '2d' | '3d' | 'drawing' | 'stick'
export const styleCategories: Record<StyleCategory, string> = { '2d': '2D', '3d': '3D', drawing: 'Tranh vẽ', stick: 'Người que' }
export interface StoryPreset {
  id: string; name: string; shape: 'stick' | 'solid' | 'illustrated'; local_actor: boolean
  category?: StyleCategory; description?: string; preview?: string; background?: string; ink?: string
  collection?: 'html'; anatomy?: 'stick' | 'human' | 'semi' | 'chibi'; background_type?: 'white' | 'black' | 'scene'
  sample_prompt?: string; preview_override?: boolean
}
export interface StyleProfile {
  id: string; name: string; image_url: string; style_instruction: string; negative_prompt: string; character_design: string
  status?: 'DRAFT' | 'PUBLISHED'; revision?: number; category?: StyleCategory; description?: string
  default_character_id?: string | null; scene_action?: string; orientation?: 'VERTICAL' | 'HORIZONTAL'
}
export interface StyleChoice { preset: StoryPreset; profile?: StyleProfile }
export const sampleAction = 'Character A places a cup on a wooden table beside a window. A small plant, a chair and a shelf create depth. Medium-wide composition.'
const baseStyles: Record<StyleCategory, string> = { '2d': 'cartoon_story', '3d': 'claymation', drawing: 'stickman_pencil', stick: 'stickman_chalk' }

export function profileChoice(profile: StyleProfile, presets: StoryPreset[]): StyleChoice | undefined {
  const preset = presets.find(p => p.id === baseStyles[profile.category || 'drawing'])
  return preset ? { preset, profile } : undefined
}
export function styleKey(choice: StyleChoice) { return choice.profile ? `mine:${choice.profile.id}` : `builtin:${choice.preset.id}` }
export function styleSearch(name: string) { return name.normalize('NFD').replace(/[\u0300-\u036f]/g, '').replace(/[đĐ]/g, 'd').toLocaleLowerCase() }
export function filterStyles(choices: StyleChoice[], query: string, category: string, favorites: string[], onlyFavorites: boolean) {
  return choices.filter(c => (!category || (c.profile?.category || c.preset.category) === category)
    && (!onlyFavorites || favorites.includes(styleKey(c)))
    && styleSearch(`${c.profile?.name || c.preset.name} ${c.profile?.description || c.preset.description || ''}`).includes(styleSearch(query)))
}
export function styleChange(choice: StyleChoice, policy: 'keep' | 'new', current: {
  character: string; secondCharacter: string; designs: StoryCharacter[]; motion: Motion
}) {
  return { style: choice.preset.id, styleProfile: choice.profile?.id || '',
    ...(policy === 'new' ? { character: choice.profile?.default_character_id || '', secondCharacter: '', designs: [] } :
      { character: current.character, secondCharacter: current.secondCharacter, designs: current.designs }),
    motion: current.motion === 'talk' && (choice.profile || !choice.preset.local_actor) ? 'ai' as const : current.motion }
}
export function styleSettings(profile: StyleProfile) {
  return { name: profile.name, description: profile.description || '', category: profile.category || 'drawing',
    style_instruction: profile.style_instruction, negative_prompt: profile.negative_prompt, character_design: profile.character_design,
    default_character_id: profile.default_character_id || null, scene_action: profile.scene_action || sampleAction,
    orientation: profile.orientation || 'VERTICAL' }
}
export async function imageBase64(image: File) {
  if (image.size > 10*1024*1024 || !['image/png', 'image/jpeg', 'image/webp'].includes(image.type)) throw new Error('Chọn PNG/JPEG/WebP tối đa 10 MB.')
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1])
    reader.onerror = () => reject(new Error('Không đọc được ảnh.'))
    reader.readAsDataURL(image)
  })
}
