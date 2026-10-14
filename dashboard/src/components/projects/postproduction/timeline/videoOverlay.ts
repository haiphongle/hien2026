import { MAX_DURATION } from '../../../../lib/mediaLimits.ts'
import type { Asset, Clip, Document, VideoOverlay } from './types.ts'
import { DEFAULT_FRAME, outputSize } from '../editingTypes.ts'

export const DEFAULT_OVERLAY: VideoOverlay = { x: .66, y: .61, width: .3, height: .35, fit: 'cover', flip: false, hidden: false, layout: 'free', border: 0, border_color: '#ffffff' }
const clamp = (n: number, min: number, max: number) => Math.max(min, Math.min(max, n))
export const mainPaneWidth = (width: number) => Math.max(2, Math.floor(width / 4) * 2)
export function overlayGeometry(value: VideoOverlay, width: number, height: number) {
  if (value.layout === 'split') return { x: mainPaneWidth(width), y: 0, width: width-mainPaneWidth(width), height }
  const w = Math.max(2, Math.floor(value.width*width/2)*2), h = Math.max(2, Math.floor(value.height*height/2)*2)
  return { x: Math.min(width-w,Math.round(value.x*width)), y: Math.min(height-h,Math.round(value.y*height)), width: w, height: h }
}
export function entranceOpacity(clip: Clip, time: number) {
  const value = clip.overlay
  return value?.entrance ? clamp((time-clip.start+(value.entrance_offset ?? 0))/value.entrance,0,1) : 1
}
export function moveOverlay(value: VideoOverlay, dx: number, dy: number): VideoOverlay {
  return { ...value, x: clamp(value.x + dx, 0, 1 - value.width), y: clamp(value.y + dy, 0, 1 - value.height) }
}
/** Corner resize keeps the frame's aspect ratio and the opposite corner fixed. */
export function resizeOverlay(value: VideoOverlay, corner: string, dx: number, dy: number): VideoOverlay {
  const left = corner.includes('w'), top = corner.includes('n')
  const scale = 1 + ((left ? -dx : dx) * value.width + (top ? -dy : dy) * value.height) / (value.width ** 2 + value.height ** 2)
  const maximum = Math.min((left ? value.x + value.width : 1 - value.x) / value.width, (top ? value.y + value.height : 1 - value.y) / value.height)
  const amount = clamp(scale, Math.max(.05 / value.width, .05 / value.height), maximum)
  const width = value.width * amount, height = value.height * amount
  return { ...value, width, height, x: left ? value.x + value.width - width : value.x, y: top ? value.y + value.height - height : value.y }
}
export function overlayPreset(value: VideoOverlay, preset: string): VideoOverlay {
  if (preset === 'split') return { ...value, layout: 'split', fit: 'contain' }
  const width = Math.min(.9, value.width), height = Math.min(.9, value.height)
  return { ...value, width, height, layout: 'free', x: preset.includes('left') ? .04 : .96 - width, y: preset.includes('top') ? .04 : .96 - height }
}
export function linkedAudio(doc: Document, clip: Clip) {
  return doc.clips.find(c => (c.id === clip.id || clip.group && c.group === clip.group) && doc.tracks.find(t => t.id === c.track_id)?.kind === 'audio')
}
export type ReviewPreset = 'corner' | 'split' | 'voice'
export function applyReviewPreset(doc: Document, id: string, preset: ReviewPreset): Document {
  const clip = doc.clips.find(c=>c.id===id)
  if (!clip?.overlay) throw new Error('Chọn một khung video chèn trước.')
  const audio = linkedAudio(doc, clip)
  if (doc.tracks.find(t=>t.id===clip.track_id)?.locked) throw new Error('Mở khóa lớp video chèn trước.')
  if (preset === 'voice' && !audio) throw new Error('Video này chưa có tiếng liên kết để làm lời kể.')
  const overlay = preset === 'split' ? { ...clip.overlay, layout: 'split' as const, fit: 'contain' as const, hidden: false, shape: 'rectangle' as const, entrance: 0, entrance_offset: 0 }
    : preset === 'voice' ? { ...clip.overlay, hidden: true }
    : { ...overlayPreset(clip.overlay,'bottom-right'), hidden: false, shape: 'rounded' as const, radius: 12, entrance: .3, entrance_offset: 0 }
  return { ...doc, clips: doc.clips.map(c=>c.id===id?{...c,overlay}:c),
    ...(audio ? { ducking: { amount: .25, attack: .2, release: .35, ...doc.ducking, enabled: true, original_amount: .2, detect_pauses: true } } : {}) }
}
export function insertOverlay(doc: Document, asset: Asset, at: number): Document {
  if (asset.kind !== 'video' || asset.missing) throw new Error('Chọn một video còn trong thư viện để chèn khung.')
  const existing = doc.tracks.find(t => t.kind === 'overlay')
  const track = existing ?? { id: crypto.randomUUID(), name: 'Video chèn', kind: 'overlay' as const, locked: false, muted: false }
  let audio = doc.tracks.find(t => t.kind === 'audio' && (t.purpose === 'overlay' || t.name === 'Tiếng video chèn'))
  const tracks = existing ? [...doc.tracks] : [...doc.tracks, track]
  if (!audio && asset.has_audio) {
    audio = { id: crypto.randomUUID(), name: 'Tiếng video chèn', kind: 'audio', purpose: 'overlay', role: 'voice', muted: false, locked: false }
    tracks.push(audio)
  }
  if (track.locked || asset.has_audio && audio?.locked) throw new Error('Mở khóa lớp video chèn và tiếng trước khi thêm.')
  const start = Math.round(Math.max(0, at) * doc.fps) / doc.fps
  const total = Math.max(0, ...doc.clips.map(c => c.start + c.duration))
  const next = doc.clips.filter(c => c.track_id === track.id && c.start >= start).sort((a, b) => a.start - b.start)[0]
  if (doc.clips.some(c => c.track_id === track.id && c.start <= start && c.start + c.duration > start)) throw new Error('Đặt vạch phát vào khoảng trống trên lớp Video chèn.')
  const duration = Math.floor(Math.min(asset.duration, MAX_DURATION - start, next ? next.start - start : Infinity, total > start ? total - start : Infinity) * doc.fps + 1e-6) / doc.fps
  if (duration < 1 / doc.fps) throw new Error('Chưa đủ khoảng trống để chèn video.')
  const [w, h] = outputSize(doc.frame ?? DEFAULT_FRAME, { width: doc.width, height: doc.height, duration: total })!
  const displayAspect = asset.width / asset.height * h / w
  let width = Math.min(.3, .35 * displayAspect), height = width / displayAspect
  const scale = Math.min(.9 / width, .9 / height, Math.max(1, .05 / width, .05 / height))
  width = clamp(width*scale,.05,.9); height = clamp(height*scale,.05,.9)
  const overlay = { ...DEFAULT_OVERLAY, width, height, x: .96 - width, y: .96 - height }
  const group = crypto.randomUUID(), base = { asset_id: asset.id, start, source_in: 0, duration, gain: 1, group }
  const clips: Clip[] = [{ ...base, id: crypto.randomUUID(), track_id: track.id, overlay }]
  if (asset.has_audio && audio) clips.push({ ...base, id: crypto.randomUUID(), track_id: audio.id })
  return { ...doc, tracks, clips: [...doc.clips, ...clips] }
}
