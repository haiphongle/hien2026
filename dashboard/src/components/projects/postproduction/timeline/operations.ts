import { MAX_DURATION, MAX_TIMELINE_CLIPS, MAX_AUDIO_CLIPS } from '../../../../lib/mediaLimits.ts'
import type { Asset, Clip, Document } from './types.ts'
import { envelopeGain } from './audio.ts'
import { DEFAULT_CAPTION_STYLE, DEFAULT_COLORS, DEFAULT_FRAME, outputSize } from '../editingTypes.ts'
import { titleTemplate } from './textTitles.ts'
import { TRANSITIONS } from './transitions.ts'

export const frame = (time: number, fps: number) => Math.round(time * fps) / fps
export const end = (clip: Clip) => clip.start + clip.duration
export const length = (doc: Document) => Math.max(0, ...doc.clips.map(end))
export const clock = (time: number) => `${Math.floor(time / 60).toString().padStart(2, '0')}:${(time % 60).toFixed(2).padStart(5, '0')}`
const uid = () => crypto.randomUUID()
export function members(doc: Document, ids: string[]) {
  const groups = new Set(doc.clips.filter(c => ids.includes(c.id) && c.group).map(c => c.group))
  const linked = doc.clips.filter(c => ids.includes(c.id) || (c.group && groups.has(c.group))).map(c => c.id)
  return doc.clips.filter(c => linked.includes(c.id) || c.anchor && linked.includes(c.anchor)).map(c => c.id)
}
const avMembers = (doc: Document, id: string) => members(doc, [id]).filter(key => key === id || !doc.clips.find(c => c.id === key)?.anchor)
export function patch(doc: Document, id: string, changes: Partial<Clip>): Document {
  editable(doc, [id])
  return { ...doc, clips: doc.clips.map(c => c.id === id ? { ...c, ...changes } : c) }
}
function editable(doc: Document, ids: string[]) {
  if (doc.clips.some(c => ids.includes(c.id) && doc.tracks.find(t => t.id === c.track_id)?.locked)) throw new Error('Lớp đang khóa. Mở khóa trước khi sửa đoạn.')
}
export function sourceIn(doc: Document, id: string, n: number): Document {
  const ids = avMembers(doc, id); editable(doc, ids)
  return { ...doc, clips: doc.clips.map(c => ids.includes(c.id) ? { ...c, source_in: frame(n, doc.fps) } : c) }
}
export function gain(doc: Document, id: string, n: number): Document {
  editable(doc, [id])
  return { ...doc, clips: doc.clips.map(c => c.id === id ? { ...c, gain: Math.max(0, Math.min(2, n)) } : c) }
}
export function validate(doc: Document, assets: Asset[]) {
  if(doc.tracks.length>12)throw new Error('Bản dựng tối đa 12 lớp.')
  if (doc.clips.length > MAX_TIMELINE_CLIPS) throw new Error(`Bản dựng tối đa ${MAX_TIMELINE_CLIPS} đoạn.`)
  const kinds = new Map(doc.tracks.map(t => [t.id, t.kind]))
  const sources = new Map(assets.map(a => [a.id, a]))
  const picturesById = new Set(doc.clips.filter(c => kinds.get(c.track_id) === 'video').map(c => c.id))
  if (doc.clips.filter(c => kinds.get(c.track_id) === 'audio').length > MAX_AUDIO_CLIPS) throw new Error(`Bản dựng tối đa ${MAX_AUDIO_CLIPS} đoạn âm thanh. Chọn ít khoảng cắt hơn.`)
  if (doc.clips.filter(c => kinds.get(c.track_id) === 'effect').length > 32) throw new Error('Bản dựng tối đa 32 đoạn hiệu ứng. Chọn ít khoảng cắt hơn hoặc ghép hiệu ứng trước.')
  if (length(doc) > MAX_DURATION + .00001) throw new Error(`Bản dựng tối đa ${MAX_DURATION / 60} phút.`)
  for (const c of doc.clips) {
    const asset = c.asset_id ? sources.get(c.asset_id) : undefined, kind = kinds.get(c.track_id)
    if (c.transition && (kind !== 'video' || !TRANSITIONS.some(t => t.kind === c.transition!.kind) || !Number.isFinite(c.transition.duration) || c.transition.duration < .1 || c.transition.duration > 2)) throw new Error('Chuyển cảnh trên lớp hình ảnh cần dài từ 0.1 đến 2 giây.')
    if (!kind || c.start < 0 || c.source_in < 0 || c.duration < 1 / doc.fps - .00001) throw new Error('Mốc cắt không hợp lệ.')
    if (kind === 'video' || kind === 'overlay' || kind === 'audio') {
      if (!asset || asset.missing) throw new Error('Tư liệu đã mất hoặc thay đổi. Chọn lại trong thư viện.')
      if ((kind === 'video' || kind === 'overlay') && asset.kind !== 'video') throw new Error('Lớp hình cần tư liệu video.')
      if (c.source_in + c.duration * (c.speed ?? 1) > asset.duration + .001) throw new Error('Mốc cắt vượt thời lượng tư liệu.')
    }
    if ((c.fade_in ?? 0)+(c.fade_out ?? 0) > c.duration+.00001) throw new Error('Hai fade không được dài hơn đoạn âm thanh.')
    if(kind==='caption'&&!c.text?.trim())throw new Error('Nhập chữ cho phụ đề trước khi lưu.')
    if (c.anchor && !picturesById.has(c.anchor)) throw new Error('Liên kết với đoạn hình đã mất.')
  }
  const pictures = doc.clips.filter(c => kinds.get(c.track_id) === 'video').sort((a, b) => a.start - b.start)
  for (let i = 1; i < pictures.length; i++) {
    const previous = pictures[i - 1], next = pictures[i], overlap = end(previous) - next.start
    if (overlap > .00001) {
      const name = (c: Clip, index: number) => sources.get(c.asset_id ?? '')?.name || `Cảnh ${index + 1}`
      throw new Error(`Đoạn hình “${name(previous, i - 1)}” và “${name(next, i)}” đang chồng nhau ${overlap.toFixed(2)} giây. Kéo vào khoảng trống hoặc dùng mũi tên đổi thứ tự.`)
    }
  }
  const overlays = doc.clips.filter(c => kinds.get(c.track_id) === 'overlay').sort((a,b) => a.start-b.start)
  if (doc.tracks.filter(t => t.kind === 'overlay').length > 1 || overlays.length > 32) throw new Error('Tối đa một lớp với 32 đoạn video chèn.')
  for (const [i,c] of overlays.entries()) {
    const o = c.overlay
    if (!o || ![o.x,o.y,o.width,o.height,o.border].every(Number.isFinite) || o.x < 0 || o.y < 0 || o.width < .05 || o.height < .05 || o.x+o.width > 1.000001 || o.y+o.height > 1.000001) throw new Error('Khung video chèn phải nằm trong màn hình.')
    if (i && end(overlays[i-1]) > c.start+.00001) throw new Error('Các khung video chèn cần đặt nối tiếp, không chồng thời gian.')
  }
  return doc
}
export function insert(doc: Document, asset: Asset, at: number, targetTrack?: string): Document {
  const video = doc.tracks.find(t => t.kind === 'video')!
  const role = asset.category === 'music' ? 'music' : asset.category === 'voice' ? 'voice' : 'effects'
  let next = doc
  let audio = asset.kind === 'video' ? doc.tracks.find(t => t.kind === 'audio' && t.purpose !== 'overlay' && (t.role ?? 'original') === 'original') : doc.tracks.find(t => t.id === targetTrack && t.kind === 'audio') ?? doc.tracks.find(t => t.kind === 'audio' && t.purpose !== 'overlay' && t.role === role)
  if (!audio) {
    audio = { id: uid(), name: role === 'music' ? 'Nhạc nền' : role === 'voice' ? 'Giọng đọc' : 'Hiệu ứng âm thanh', kind: 'audio', role, locked: false, muted: false }
    next = { ...doc, tracks: [...doc.tracks, audio] }
  }
  if (video.locked && asset.kind === 'video' || audio.locked && asset.has_audio) throw new Error('Mở khóa lớp trước khi thêm tư liệu.')
  const duration = Math.floor(asset.duration * doc.fps + 1e-6) / doc.fps
  let start = frame(Math.max(0, at), doc.fps)
  if (asset.kind === 'video') {
    for (const c of doc.clips.filter(c => c.track_id === video.id).sort((a, b) => a.start - b.start)) {
      if (start + duration > c.start && start < end(c)) start = end(c)
    }
  }
  const group = asset.kind === 'video' ? uid() : null
  const base = { asset_id: asset.id, start: frame(start, doc.fps), source_in: 0, duration, gain: 1, group }
  const clips: Clip[] = []
  if (asset.kind === 'video') clips.push({ ...base, id: uid(), track_id: video.id })
  if (asset.has_audio) clips.push({ ...base, id: uid(), track_id: audio.id, gain: asset.kind === 'audio' ? .5 : 1 })
  return { ...next, clips: [...doc.clips, ...clips] }
}
export function move(doc: Document, ids: string[], delta: number): Document {
  const selected = members(doc, ids); editable(doc, selected)
  delta = frame(delta, doc.fps)
  const earliest = Math.min(...doc.clips.filter(c => selected.includes(c.id)).map(c => c.start))
  delta = Math.max(-earliest, delta)
  return { ...doc, clips: doc.clips.map(c => selected.includes(c.id) ? { ...c, start: frame(c.start + delta, doc.fps) } : c) }
}
export function trim(doc: Document, id: string, side: 'left' | 'right', delta: number, ripple = false): Document {
  const c = doc.clips.find(c => c.id === id)!
  const ids = avMembers(doc, id); editable(doc, members(doc, [id]))
  delta = frame(delta, doc.fps)
  if (side === 'left') delta = Math.min(c.duration - 1 / doc.fps, Math.max(-c.start, c.asset_id ? -c.source_in/(c.speed ?? 1) : -c.start, delta))
  else delta = Math.max(1 / doc.fps - c.duration, delta)
  const picture = doc.tracks.find(t => t.id === c.track_id)?.kind === 'video'
  if (picture && !ripple) {
    const neighbors = doc.clips.filter(item => item.track_id === c.track_id && item.id !== id)
    if (side === 'left') delta = Math.max(delta, Math.max(0, ...neighbors.filter(item => item.start < c.start).map(end)) - c.start)
    else delta = Math.min(delta, Math.min(Infinity, ...neighbors.filter(item => item.start > c.start).map(item => item.start)) - end(c))
  }
  const a = side === 'left' ? c.start+delta : c.start, b = side === 'right' ? end(c)+delta : end(c)
  const next = { ...doc, clips: doc.clips.flatMap(item => {
    if (ids.includes(item.id)) return [slice(item, a, b, doc.fps)]
    if (item.anchor && ids.includes(item.anchor)) {
      const left = Math.max(a, item.start), right = Math.min(b, end(item))
      return right > left ? [slice(item, left, right, doc.fps)] : []
    }
    return [item]
  }) }
  if (!picture || !ripple || side !== 'right' || Math.abs(delta) < .00001) return next
  const attached = new Set(members(doc, [id]))
  const following = new Set(next.clips.filter(item => !attached.has(item.id) && item.start >= end(c) - .00001).map(item => item.id))
  editable(doc, [...following])
  return { ...next, clips: next.clips.map(item => following.has(item.id) ? { ...item, start: frame(item.start + delta, doc.fps) } : item) }
}
/** Longer than the source: use the existing speed edit to keep linked layers in sync. */
export function setDuration(doc: Document, id: string, value: number, assets: Asset[]): Document {
  const c = doc.clips.find(item => item.id === id)!
  const duration = frame(value, doc.fps)
  if (!Number.isFinite(duration) || duration < 1 / doc.fps) throw new Error('Độ dài cần ít nhất một khung hình.')
  if (Math.abs(duration - c.duration) < .00001) return doc
  const kind = doc.tracks.find(t => t.id === c.track_id)?.kind
  const asset = assets.find(a => a.id === c.asset_id)
  if ((kind === 'video' || kind === 'overlay') && asset && c.source_in + duration * (c.speed ?? 1) > asset.duration + .001) {
    const rate = c.duration * (c.speed ?? 1) / duration
    if (rate < .5 || rate > 2) throw new Error(`Để giữ tốc độ từ 0.5× đến 2×, đoạn này dài tối đa ${frame(c.duration * (c.speed ?? 1) / .5, doc.fps)} giây.`)
    return speed(doc, id, rate)
  }
  return trim(doc, id, 'right', duration - c.duration, kind === 'video')
}
export function split(doc: Document, ids: string[], at: number): Document {
  return splitSelection(doc, ids, at).document
}
/** Keep the remaining right-hand pieces selected for consecutive cuts. */
export function splitSelection(doc: Document, ids: string[], at: number): { document: Document; selection: string[] } {
  at = frame(at, doc.fps)
  const selected = members(doc, ids); editable(doc, selected)
  const groups = new Map<string, string>()
  const rightIds = new Map(doc.clips.filter(c => selected.includes(c.id) && at > c.start + .00001 && at < end(c) - .00001).map(c => [c.id, uid()]))
  let changed = false
  const clips = doc.clips.flatMap(c => {
    if (!selected.includes(c.id) || at <= c.start + .00001 || at >= end(c) - .00001) return [c]
    changed = true
    if (c.group && !groups.has(c.group)) groups.set(c.group, uid())
    return [slice(c, c.start, at, doc.fps),
      { ...slice(c, at, end(c), doc.fps), id: rightIds.get(c.id)!, group: c.group ? groups.get(c.group)! : null }]
  })
  if (!changed) throw new Error('Đặt vạch phát bên trong đoạn đã chọn rồi bấm Tách.')
  return {
    document: { ...doc, clips: clips.map(c => c.anchor && c.start >= at && rightIds.has(c.anchor) ? { ...c, anchor: rightIds.get(c.anchor)!,origin:c.origin?.replace(c.anchor,rightIds.get(c.anchor)!) } : c) },
    selection: ids.map(id => rightIds.get(id) ?? id),
  }
}
export function remove(doc: Document, ids: string[], ripple: boolean): Document {
  const selected = members(doc, ids); editable(doc, selected)
  const removed = doc.clips.filter(c => selected.includes(c.id))
  let result = { ...doc, clips: doc.clips.filter(c => !selected.includes(c.id)) }
  if (!ripple) return result
  const ranges: [number, number][] = []
  for (const c of removed.sort((a, b) => a.start - b.start)) {
    const last = ranges.at(-1)
    if (last && c.start <= last[1]) last[1] = Math.max(last[1], end(c))
    else ranges.push([c.start, end(c)])
  }
  // Process backwards: original-time intervals remain valid for earlier cuts.
  for (const [a, b] of ranges.reverse()) result = cutRange(result, a, b)
  return result
}
/** Remove only the clips on a track; a bulk clear must never ripple into other layers. */
export function clearTrack(doc: Document, trackId: string): Document {
  const track = doc.tracks.find(item => item.id === trackId)
  if (!track) return doc
  if (track.locked) throw new Error('Mở khóa lớp trước khi xóa các đoạn.')
  return { ...doc, clips: doc.clips.filter(clip => clip.track_id !== trackId) }
}
export function duplicate(doc: Document, ids: string[]): Document {
  const selected = members(doc, ids); editable(doc, selected)
  const clips = doc.clips.filter(c => selected.includes(c.id))
  const earliest = Math.min(...clips.map(c => c.start)), offset = length(doc) - earliest
  const groups = new Map<string, string>()
  const copies = new Map(clips.map(c => [c.id, uid()]))
  return { ...doc, clips: [...doc.clips, ...clips.map(c => {
    if (c.group && !groups.has(c.group)) groups.set(c.group, uid())
    return { ...c, id: copies.get(c.id)!, anchor: c.anchor ? copies.get(c.anchor) ?? c.anchor : null, origin: c.anchor&&copies.has(c.anchor)?c.origin?.replace(c.anchor,copies.get(c.anchor)!):'', start: frame(c.start + offset, doc.fps), group: c.group ? groups.get(c.group)! : null }
  })] }
}
export function link(doc: Document, ids: string[], unlink: boolean): Document {
  const selected = unlink ? members(doc, ids) : ids; editable(doc, selected)
  const clips = doc.clips.filter(c => selected.includes(c.id))
  if (!unlink && (clips.length < 2 || new Set(clips.map(c => `${c.start}:${c.source_in}:${c.duration}:${c.asset_id}`)).size !== 1)) throw new Error('Chọn hình và tiếng của cùng tư liệu, cùng mốc để liên kết.')
  const group = unlink ? null : uid()
  return { ...doc, clips: doc.clips.map(c => selected.includes(c.id) ? { ...c, group } : c) }
}
export function reorder(doc: Document, id: string, direction: number): Document {
  const c = doc.clips.find(c => c.id === id)!
  if (doc.tracks.find(t => t.id === c.track_id)?.kind !== 'video') throw new Error('Chọn đoạn hình để đổi thứ tự.')
  const pictures = doc.clips.filter(item => item.track_id === c.track_id).sort((a, b) => a.start - b.start)
  const index = pictures.findIndex(item => item.id === id), other = pictures[index + direction]
  if (!other) return doc
  const swap = pictures[index]; pictures[index] = other; pictures[index + direction] = swap
  return pack(doc, pictures.map(c => c.id))
}
export function pack(doc: Document, order?: string[]): Document {
  const pictures = doc.clips.filter(c => doc.tracks.find(t => t.id === c.track_id)?.kind === 'video')
    .sort((a, b) => order ? order.indexOf(a.id) - order.indexOf(b.id) : a.start - b.start)
  let result = doc, cursor = 0
  for (const c of pictures) {
    const now = result.clips.find(item => item.id === c.id)!
    result = move(result, [c.id], cursor - now.start)
    cursor = frame(cursor + c.duration, doc.fps)
  }
  return result
}
export function dragReorder(doc: Document, ids: string[], proposedStart: number): Document {
  const selected = members(doc, ids)
  const pictures = doc.clips.filter(c => doc.tracks.find(t => t.id === c.track_id)?.kind === 'video').sort((a,b) => a.start-b.start)
  const moving = pictures.filter(c => selected.includes(c.id))
  if (moving.length !== 1) throw new Error('Để đổi thứ tự bằng kéo, chọn một đoạn hình. Có thể dùng Sắp xếp liền nhau sau khi kéo nhiều đoạn.')
  const ordered = pictures.filter(c => c.id !== moving[0].id)
  let index = ordered.findIndex(c => Math.max(0, proposedStart) < c.start + c.duration / 2)
  if (index < 0) index = ordered.length
  ordered.splice(index, 0, moving[0])
  return pack(doc, ordered.map(c => c.id))
}
export function snap(doc: Document, ids: string[], proposed: number, span: number, playhead: number, threshold: number) {
  const selected = members(doc, ids)
  const targets = [0, playhead, ...doc.clips.filter(c => !selected.includes(c.id)).flatMap(c => [c.start, end(c)])]
  const matches = targets.flatMap(target => [{ value: target, distance: Math.abs(target - proposed) }, { value: target - span, distance: Math.abs(target - proposed - span) }]).sort((a, b) => a.distance - b.distance)
  return matches[0]?.distance <= threshold ? matches[0].value : proposed
}

