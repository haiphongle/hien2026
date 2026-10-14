import assert from 'node:assert/strict'
import test from 'node:test'
import { applySpeechCandidate, candidateRange, type SpeechCandidate } from '../src/components/projects/postproduction/transcriptionDraft.ts'
import { MAX_SPEECH_CUES } from '../src/lib/mediaLimits.ts'
import type { SpeechCue, SpeechDocument } from '../src/components/projects/postproduction/speechTypes.ts'

const cue = (id: string, start: number, end: number, text = id): SpeechCue => ({ id, start, end, text, speaker: 'Phúc', voice_id: 'voice-a', speed: 1.1 })
const document = (cues: SpeechCue[]): SpeechDocument => ({ revision: 3, mode: 'srt', timeline: 'source', cues })
const draft = (cues: SpeechCue[], start = 10, end = 20): SpeechCandidate => ({ kind: 'transcript', language: 'vi', scope: 'range', start, end, document: document(cues) })

test('partial ASR replaces only its range, retaining outside IDs, voices and the immutable undo snapshot', () => {
  const original = document([cue('before', 0, 2), cue('old', 11, 15), cue('after', 21, 23)])
  const before = structuredClone(original)
  const candidate = draft([cue('new', 12, 18)])
  const result = applySpeechCandidate(original, candidate)
  assert.deepEqual(result.cues, [before.cues[0], candidate.document.cues[0], before.cues[2]])
  assert.deepEqual(original, before)
  assert.equal(result.revision, original.revision)
})

test('boundary fragments survive with new IDs so full old narration is not reused', () => {
  const original = document([cue('spanning', 5, 25)])
  let id = 0
  const result = applySpeechCandidate(original, draft([cue('new', 11, 19)]), () => `fragment-${++id}`)
  assert.deepEqual(result.cues.map(c => [c.id, c.start, c.end]), [['fragment-1', 5, 10], ['new', 11, 19], ['fragment-2', 20, 25]])
  assert.equal(result.cues[0].voice_id, 'voice-a')
  assert.equal(original.cues[0].end, 25)
})

test('legacy range drafts merge safely; full-video and translation drafts replace the document', () => {
  const original = document([cue('outside', 30, 32)])
  const candidate = { ...draft([cue('new', 12, 18)]), scope: undefined }
  assert.equal(applySpeechCandidate(original, candidate).cues.length, 2)
  for (const whole of [{ ...candidate, scope: 'all' as const }, { ...candidate, kind: 'translation' }, { ...candidate, start: undefined, end: undefined }]) {
    assert.equal(candidateRange(whole), null)
    assert.deepEqual(applySpeechCandidate(original, whole), candidate.document)
  }
})

test('new empty script accepts its first transcript and acquires source identity', () => {
  const empty: SpeechDocument = { revision: 0, mode: 'dialogue', timeline: '', cues: [] }
  const candidate = draft([cue('first', 11, 12)])
  candidate.document.revision = 0
  assert.equal(applySpeechCandidate(empty, candidate).timeline, 'source')
})

test('invalid bounds, stale revision/timeline and excess merged cues cannot overwrite the draft', () => {
  const original = document([cue('outside', 30, 32)])
  assert.throws(() => applySpeechCandidate(original, { ...draft([]), end: undefined }), /thiếu phạm vi/)
  for (const change of [{ revision: 4 }, { timeline: 'old' }]) {
    assert.throws(() => applySpeechCandidate(original, { ...draft([]), document: { ...document([]), ...change } }), /cũ/)
  }
  const full = document(Array.from({ length: MAX_SPEECH_CUES }, (_, i) => cue(String(i), 30 + i, 31 + i)))
  assert.throws(() => applySpeechCandidate(full, draft([cue('extra', 11, 12)])), /tối đa/)
  assert.equal(full.cues.length, MAX_SPEECH_CUES)
})

test('clips incoming ASR context to the requested window without overlapping retained text', () => {
  const result = applySpeechCandidate(document([cue('before', 0, 10), cue('after', 20, 30)]), draft([cue('new', 9, 21)]))
  assert.deepEqual(result.cues.map(c => [c.start, c.end]), [[0, 10], [10, 20], [20, 30]])
})

test('ASR edit identity survives a range import and cannot mix with source coordinates', () => {
  const candidate = draft([cue('new', 12, 18)])
  candidate.document.edit_signature = 'saved-edit'
  const empty = document([])
  assert.equal(applySpeechCandidate(empty, candidate).edit_signature, 'saved-edit')
  const old = document([cue('old', 11, 15)])
  assert.throws(() => applySpeechCandidate(old, candidate), /trộn hai bộ mốc/)
  old.edit_signature = 'saved-edit'
  assert.equal(applySpeechCandidate(old, candidate).edit_signature, 'saved-edit')
  assert.equal(old.cues[0].id, 'old')
})
