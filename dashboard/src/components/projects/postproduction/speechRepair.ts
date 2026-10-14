import type { SpeechAudio, SpeechCue, SpeechDocument, SpeechState } from './speechTypes'
import { MAX_SPEECH_CUES } from '../../../lib/mediaLimits.ts'
export { MAX_SPEECH_CUES }

export const MAX_CAPTION_CHARS = 84
export const MAX_CHARS_PER_SECOND = 22
const millis = (seconds: number) => Math.round(seconds * 1000)

// Timings and speaker labels do not change the synthesis cache signature.
export function speechAudioIndex(state: SpeechState | null) {
  return { saved: new Map(state?.document.cues.map(c => [c.id, c])), audio: new Map(state?.audio.map(a => [a.id, a])) }
}
export function matchingAudio(cue: SpeechCue, state: SpeechState | null, index?: ReturnType<typeof speechAudioIndex>): SpeechAudio | undefined {
  const saved = index ? index.saved.get(cue.id) : state?.document.cues.find(item => item.id === cue.id)
  if (!saved || saved.text !== cue.text.trim() || saved.voice_id !== cue.voice_id || saved.speed !== cue.speed) return
  const audio = index ? index.audio.get(cue.id) : state?.audio.find(item => item.id === cue.id)
  return audio?.audio_url && (audio.status === 'READY' || audio.status === 'TOO_LONG') && Number.isFinite(audio.duration) && audio.duration! > 0 ? audio : undefined
}

export function validateRepair(doc: SpeechDocument, duration: number): string {
  if (!Number.isFinite(duration) || duration <= 0) return 'Chưa có thời lượng video hợp lệ.'
  if (doc.cues.length > MAX_SPEECH_CUES) return `Kịch bản tối đa ${MAX_SPEECH_CUES.toLocaleString('vi-VN')} câu.`
  for (let i = 0; i < doc.cues.length; i++) {
    const cue = doc.cues[i]
    if (!Number.isFinite(cue.start) || !Number.isFinite(cue.end) || cue.start < 0 || cue.end <= cue.start) return `Sửa mốc thời gian câu ${i + 1} trước.`
    if (i && cue.start < doc.cues[i - 1].end - .0001) return `Câu ${i + 1} chồng thời gian hoặc chưa xếp theo mốc.`
  }
  const end = doc.cues.at(-1)?.end ?? 0
  if (end > duration + .0001) return `Vượt cuối video ${(end - duration).toFixed(2)}s. Rút gọn lời hoặc chọn ít câu hơn để căn.`
  return ''
}

export interface TimingChange { index: number; before: SpeechCue; after: SpeechCue }
export function fitTiming(doc: SpeechDocument, durations: ReadonlyMap<string, number>, duration: number, ripple: boolean, id?: string) {
  const changes: TimingChange[] = []
  let error = validateRepair(doc, duration)
  if (error) return { document: doc, changes, error }
  if (id && !durations.has(id)) return { document: doc, changes, error: 'Tạo lời đọc cho nội dung và giọng hiện tại trước khi căn thời lượng.' }
  let cursor = 0
  const cues = doc.cues.map((cue, index) => {
    const length = millis(cue.end) - millis(cue.start)
    const audio = durations.get(cue.id)
    const target = (!id || cue.id === id) && audio != null && Number.isFinite(audio) && audio > 0 && audio > cue.end - cue.start + .04
      ? Math.ceil(audio * 1000) : length
    const start = ripple ? Math.max(millis(cue.start), cursor) : millis(cue.start)
    const end = start + Math.max(length, target)
    cursor = end
    const after = { ...cue, start: start / 1000, end: end / 1000 }
    if (Math.abs(after.start - cue.start) > .0001 || Math.abs(after.end - cue.end) > .0001) changes.push({ index, before: cue, after })
    if (!ripple && doc.cues[index + 1] && end > millis(doc.cues[index + 1].start)) {
      error ||= `Câu ${index + 1} cần thêm ${((end - millis(doc.cues[index + 1].start)) / 1000).toFixed(2)}s trước câu ${index + 2}. Cho phép dời câu sau hoặc rút gọn lời.`
    }
    return after
  })
  const result = { ...doc, cues }
  error ||= validateRepair(result, duration)
  return { document: result, changes, error }
}

export function suggestedSplit(text: string): [string, string] {
  const clean = text.trim()
  const boundaries = [...clean.matchAll(/\s+/g)].map(match => match.index!)
  const punctuation = boundaries.filter(index => /[.!?;,:…]$/.test(clean.slice(0, index)) && index > clean.length * .25 && index < clean.length * .75)
  const candidates = punctuation.length ? punctuation : boundaries
  if (!candidates.length) return [clean, '']
  const at = candidates.reduce((best, index) => Math.abs(index - clean.length / 2) < Math.abs(best - clean.length / 2) ? index : best)
  return [clean.slice(0, at).trim(), clean.slice(at).trim()]
}

export function splitCue(doc: SpeechDocument, id: string, parts: [string, string], newId = () => crypto.randomUUID()): SpeechDocument {
  if (doc.cues.length >= MAX_SPEECH_CUES) throw new Error(`Kịch bản đã đủ ${MAX_SPEECH_CUES.toLocaleString('vi-VN')} câu. Bỏ bớt câu trước khi tách.`)
  const index = doc.cues.findIndex(cue => cue.id === id)
  if (index < 0) throw new Error('Không tìm thấy câu cần tách.')
  const original = doc.cues[index]
  const text = parts.map(part => part.trim())
  if (text.some(part => !part || part.length > 1500)) throw new Error('Mỗi phần cần có từ 1 đến 1.500 ký tự.')
  const start = millis(original.start), end = millis(original.end)
  if (end - start < 200) throw new Error('Khung dưới 0,2s quá ngắn để tách. Tăng thời lượng trước.')
  const middle = Math.max(start + 100, Math.min(end - 100, start + Math.round((end - start) * text[0].length / (text[0].length + text[1].length))))
  const first = { ...original, id: newId(), text: text[0], start: start / 1000, end: middle / 1000 }
  const second = { ...original, id: newId(), text: text[1], start: middle / 1000, end: end / 1000 }
  return { ...doc, cues: [...doc.cues.slice(0, index), first, second, ...doc.cues.slice(index + 1)] }
}