/** Slice in output time; automation/source offsets follow the retained samples. */
export function slice(c: Clip, a: number, b: number, fps: number): Clip {
  const offset = a-c.start, duration = frame(b-a, fps)
  const boundaries = new Set([0, duration, ...(c.envelope ?? []).map(p=>p.time-offset), (c.fade_in ?? 0)-offset, c.duration-(c.fade_out ?? 0)-offset])
  const envelope = [...boundaries].filter(t=>t>=0 && t<=duration).sort((a,b)=>a-b).map(time=>({ time, value: Math.max(0,Math.min(2,envelopeGain(c,time+offset))) }))
  return { ...c, start:frame(a,fps), source_in:c.asset_id ? frame(c.source_in+offset*(c.speed??1),fps) : 0, duration,
    ...(offset > .00001 && c.transition ? { transition: null } : {}),
    ...(c.overlay?.entrance ? {overlay:{...c.overlay,entrance_offset:Math.max(0,(c.overlay.entrance_offset??0)+offset)}} : {}),
    fade_in:0,fade_out:0,envelope: c.fade_in || c.fade_out || c.envelope?.length ? envelope : [] }
}

/** Close an interval on every layer, including attached subtitles and voices. */
export function cutRange(doc: Document, a: number, b: number): Document {
  a=frame(a,doc.fps); b=frame(b,doc.fps)
  if (b<=a) throw new Error('Kéo chọn một khoảng thời gian trước.')
  editable(doc,doc.clips.filter(c=>end(c)>a).map(c=>c.id))
  const groups = new Map<string,string>(), pieces = new Map<string,Clip[]>()
  for (const c of doc.clips) {
    let result: Clip[] = []
    if (end(c)<=a) result=[c]
    else if (c.start>=b) result=[{...c,start:frame(c.start-b+a,doc.fps)}]
    else {
      if (c.start<a) result.push(slice(c,c.start,a,doc.fps))
      if (end(c)>b) {
        if(c.group && !groups.has(c.group)) groups.set(c.group,uid())
        result.push({...slice(c,b,end(c),doc.fps),id:result.length?uid():c.id,start:a,group:c.group?groups.get(c.group)!:null})
      }
    }
    pieces.set(c.id,result)
  }
  const clips = [...pieces.values()].flat().map(c=>{
    if (!c.anchor) return c
    const parent=pieces.get(c.anchor)?.find(p=>c.start>=p.start-.001 && end(c)<=end(p)+.001)
    return {...c,anchor:parent?.id??null,origin:parent?c.origin?.replace(c.anchor,parent.id):''}
  })
  return {...doc,clips}
}
export function keepRange(doc: Document, a: number, b: number): Document {
  a=frame(a,doc.fps); b=frame(b,doc.fps)
  if(b<=a) throw new Error('Kéo chọn một khoảng thời gian trước.')
  let next=doc
  if(b<length(doc)) next=cutRange(next,b,length(doc))
  if(a>0) next=cutRange(next,0,a)
  return next
}

