import assert from 'node:assert/strict'
import test from 'node:test'
import { applySpeakers, assignNarratorVoice, renameSpeaker } from '../src/components/projects/postproduction/speechSpeakers.ts'
import type { SpeechDocument } from '../src/components/projects/postproduction/speechTypes.ts'

const doc: SpeechDocument = { revision: 3, timeline: 'source', mode: 'srt', cues: [
  { id: 'a', start: 0, end: 2, text: 'Xin chào.', speaker: 'A', voice_id: 'old', speed: 1 },
  { id: 'b', start: 3, end: 4, text: 'Chào bạn.', speaker: 'B', voice_id: 'other', speed: 1.2 },
] }

test('using one narrator preserves cue identities, speakers, text, timing and speed without mutating the saved document', () => {
  const next = assignNarratorVoice(doc, 'narrator')
  assert.deepEqual(next, { ...doc, cues: doc.cues.map(cue => ({ ...cue, voice_id: 'narrator' })) })
  assert.deepEqual(doc.cues.map(cue => cue.voice_id), ['old', 'other'])
  assert.throws(() => assignNarratorVoice(doc, ''))
})

test('speaker suggestions preserve text, timings, ids and unselected cues', () => {
  const next = applySpeakers(doc, { a: 'Người nói 1', b: '' }, { 'Người nói 1': 'Phúc' }, { 'Người nói 1': 'selected' })
  assert.deepEqual(next.cues[0], { ...doc.cues[0], speaker: 'Phúc', voice_id: 'selected' })
  assert.equal(next.cues[1], doc.cues[1])
  assert.equal(doc.cues[0].speaker, 'A')
  assert.equal(next.revision, doc.revision)
})

test('changing person clears an inherited voice, retaining an unchanged person preserves it', () => {
  assert.equal(applySpeakers(doc, { a: 'new' }, {}, {}).cues[0].voice_id, '')
  assert.equal(applySpeakers(doc, { a: 'cluster' }, { cluster: 'A' }, {}).cues[0].voice_id, 'old')
})

test('rename merges labels without modifying per-line voice overrides or source document', () => {
  const renamed = renameSpeaker(doc, 'B', ' A ')
  assert.deepEqual(renamed.cues.map(c => c.speaker), ['A', 'A'])
  assert.equal(renamed.cues[1].voice_id, 'other')
  assert.equal(doc.cues[1].speaker, 'B')
  assert.throws(() => renameSpeaker(doc, 'A', '  '))
  assert.throws(() => applySpeakers(doc, { a: 'one' }, { one: '' }, {}))
})
