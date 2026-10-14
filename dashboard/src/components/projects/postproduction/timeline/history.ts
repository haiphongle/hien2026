import { MAX_TIMELINE_CLIPS } from '../../../../lib/mediaLimits.ts'
import type { Document } from './types'

type Storage = { getItem: (key: string) => string | null; setItem: (key: string, value: string) => void }
type History = { undo: Document[]; redo: Document[] }
const key = (videoId: string) => `flowkit:timeline-history:v1:${videoId}`
const maxCharacters = 750_000
function normalized(saved: Document): Document {
  const clean=(value:object,defaults:Record<string,unknown>)=>Object.fromEntries(Object.entries(value).filter(([key,item])=>!(key in defaults)||JSON.stringify(item)!==JSON.stringify(defaults[key])))
  const data={...saved,tracks:saved.tracks.map(t=>clean(t,{solo:false,role:'original',purpose:'standard'})),clips:saved.clips.map(c=>{
    const item=clean(c,{anchor:null,speed:1,fade_in:0,fade_out:0,envelope:[],text:'',effect:null,colors:null,origin:'',muted:false,overlay:null,transition:null})
    if(!c.text && saved.tracks.find(t => t.id === c.track_id)?.kind !== 'text')delete item.caption
    if(c.overlay)item.overlay=clean(c.overlay,{shape:'rectangle',radius:12,entrance:0,entrance_offset:0})
    return item
  })} as Document
  if(data.ducking)data.ducking=clean(data.ducking,{original_amount:1,detect_pauses:false}) as unknown as Document['ducking']
  if(!saved.ducking?.enabled&&(!saved.ducking||saved.ducking.amount===.25&&saved.ducking.attack===.2&&saved.ducking.release===.35&&(saved.ducking.original_amount??1)===1&&!saved.ducking.detect_pauses))delete data.ducking
  if (!data.frame) delete data.frame
  if (!data.legacy_imported) delete data.legacy_imported
  return data
}
const signature = (saved: Document) => JSON.stringify(normalized(saved), (_key, value) => value && typeof value === 'object' && !Array.isArray(value)
  ? Object.fromEntries(Object.keys(value).sort().map(key => [key, value[key]])) : value)

function document(value: unknown): value is Document {
  if (!value || typeof value !== 'object') return false
  const d = value as Document
  return d.version === 1 && Number.isFinite(d.fps) && d.fps > 0 && Number.isFinite(d.width) && Number.isFinite(d.height)
    && Array.isArray(d.tracks) && d.tracks.length >= 2 && d.tracks.length <= 12
    && Array.isArray(d.clips) && d.clips.length <= MAX_TIMELINE_CLIPS
    && d.tracks.every(t => t && typeof t.id === 'string' && ['video', 'overlay', 'audio','caption','text','effect'].includes(t.kind))
    && d.clips.every(c => c && typeof c.id === 'string' && (typeof c.asset_id === 'string'||c.asset_id===null) && typeof c.track_id === 'string'
      && [c.start, c.source_in, c.duration, c.gain].every(Number.isFinite))
}

/** Restore only history anchored to the exact document loaded from the server. */
export function readHistory(storage: Storage, videoId: string, saved: Document): History {
  const empty = { undo: [], redo: [] }
  try {
    const value = JSON.parse(storage.getItem(key(videoId)) || 'null')
    // Older signatures may contain newly optional default fields. Normalize
    // the stored signature too, while still rejecting a genuinely different draft.
    if (!value || (value.saved !== signature(saved) && signature(JSON.parse(value.saved)) !== signature(saved)) || !Array.isArray(value.undo) || !Array.isArray(value.redo)
      || value.undo.length > 50 || value.redo.length > 50 || !value.undo.every(document) || !value.redo.every(document)) return empty
    return { undo: value.undo, redo: value.redo }
  } catch { return empty }
}

/** Bound local storage size; keep the nearest undo/redo steps if a draft is large. */
export function writeHistory(storage: Storage, videoId: string, saved: Document, history: History): boolean {
  const value = { saved: signature(saved), undo: history.undo.slice(-50), redo: history.redo.slice(-50) }
  let text = JSON.stringify(value)
  while (text.length > maxCharacters && (value.undo.length || value.redo.length)) {
    if (value.undo.length >= value.redo.length) value.undo.shift()
    else value.redo.shift()
    text = JSON.stringify(value)
  }
  try { storage.setItem(key(videoId), text); return true } catch { return false }
}