/** Apply many ripple cuts in one pass and one undo step, in the original timebase. */
export function cutRanges(doc: Document, ranges: { start: number; end: number }[]): Document {
  const total = Math.round(length(doc) * doc.fps), cuts: { a: number; b: number }[] = []
  for (const r of [...ranges].sort((a, b) => a.start - b.start)) {
    if (!Number.isFinite(r.start) || !Number.isFinite(r.end)) throw new Error('Mốc cắt không hợp lệ.')
    const a = Math.max(0, Math.round(r.start * doc.fps)), b = Math.min(total, Math.round(r.end * doc.fps))
    if (b <= a) continue
    const last = cuts.at(-1)
    if (last && a <= last.b) last.b = Math.max(last.b, b)
    else cuts.push({ a, b })
  }
  if (!cuts.length) return doc
  editable(doc, doc.clips.filter(c => Math.round(end(c) * doc.fps) > cuts[0].a).map(c => c.id))
  const kept: { a: number; b: number; removed: number }[] = []
  let cursor = 0, removed = 0
  for (const cut of cuts) {
    if (cut.a > cursor) kept.push({ a: cursor, b: cut.a, removed })
    removed += cut.b - cut.a; cursor = cut.b
  }
  if (cursor < total) kept.push({ a: cursor, b: total, removed })
  if (!kept.length) throw new Error('Cần giữ lại ít nhất một khung hình.')
  const groups = new Map<string, string>(), pieces = new Map<string, Map<number, Clip>>()
  for (const c of doc.clips) {
    const ca = Math.round(c.start * doc.fps), cb = Math.round(end(c) * doc.fps), retained = new Map<number, Clip>()
    let low = 0, high = kept.length
    while (low < high) { const mid = (low + high) >>> 1; if (kept[mid].b <= ca) low = mid + 1; else high = mid }
    for (let i = low; i < kept.length && kept[i].a < cb; i++) {
      const k = kept[i], a = Math.max(ca, k.a), b = Math.min(cb, k.b)
      if (b <= a) continue
      const groupKey = `${c.group}:${i}`
      if (c.group && !groups.has(groupKey)) groups.set(groupKey, i === 0 ? c.group : uid())
      retained.set(i, { ...slice(c, a / doc.fps, b / doc.fps, doc.fps), id: retained.size ? uid() : c.id,
        start: (a - k.removed) / doc.fps, group: c.group ? groups.get(groupKey)! : null })
    }
    pieces.set(c.id, retained)
  }
  const clips = [...pieces.values()].flatMap(retained => [...retained.entries()].map(([i, c]) => {
    if (!c.anchor) return c
    const parent = pieces.get(c.anchor)?.get(i)
    return { ...c, anchor: parent?.id ?? null, origin: parent ? c.origin?.replace(c.anchor, parent.id) : '' }
  }))
  return { ...doc, clips }
}
export function speed(doc: Document, id: string, value: number): Document {
  const c=doc.clips.find(c=>c.id===id)!
  const ids=avMembers(doc,id), all=members(doc,[id]); editable(doc,all)
  const ratio=(c.speed??1)/Math.max(.5,Math.min(2,value))
  if (doc.clips.some(item => all.includes(item.id) && ((item.speed ?? 1) / ratio < .5 || (item.speed ?? 1) / ratio > 2))) throw new Error('Một đoạn liên quan vượt tốc độ 0.5×–2×. Chọn độ dài khác hoặc tách liên kết trước.')
  const resized = (item: Clip) => item.asset_id ? Math.floor(item.duration*ratio*doc.fps+1e-6)/doc.fps : frame(item.duration*ratio,doc.fps)
  if (resized(c) < 1/doc.fps) throw new Error('Đoạn quá ngắn để tăng tốc.')
  const delta=resized(c)-c.duration
  const ripple = !doc.clips.some(item=>ids.includes(item.id)&&doc.tracks.find(t=>t.id===item.track_id)?.kind==='overlay')
  if(ripple) editable(doc,doc.clips.filter(item=>item.start>=end(c)-.00001).map(item=>item.id))
  const clips=doc.clips.map(item=>{
    if (!all.includes(item.id)) return ripple&&item.start>=end(c)-.00001?{...item,start:frame(item.start+delta,doc.fps)}:item
    const scaled = {...item,start:ids.includes(item.id)?item.start:frame(c.start+(item.start-c.start)*ratio,doc.fps),
      duration:item.duration*ratio,speed:(item.speed??1)/ratio,
      fade_in:(item.fade_in??0)*ratio,fade_out:(item.fade_out??0)*ratio,envelope:item.envelope?.map(p=>({...p,time:p.time*ratio}))}
    // Rounding down may remove part of the last frame; clip automation as well.
    return slice(scaled,scaled.start,scaled.start+resized(item),doc.fps)
  })
  return {...doc,clips}
}
export function layer(doc: Document, kind: 'caption'|'text'|'effect', at: number, variant = 'caption'): Document {
  const existing=doc.tracks.find(t=>t.kind===kind), track=existing??{id:uid(),name:kind==='caption'?'Phụ đề':kind==='text'?'Chữ & tiêu đề':'Hiệu ứng hình',kind,locked:false,muted:false}
  if(track.locked) throw new Error('Mở khóa lớp trước khi thêm đoạn.')
  const start=frame(Math.max(0,at),doc.fps)
  const remaining = length(doc) - start
  const duration=frame(Math.min(3,MAX_DURATION-start,Math.max(1/doc.fps,kind==='text'&&remaining<=0?3:remaining)),doc.fps)
  if (duration < 1/doc.fps) throw new Error('Bản dựng đã đủ 180 phút. Đặt vạch phát sớm hơn để thêm chữ hoặc hiệu ứng.')
  const picture=doc.clips.find(c=>doc.tracks.find(t=>t.id===c.track_id)?.kind==='video'&&c.start<=start&&end(c)>=start+duration)
  const base:Clip={id:uid(),asset_id:null,track_id:track.id,start,duration,source_in:0,gain:1,group:null,anchor:picture?.id??null}
  const title = titleTemplate(variant)
  const [width,height] = outputSize(doc.frame ?? DEFAULT_FRAME, {width:doc.width,height:doc.height,duration:length(doc)})!
  title.caption.size = Math.max(1, Math.round(title.caption.size * Math.min(1, width/height) * 10) / 10)
  const clip=kind==='text'?{...base,...title}:kind==='caption'?{...base,text:'Nhập phụ đề ở bảng thuộc tính',caption:{...DEFAULT_CAPTION_STYLE,x:.5,y:.85}}:
    variant==='colors'?{...base,colors:{...DEFAULT_COLORS}}:{...base,effect:{id:uid(),kind:variant as 'blur'|'color'|'image'|'delogo',x:.65,y:.75,width:.25,height:.15,start:0,end:3,strength:12,color:'#000000',opacity:1,image_id:null}}
  return {...doc,tracks:existing?doc.tracks:[...doc.tracks,track],clips:[...doc.clips,clip]}
}
