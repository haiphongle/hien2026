import type { Clip, Document, Transition } from './types.ts'

export const TRANSITIONS: { kind: Transition['kind']; name: string }[] = [
  { kind: 'dissolve', name: 'Hòa tan' }, { kind: 'blur', name: 'Mờ → rõ' },
  { kind: 'black', name: 'Qua màu đen' }, { kind: 'slide', name: 'Lướt ngang' },
  { kind: 'light', name: 'Ánh sáng' },
]
const end = (c: Clip) => c.start + c.duration

/** Joins are derived from current edit positions; moving clips never leaves orphan effects. */
export function transitionJoins(doc: Document) {
  const track = doc.tracks.find(t => t.kind === 'video')
  const clips = doc.clips.filter(c => c.track_id === track?.id).sort((a, b) => a.start - b.start)
  return clips.slice(1).flatMap((next, i) => Math.abs(end(clips[i]) - next.start) < .00001
    ? [{ previous: clips[i], next, duration: Math.max(1, Math.floor(Math.min(next.transition?.duration ?? .4, next.duration, clips[i].duration) * doc.fps + 1e-6)) / doc.fps }] : [])
}

export function transitionAt(doc: Document, time: number, lookahead = 0, joins = transitionJoins(doc)) {
  if (doc.tracks.find(t => t.kind === 'video')?.muted) return undefined
  return joins.find(j => j.next.transition && time < j.next.start + j.duration - .000001 && j.next.start <= time + lookahead)
}

export function setTransition(doc: Document, id: string | null, transition: Transition | null, all = false): Document {
  const track = doc.tracks.find(t => t.kind === 'video')!
  if (track.locked) throw new Error('Mở khóa lớp hình ảnh trước khi đổi chuyển cảnh.')
  if (transition && (!TRANSITIONS.some(t => t.kind === transition.kind) || !Number.isFinite(transition.duration) || transition.duration < .1 || transition.duration > 2)) throw new Error('Chuyển cảnh cần dài từ 0.1 đến 2 giây.')
  const joins = transitionJoins(doc)
  if (!(all && !transition) && !joins.some(j => j.next.id === id)) throw new Error('Chọn chỗ nối giữa hai cảnh liền nhau.')
  const ids = new Set(joins.filter(j => all || j.next.id === id).map(j => j.next.id))
  return { ...doc, clips: doc.clips.map(c => ids.has(c.id) ? { ...c, transition: transition ? { ...transition } : null } : all && c.track_id === track.id && c.transition ? { ...c, transition: null } : c) }
}

// Match FFmpeg fadeblack/fadewhite; weights add to one, keeping preview/export consistent.
export function transitionWeights(kind: Transition['kind'], progress: number) {
  const p = Math.max(0, Math.min(1, progress)), old = 1 - p
  if (kind !== 'black' && kind !== 'light') return { old, next: p, background: 0 }
  const smooth = (a: number, b: number, x: number) => { const t = Math.max(0, Math.min(1, (x-a)/(b-a))); return t*t*(3-2*t) }
  const previous = old * smooth(.8, 1, old), next = p * (1 - smooth(.2, 1, old))
  return { old: previous, next, background: 1 - previous - next }
}