export function shorterBudget(cue: SpeechCue, audioDuration?: number | null) {
  const slot = Math.max(0, cue.end - cue.start)
  const audioBudget = audioDuration && audioDuration > 0 ? cue.text.trim().length * slot / audioDuration * .95 : Infinity
  return Math.max(1, Math.floor(Math.min(MAX_CAPTION_CHARS, slot * MAX_CHARS_PER_SECOND, audioBudget) + 1e-9))
}

export function speechReadiness(doc: SpeechDocument, state: SpeechState | null) {
  const ready: string[] = [], long: string[] = [], missing: string[] = []
  const index = speechAudioIndex(state)
  for (const cue of doc.cues) {
    const audio = matchingAudio(cue, state, index)
    if (!audio) missing.push(cue.id)
    else if (audio.duration! > cue.end - cue.start + .04) long.push(cue.id)
    else ready.push(cue.id)
  }
  return { ready, long, missing }
}

// Use only free gaps; never move another speaker or silently truncate speech.
export function fitSelectedSpeech(doc: SpeechDocument, state: SpeechState, selected: ReadonlySet<string>, maxSpeed = 1.25) {
  const error = validateRepair(doc, state.duration)
  if (error) throw new Error(error)
  const regenerate: string[] = [], unresolved: string[] = []
  const audioIndex = speechAudioIndex(state)
  const cues = doc.cues.map((cue, index) => {
    const audio = selected.has(cue.id) ? matchingAudio(cue, state, audioIndex) : undefined
    if (!audio || audio.duration! <= cue.end - cue.start + .04) return cue
    const availableEnd = Math.min(state.duration, doc.cues[index + 1]?.start ?? state.duration)
    const end = Math.max(cue.end, Math.min(availableEnd, cue.start + Math.ceil(audio.duration! * 1000) / 1000))
    if (audio.duration! <= end - cue.start + .04) return { ...cue, end }
    const speed = Math.ceil(cue.speed * audio.duration! / Math.max(.01, end - cue.start - .02) * 100) / 100
    if (speed > maxSpeed) { unresolved.push(cue.id); return { ...cue, end } }
    regenerate.push(cue.id)
    return { ...cue, end, speed }
  })
  return { document: { ...doc, cues }, regenerate, unresolved }
}

export interface ShorteningCandidate { id?: string; original: SpeechDocument; document: SpeechDocument; selection: Record<string, number> }
export function shorteningConflicts(doc: SpeechDocument, candidate: ShorteningCandidate, applied: ReadonlyMap<string, string> = new Map()) {
  const original = candidate.original
  const before = new Map(original.cues.map(c => [c.id, c]))
  const current = new Map(doc.cues.map(c => [c.id, c]))
  const stale = doc.revision < original.revision || doc.timeline !== original.timeline || doc.mode !== original.mode
  return new Set(Object.keys(candidate.selection).filter(id => {
    const cue = current.get(id), source = before.get(id)
    return stale || !cue || !source ||
      (cue.text !== source.text && cue.text !== applied.get(id)?.trim()) ||
      cue.start !== source.start || cue.end !== source.end || cue.voice_id !== source.voice_id || cue.speed !== source.speed || cue.speaker !== source.speaker
  }))
}

export function applyShortening(doc: SpeechDocument, candidate: ShorteningCandidate, texts: ReadonlyMap<string, string>, applied: ReadonlyMap<string, string> = new Map()) {
  const ids = new Set(doc.cues.map(cue => cue.id))
  if (!texts.size || [...texts.keys()].some(id => !ids.has(id) || !Object.hasOwn(candidate.selection, id))) throw new Error('Chọn ít nhất một câu trong bản rút gọn.')
  const conflicts = shorteningConflicts(doc, candidate, applied)
  if ([...texts.keys()].some(id => conflicts.has(id))) throw new Error('Kịch bản đã đổi ở câu được chọn. Bỏ chọn câu đó hoặc rút gọn lại để tránh ghi đè.')
  const cues = doc.cues.map(cue => {
    const text = texts.get(cue.id)?.trim()
    if (text === undefined) return cue
    if (!text || text.length > 1500) throw new Error('Lời đọc phải có từ 1 đến 1.500 ký tự.')
    return { ...cue, text }
  })
  return { ...doc, cues }
}

export function restoreShortening(doc: SpeechDocument, candidate: ShorteningCandidate, texts: ReadonlyMap<string, string>) {
  if (doc.timeline !== candidate.original.timeline || doc.mode !== candidate.original.mode || doc.revision < candidate.original.revision) return { document: doc, count: 0 }
  const original = new Map(candidate.original.cues.map(c => [c.id, c.text]))
  let count = 0
  const cues = doc.cues.map(cue => {
    const before = original.get(cue.id)
    if (before === undefined || cue.text === before || cue.text !== texts.get(cue.id)?.trim() || !Object.hasOwn(candidate.selection, cue.id)) return cue
    count++
    return { ...cue, text: before }
  })
  return { document: { ...doc, cues }, count }
}
