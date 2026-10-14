import type { SpeechDocument } from './speechTypes'
import { validateRepair } from './speechRepair.ts'

export interface SpeechCandidate {
  kind: string; document: SpeechDocument; original?: SpeechDocument; language: string
  scope?: 'all' | 'range'; start?: number; end?: number
}

export function candidateRange(candidate: SpeechCandidate): { start: number; end: number } | null {
  if (candidate.kind !== 'transcript' || candidate.scope === 'all') return null
  const { start, end } = candidate
  if (start != null && end != null && Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start) return { start, end }
  if (candidate.scope === 'range') throw new Error('Bản nháp thiếu phạm vi nhận dạng. Nhận dạng lại đoạn cần sửa.')
  return null // Older full-video drafts did not include a range.
}

export function applySpeechCandidate(current: SpeechDocument, candidate: SpeechCandidate, newId = () => crypto.randomUUID()): SpeechDocument {
  if (current.revision !== candidate.document.revision || (current.timeline && current.timeline !== candidate.document.timeline)) {
    throw new Error('Bản nháp thuộc kịch bản hoặc mốc cảnh cũ. Nhận dạng / dịch lại trước khi áp dụng.')
  }
  const range = candidateRange(candidate)
  if (!range) return structuredClone(candidate.document)
  if (current.cues.length && (current.edit_signature ?? '') !== (candidate.document.edit_signature ?? '')) {
    throw new Error('Phụ đề cũ dùng mốc video nguồn, bản nhận dạng dùng mốc timeline. Nhận dạng toàn bộ bản dựng để tránh trộn hai bộ mốc.')
  }
  const kept = current.cues.flatMap(cue => {
    if (cue.end <= range.start || cue.start >= range.end) return [cue]
    // A boundary may cut through an existing sentence. Retain its outside
    // portions with fresh IDs so old full-sentence audio cannot be reused.
    return [
      ...(cue.start < range.start ? [{ ...cue, id: newId(), end: range.start }] : []),
      ...(cue.end > range.end ? [{ ...cue, id: newId(), start: range.end }] : []),
    ]
  })
  const incoming = candidate.document.cues.map(cue => ({ ...cue,
    start: Math.max(range.start, cue.start), end: Math.min(range.end, cue.end),
  })).filter(cue => cue.end > cue.start)
  const result: SpeechDocument = { ...current, timeline: candidate.document.timeline, edit_signature: candidate.document.edit_signature, mode: 'srt', cues: [...kept, ...incoming].sort((a, b) => a.start - b.start) }
  const error = validateRepair(result, Math.max(range.end, result.cues.at(-1)?.end ?? 0))
  if (error) throw new Error(error)
  if (new Set(result.cues.map(cue => cue.id)).size !== result.cues.length) throw new Error('ID câu bị trùng. Nhận dạng lại đoạn cần sửa.')
  return result
}
