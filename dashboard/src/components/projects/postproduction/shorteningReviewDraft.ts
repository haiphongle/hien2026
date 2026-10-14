import type { ShorteningCandidate } from './speechRepair'

export interface ShorteningReviewDraft {
  texts: Map<string, string>; accepted: Set<string>; applied: Map<string, string>
}
const identity = (candidate: ShorteningCandidate) => candidate.id ?? JSON.stringify(candidate)

// One bounded record per video; a new AI candidate starts a fresh review.
export function readShorteningReview(raw: string | null, candidate: ShorteningCandidate): ShorteningReviewDraft {
  const rows = candidate.document.cues.filter(c => Object.hasOwn(candidate.selection, c.id))
  const texts = new Map(rows.map(c => [c.id, c.text]))
  const defaults = { texts, accepted: new Set(texts.keys()), applied: new Map(texts) }
  try {
    const saved = JSON.parse(raw ?? 'null')
    if (saved?.version !== 1 || saved.candidate !== identity(candidate)) return defaults
    const restoreTexts = (value: unknown, allowEmpty: boolean) => {
      const restored = new Map(texts)
      if (value && typeof value === 'object' && !Array.isArray(value)) {
        for (const [id, text] of Object.entries(value)) {
          if (texts.has(id) && typeof text === 'string' && text.length <= 1500 && (allowEmpty || text.trim())) restored.set(id, text)
        }
      }
      return restored
    }
    return {
      texts: restoreTexts(saved.texts, true), applied: restoreTexts(saved.applied, false),
      accepted: Array.isArray(saved.accepted) ? new Set(saved.accepted.filter((id: unknown): id is string => typeof id === 'string' && texts.has(id))) : defaults.accepted,
    }
  } catch { return defaults }
}

export function serializeShorteningReview(candidate: ShorteningCandidate, draft: ShorteningReviewDraft) {
  return JSON.stringify({ version: 1, candidate: identity(candidate), texts: Object.fromEntries(draft.texts),
    accepted: [...draft.accepted], applied: Object.fromEntries(draft.applied) })
}
