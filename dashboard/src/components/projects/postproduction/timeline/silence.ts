import type { Asset, Clip, Document, Visuals } from './types.ts'
import { silences } from './audio.ts'
import { length } from './operations.ts'

export interface TimeRange { start: number; end: number }
export interface SilenceRange extends TimeRange { id: string }
export type SilenceSource = 'voices' | 'original' | 'selection'
export interface SilenceOptions { source: SilenceSource; selectedId?: string; threshold: number; minimum: number; padding: number; trimEdges: boolean }
export const SILENCE_PRESETS = [
  { id: 'natural', name: 'Tự nhiên', threshold: -40, minimum: .7, padding: .16 },
  { id: 'review', name: 'Review nhanh', threshold: -35, minimum: .4, padding: .1 },
  { id: 'tight', name: 'Rất gọn', threshold: -30, minimum: .25, padding: .08 },
] as const

export function mergeRanges(ranges: TimeRange[]): TimeRange[] {
  const result: TimeRange[] = []
  for (const range of [...ranges].sort((a, b) => a.start - b.start)) {
    if (range.end <= range.start) continue
    const last = result.at(-1)
    if (last && range.start <= last.end + 1e-6) last.end = Math.max(last.end, range.end)
    else result.push({ ...range })
  }
  return result
}
function gaps(window: TimeRange, occupied: TimeRange[]) {
  const result: TimeRange[] = []
  let cursor = window.start
  for (const range of mergeRanges(occupied)) {
    const a = Math.max(window.start, range.start), b = Math.min(window.end, range.end)
    if (b <= a) continue
    if (a > cursor) result.push({ start: cursor, end: a })
    cursor = Math.max(cursor, b)
  }
  if (cursor < window.end) result.push({ start: cursor, end: window.end })
  return result
}
const span = (c: Clip): TimeRange => ({ start: c.start, end: c.start + c.duration })

/** Analyse source-level peaks, never the user's playback volume or backing music. */
export function planSilenceCuts(doc: Document, assets: Asset[], visuals: Record<string, Visuals>, options: SilenceOptions) {
  const tracks = new Map(doc.tracks.map(t => [t.id, t])), catalog = new Map(assets.map(a => [a.id, a]))
  const enabledAudio = doc.clips.filter(c => tracks.get(c.track_id)?.kind === 'audio' && !tracks.get(c.track_id)?.muted && !c.muted && c.gain > 0)
  const voices = enabledAudio.filter(c => tracks.get(c.track_id)?.role === 'voice' || catalog.get(c.asset_id ?? '')?.category === 'voice')
  const original = enabledAudio.filter(c => (tracks.get(c.track_id)?.role ?? 'original') === 'original' && !['music', 'sfx', 'voice'].includes(catalog.get(c.asset_id ?? '')?.category ?? ''))
  const selected = doc.clips.find(c => c.id === options.selectedId && catalog.get(c.asset_id ?? '')?.has_audio)
  const sources = options.source === 'voices' ? voices : options.source === 'original' ? original : selected ? [selected] : []
  const missingIds = [...new Set(sources.flatMap(c => options.source !== 'voices' && c.asset_id && !visuals[c.asset_id] ? [c.asset_id] : []))]
  const unavailable = sources.some(c => !catalog.has(c.asset_id ?? '') || catalog.get(c.asset_id ?? '')?.missing)
  const base = { ranges: [] as SilenceRange[], missingIds, sourceCount: sources.length, hasVoices: voices.length > 0,
    error: unavailable ? 'Có tư liệu bị mất. Nạp lại thư viện trước khi dò khoảng lặng.' : '' }
  if (!sources.length || unavailable || missingIds.length) return base
  const window = options.source === 'selection' ? span(sources[0]) : { start: 0, end: length(doc) }
  const occupied: TimeRange[] = []
  const cache = new Map<string, TimeRange[]>()
  for (const c of sources) {
    if (options.source === 'voices') { occupied.push(span(c)); continue }
    const speed = c.speed ?? 1, id = c.asset_id!, data = visuals[id]
    // An empty/unreadable waveform is unknown audio, not silence.
    if (!data?.peaks.length || data.interval <= 0) { occupied.push(span(c)); continue }
    if (!cache.has(id)) cache.set(id, silences(data, options.threshold, 0))
    const quiet = cache.get(id)!.flatMap(q => {
      const a = Math.max(c.source_in, q.start), b = Math.min(c.source_in + c.duration * speed, q.end)
      return b > a ? [{ start: c.start + (a - c.source_in) / speed, end: c.start + (b - c.source_in) / speed }] : []
    })
    occupied.push(...gaps(span(c), quiet))
  }
  // Cutting original audio must never eat an overlapping narration sentence.
  occupied.push(...voices.filter(c => !sources.includes(c)).map(span))
  const ranges = gaps(window, occupied).flatMap(q => {
    if (!options.trimEdges && (q.start <= window.start + 1e-6 || q.end >= window.end - 1e-6)) return []
    if (q.end - q.start < options.minimum - 1e-6) return []
    // Round inwards: preserve at least the requested breathing room around speech.
    const a = Math.ceil((q.start + options.padding) * doc.fps - 1e-6)
    const b = Math.floor((q.end - options.padding) * doc.fps + 1e-6)
    return b > a ? [{ id: `${a}:${b}`, start: a / doc.fps, end: b / doc.fps }] : []
  })
  return { ...base, ranges }
}
