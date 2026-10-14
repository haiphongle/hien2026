export interface FrameSettings {
  preset: 'source' | '9:16' | '16:9' | '1:1' | '4:5' | 'custom'
  width: number; height: number; fit: 'contain' | 'cover'; background: 'color' | 'blur'
  color: string; zoom: number; x: number; y: number
}
export interface CaptionStyle {
  font_id: string; size: number; color: string; outline_color: string; outline: number
  background_color: string; background_opacity: number; align: 'left' | 'center' | 'right'
  position: 'top' | 'middle' | 'bottom'; margin: number
}

export const DEFAULT_FRAME: Readonly<FrameSettings> = {
  preset: 'source', width: 1080, height: 1920, fit: 'contain', background: 'color',
  color: '#000000', zoom: 1, x: .5, y: .5,
}

export const DEFAULT_CAPTION_STYLE: Readonly<CaptionStyle> = {
  font_id: 'sans', size: 3.5, color: '#ffffff', outline_color: '#000000', outline: 1,
  background_color: '#000000', background_opacity: .7, align: 'center', position: 'bottom', margin: 7.5,
}
export interface ColorSettings { brightness: number; contrast: number; saturation: number }
export const DEFAULT_COLORS: Readonly<ColorSettings> = { brightness: 0, contrast: 1, saturation: 1 }
export interface EditRegion {
  id: string; kind: 'blur' | 'color' | 'image' | 'delogo'
  x: number; y: number; width: number; height: number; start: number; end: number
  strength: number; color: string; opacity: number; image_id: string | null
}
export interface EditingSettings {
  frame: FrameSettings; captions: CaptionStyle
  mode: 'voice_subtitles' | 'voice' | 'subtitles' | 'original'
  voice_source: 'speech' | 'scenes'; source_volume: number; narration_volume: number; use_music: boolean
  colors?: ColorSettings; regions?: EditRegion[]
}
export interface EditingResult {
  url: string; width: number; height: number; duration: number; revision: number
  settings?: EditingSettings
  export_id?: string; created_at?: number; name?: string; bytes?: number
  range_start?: number; range_end?: number | null
}
export interface EditingState {
  revision: number; settings: EditingSettings; last_export: EditingResult | null
  exports?: EditingResult[]
  fonts: { id: string; name: string }[]; preview_available: boolean
  source: { width: number; height: number; duration: number } | null
  speech_cues: number; scene_texts: number; music: { filename: string; volume: number } | null
}

export function outputSize(frame: FrameSettings, source: EditingState['source']): [number, number] | null {
  if (frame.preset === 'source') return source ? [source.width - source.width % 2, source.height - source.height % 2] : null
  if (frame.preset === 'custom') return [frame.width, frame.height]
  const sizes: Record<string, [number, number]> = { '9:16': [1080, 1920], '16:9': [1920, 1080], '1:1': [1080, 1080], '4:5': [1080, 1350] }
  return sizes[frame.preset]
}

export function dragFrame(frame: FrameSettings, source: EditingState['source'], dx: number, dy: number): FrameSettings {
  const output = outputSize(frame, source)
  if (!source || !output) return frame
  const [ow, oh] = output
  const ratio = (frame.fit === 'cover' ? Math.max : Math.min)(ow / source.width, oh / source.height) * frame.zoom
  const sw = Math.max(2, Math.round(source.width * ratio / 2) * 2), sh = Math.max(2, Math.round(source.height * ratio / 2) * 2)
  const shift = (value: number, delta: number, scaled: number, target: number) => scaled === target ? value : Math.max(0, Math.min(1, value + delta * target / (target - scaled)))
  return { ...frame, x: shift(frame.x, dx, sw, ow), y: shift(frame.y, dy, sh, oh) }
}

/** Crop/pad rectangles in output pixels, matching the FFmpeg layout filter. */
export function frameGeometry(frame: FrameSettings, source: EditingState['source'], minimum = frame.preset === 'source' ? 2 : 240) {
  const output = outputSize(frame, source)
  if (!source || !output || !output.every(n => Number.isFinite(n) && n >= minimum && n <= 3840) || output[0] * output[1] > 3840 * 2160) return null
  const [width, height] = output
  const ratio = (frame.fit === 'cover' ? Math.max : Math.min)(width / source.width, height / source.height) * frame.zoom
  const sw = Math.max(2, Math.round(source.width * ratio / 2) * 2), sh = Math.max(2, Math.round(source.height * ratio / 2) * 2)
  const dw = Math.min(sw, width), dh = Math.min(sh, height)
  return { width, height, dw, dh,
    dx: Math.round((width - dw) * frame.x), dy: Math.round((height - dh) * frame.y),
    sx: Math.round((sw - dw) * frame.x) * source.width / sw,
    sy: Math.round((sh - dh) * frame.y) * source.height / sh,
    sourceWidth: dw * source.width / sw, sourceHeight: dh * source.height / sh }
}
