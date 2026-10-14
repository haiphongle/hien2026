import assert from 'node:assert/strict'
import test from 'node:test'
import { applyShortening, restoreShortening, fitSelectedSpeech, speechReadiness, MAX_SPEECH_CUES, fitTiming, matchingAudio, shorterBudget, splitCue, suggestedSplit, validateRepair } from '../src/components/projects/postproduction/speechRepair.ts'
import type { SpeechCue, SpeechDocument, SpeechState } from '../src/components/projects/postproduction/speechTypes.ts'
import { readShorteningReview, serializeShorteningReview } from '../src/components/projects/postproduction/shorteningReviewDraft.ts'

const cue = (id: string, start: number, end: number, text = 'Xin chào bạn.'): SpeechCue => ({ id, start, end, text, speaker: 'Phúc', voice_id: 'test', speed: 1 })
const document = (cues: SpeechCue[]): SpeechDocument => ({ revision: 3, timeline: 'source', mode: 'srt', cues })

test('2.64s audio extends a 0.88s slot into a gap, keeps all other cues and cache identity', () => {
  const original = document([cue('a', 0, .88), cue('b', 5, 6)])
  const result = fitTiming(original, new Map([['a', 2.64]]), 10, false, 'a')
  assert.equal(result.error, '')
  assert.equal(result.document.cues[0].end, 2.64)
  assert.deepEqual(result.document.cues[1], original.cues[1])
  assert.equal(result.changes.length, 1)
  assert.equal(original.cues[0].end, .88)
  assert.equal(result.document.revision, 3)
  assert.equal(result.document.cues[0].id, 'a')
})

test('collision blocks default preview; opt-in ripple consumes gaps and preserves downstream durations', () => {
  const original = document([cue('a', 0, .88), cue('b', 1, 2), cue('c', 3.5, 4.5), cue('d', 6, 7)])
  const audio = new Map([['a', 2.64]])
  assert.match(fitTiming(original, audio, 10, false, 'a').error, /Câu 1 cần thêm/)
  const result = fitTiming(original, audio, 10, true, 'a')
  assert.equal(result.error, '')
  assert.deepEqual(result.document.cues.map(c => [c.start, c.end]), [[0, 2.64], [2.64, 3.64], [3.64, 4.64], [6, 7]])
  assert.equal(validateRepair(result.document, 10), '')
})

test('overflow is blocked, never truncates or partially applies the proposal', () => {
  const original = document([cue('a', 0, .88), cue('b', 1, 2)])
  const result = fitTiming(original, new Map([['a', 2.64]]), 3, true)
  assert.match(result.error, /Vượt cuối video 0.64s/)
  assert.equal(result.document.cues[1].end, 3.64)
  assert.equal(original.cues[1].end, 2)
})

test('batch fit stretches measured long cues only, selected fit does not alter another long cue', () => {
  const original = document([cue('a', 0, 1), cue('b', 3, 4), cue('c', 6, 7)])
  const audio = new Map([['a', 2], ['b', 2.5]])
  assert.deepEqual(fitTiming(original, audio, 10, false).document.cues.map(c => c.end), [2, 5.5, 7])
  assert.deepEqual(fitTiming(original, audio, 10, false, 'a').document.cues.map(c => c.end), [2, 4, 7])
  assert.match(fitTiming(original, audio, 10, false, 'c').error, /Tạo lời đọc/)
})

test('invalid and overlapping source timestamps cannot be repaired silently', () => {
  assert.match(fitTiming(document([cue('a', 0, NaN)]), new Map(), 5, true).error, /mốc thời gian/)
  assert.match(fitTiming(document([cue('a', 0, 3), cue('b', 2, 4)]), new Map(), 5, true).error, /chồng thời gian/)
  assert.match(validateRepair(document([]), 0), /thời lượng video/)
  const noChange = fitTiming(document([cue('a', 0, 1)]), new Map([['a', 1.03]]), 2, true)
  assert.equal(noChange.changes.length, 0)
})

test('audio duration can be reused after timing/speaker edits but not after text, voice or speed edits', () => {
  const original = cue('a', 0, 1)
  const state = { document: document([original]), audio: [{ id: 'a', duration: 2.64, status: 'TOO_LONG', audio_url: '/audio', error: null }] } as SpeechState
  assert.equal(matchingAudio({ ...original, start: 2, end: 5, speaker: 'B' }, state)?.duration, 2.64)
  assert.equal(matchingAudio({ ...original, text: 'Lời mới' }, state), undefined)
  assert.equal(matchingAudio({ ...original, voice_id: 'other' }, state), undefined)
  assert.equal(matchingAudio({ ...original, speed: 1.1 }, state), undefined)
  assert.equal(matchingAudio(cue('new', 0, 1), state), undefined)
})

test('split preserves every word, speaker, voice, speed and total window without overlaps', () => {
  const text = 'Xin chào Việt Nam! Hôm nay trời thật đẹp.'
  const parts = suggestedSplit(text)
  assert.equal(parts.join(' '), text)
  assert.equal(parts[0], 'Xin chào Việt Nam!')
  const original = document([{ ...cue('a', 1.12, 4.37, text), speed: 1.25 }, cue('b', 5, 6)])
  const result = splitCue(original, 'a', parts)
  assert.equal(result.cues.length, 3)
  assert.equal(new Set(result.cues.map(c => c.id)).size, 3)
  assert.ok(result.cues[0].id !== 'a' && result.cues[1].id !== 'a')
  assert.equal(result.cues[0].start, 1.12)
  assert.equal(result.cues[0].end, result.cues[1].start)
  assert.equal(result.cues[1].end, 4.37)
  assert.deepEqual(result.cues.slice(0, 2).map(c => [c.speaker, c.voice_id, c.speed]), [['Phúc', 'test', 1.25], ['Phúc', 'test', 1.25]])
  assert.equal(validateRepair(result, 10), '')
  assert.equal(original.cues.length, 2)
})

test('unsaved timing changes keep per-cue playback available, while editing one text invalidates only that cue', () => {
  const original = document([cue('a', 0, .88), cue('b', 5, 6)])
  const state = { document: original, audio: original.cues.map(c => ({
    id: c.id, duration: c.id === 'a' ? 2.64 : .9, status: c.id === 'a' ? 'TOO_LONG' : 'READY', audio_url: `/audio/${c.id}`, error: null,
  })) } as SpeechState
  const repaired = fitTiming(original, new Map([['a', 2.64]]), 10, false).document
  assert.deepEqual(repaired.cues.map(c => matchingAudio(c, state)?.audio_url), ['/audio/a', '/audio/b'])
  const edited = { ...repaired.cues[0], text: 'Nội dung mới.' }
  assert.equal(matchingAudio(edited, state), undefined)
  assert.equal(matchingAudio(repaired.cues[1], state)?.audio_url, '/audio/b')
})

test('split refuses empty parts, tiny slots and exceeding the shared cue limit', () => {
  assert.deepEqual(suggestedSplit('Hello'), ['Hello', ''])
  assert.throws(() => splitCue(document([cue('a', 0, 1)]), 'a', ['Hello', '']), /1 đến 1.500/)
  assert.throws(() => splitCue(document([cue('a', 0, .1)]), 'a', ['Hello', 'World']), /quá ngắn/)
  assert.throws(() => splitCue(document(Array.from({ length: MAX_SPEECH_CUES }, (_, i) => cue(String(i), i, i + 1))), '0', ['Hi', 'There']), /đủ .* câu/)
})

test('hour-long subtitles can be repaired and split after the thousandth cue', () => {
  const original = document(Array.from({ length: 1200 }, (_, i) => cue(String(i), i * 3, (i + 1) * 3)))
  assert.equal(validateRepair(original, 3600), '')
  const result = splitCue(original, '1199', ['Xin chào', 'bạn.'])
  assert.equal(result.cues.length, 1201)
  assert.equal(result.cues.at(-1)?.end, 3600)
  assert.equal(validateRepair(result, 3600), '')
})

test('shortening budget uses both actual voice duration and caption readability', () => {
  assert.equal(shorterBudget(cue('a', 0, .88, 'a'.repeat(60)), 2.64), 19)
  assert.equal(shorterBudget(cue('a', 0, 20, 'a'.repeat(200))), 84)
  assert.equal(shorterBudget(cue('a', 0, .01)), 1)
})

const measuredState = (doc: SpeechDocument, durations: number[], duration = 10): SpeechState => ({
  document: doc, duration, timeline: doc.timeline, job: null, characters: [],
  audio: doc.cues.map((c, i) => ({ id: c.id, duration: durations[i], status: durations[i] > c.end - c.start + .04 ? 'TOO_LONG' : 'READY', audio_url: `/audio/${c.id}`, error: null })),
})

test('bulk fit consumes only gaps, caps speed and reports rather than clips impossible speech', () => {
  const original = document([cue('a', 0, 1), cue('b', 2, 3), cue('c', 4, 5), cue('d', 6, 7)])
  const state = measuredState(original, [1.5, 2.2, 5, .5])
  const result = fitSelectedSpeech(original, state, new Set(['a', 'b', 'c']))
  assert.equal(result.document.cues[0].end, 1.5)
  assert.equal(result.document.cues[0].speed, 1)
  assert.equal(result.document.cues[1].end, 4)
  assert.equal(result.document.cues[1].speed, 1.12)
  assert.deepEqual(result.regenerate, ['b'])
  assert.deepEqual(result.unresolved, ['c'])
  assert.equal(result.document.cues[2].speed, 1)
  assert.deepEqual(result.document.cues[3], original.cues[3])
  assert.equal(validateRepair(result.document, 10), '')
  assert.deepEqual(original.cues.map(c => c.end), [1, 3, 5, 7])
})

test('bulk fit leaves unselected, stale audio and other speakers untouched', () => {
  const original = document([cue('a', 0, 1), { ...cue('b', 2, 3), speaker: 'Linh' }])
  const state = measuredState(original, [2.5, 4], 3)
  assert.deepEqual(fitSelectedSpeech(original, state, new Set(['a'])).document.cues[1], original.cues[1])
  const edited = { ...original, cues: original.cues.map(c => ({ ...c, text: 'Lời mới' })) }
  assert.deepEqual(fitSelectedSpeech(edited, state, new Set(['a', 'b'])).document, edited)
  assert.throws(() => fitSelectedSpeech(original, { ...state, duration: 2 }, new Set(['a'])), /Vượt cuối/)
})

test('readiness counts saved, missing and overlong speech against current draft content and timings', () => {
  const original = document([cue('a', 0, 1), cue('b', 2, 3), cue('c', 4, 5)])
  const state = measuredState(original, [.5, 2, .5])
  const draft = { ...original, cues: original.cues.map(c => c.id === 'c' ? { ...c, text: 'Edited' } : c) }
  assert.deepEqual(speechReadiness(draft, state), { ready: ['a'], long: ['b'], missing: ['c'] })
  assert.deepEqual(speechReadiness({ ...draft, cues: draft.cues.map(c => c.id === 'b' ? { ...c, end: 4 } : c) }, state).ready, ['a', 'b'])
})

test('AI shortening applies reviewed texts only and preserves all non-text fields', () => {
  const original = document([cue('a', 0, 1), cue('b', 2, 3), cue('c', 4, 5)])
  const candidate = { original, document: original, selection: { a: 20, b: 20 } }
  const result = applyShortening(original, candidate, new Map([['a', '  Chào!  ']]))
  assert.equal(result.cues[0].text, 'Chào!')
  assert.deepEqual(result.cues.slice(1), original.cues.slice(1))
  assert.deepEqual({ ...result.cues[0], text: original.cues[0].text }, original.cues[0])
  assert.equal(original.cues[0].text, 'Xin chào bạn.')
  assert.throws(() => applyShortening(original, candidate, new Map([['c', 'Unselected']])), /Chọn ít nhất/)
  assert.throws(() => applyShortening(original, candidate, new Map([['a', '  ']])), /1 đến/)
  assert.throws(() => applyShortening({ ...original, revision: 2 }, candidate, new Map([['a', 'Hi']])), /Kịch bản đã đổi/)
  assert.throws(() => applyShortening({ ...original, cues: original.cues.map(c => ({ ...c, speed: 1.2 })) }, candidate, new Map([['a', 'Hi']])), /Kịch bản đã đổi/)
})

test('restore original wording after saving and timing repair preserves later manual changes', () => {
  const original = document([cue('a', 0, 1), cue('b', 2, 3)])
  const candidate = { original, document: original, selection: { a: 20, b: 20 } }
  const texts = new Map([['a', 'Chào!'], ['b', 'Xin chào!']])
  const applied = applyShortening(original, candidate, texts)
  const current = { ...applied, revision: 4, cues: applied.cues.map(c => c.id === 'a' ? { ...c, end: 1.5, speed: 1.2 } : { ...c, text: 'Sửa riêng sau đó.' }) }
  const restored = restoreShortening(current, candidate, texts)
  assert.equal(restored.count, 1)
  assert.equal(restored.document.cues[0].text, original.cues[0].text)
  assert.equal(restored.document.cues[0].end, 1.5)
  assert.equal(restored.document.cues[0].speed, 1.2)
  assert.deepEqual(restored.document.cues[1], current.cues[1])
  assert.equal(restored.document.revision, 4)
})

test('review can apply another group after saving without overwriting other edits', () => {
  const original = document([cue('a', 0, 1), cue('b', 2, 3), cue('c', 4, 5)])
  const candidate = { original, document: original, selection: { a: 20, b: 20 } }
  const first = applyShortening(original, candidate, new Map([['a', 'Chào!']]))
  const saved = { ...first, revision: 4, cues: first.cues.map(c => c.id === 'c' ? { ...c, text: 'Sửa riêng.' } : c) }
  const next = applyShortening(saved, candidate, new Map([['b', 'Chào bạn.']]))
  assert.deepEqual(next.cues.map(c => c.text), ['Chào!', 'Chào bạn.', 'Sửa riêng.'])
  assert.equal(next.revision, 4)
})

test('selected conflicts reject the entire group while unrelated deleted or reordered cues are preserved', () => {
  const original = document([cue('a', 0, 1), cue('b', 2, 3), cue('c', 4, 5)])
  const candidate = { original, document: original, selection: { a: 20, b: 20 } }
  for (const edit of [{ text: 'Sửa riêng.' }, { start: .1 }, { end: 1.1 }, { speaker: 'Linh' }, { voice_id: 'other' }, { speed: 1.1 }]) {
    const edited = { ...original, cues: original.cues.map(c => c.id === 'a' ? { ...c, ...edit } : c) }
    assert.throws(() => applyShortening(edited, candidate, new Map([['b', 'Chào!'], ['a', 'Hi']])), /Kịch bản đã đổi/)
    assert.equal(edited.cues[1].text, original.cues[1].text)
  }
  const remaining = { ...original, cues: [original.cues[1], original.cues[0]] }
  assert.deepEqual(applyShortening(remaining, candidate, new Map([['a', 'Hi']])).cues.map(c => [c.id, c.text]), [['b', 'Xin chào bạn.'], ['a', 'Hi']])
  assert.throws(() => applyShortening({ ...original, timeline: 'other' }, candidate, new Map([['a', 'Hi']])), /Kịch bản đã đổi/)
  assert.throws(() => applyShortening({ ...original, mode: 'dialogue' }, candidate, new Map([['a', 'Hi']])), /Kịch bản đã đổi/)
  assert.throws(() => applyShortening({ ...original, cues: [original.cues[1]] }, candidate, new Map([['a', 'Hi']])), /Chọn ít nhất/)
})

test('edited review and selection survive reopening, and restoring uses the last applied wording', () => {
  const original = document([cue('a', 0, 1), cue('b', 2, 3)])
  const candidate = { id: 'first', original, document: { ...original, cues: original.cues.map(c => ({ ...c, text: 'Chào!' })) }, selection: { a: 20, b: 20 } }
  const draft = readShorteningReview(null, candidate)
  draft.texts.set('a', 'Lời duyệt tay.')
  draft.accepted = new Set(['a'])
  const applied = new Map([['a', draft.texts.get('a')!]])
  const current = applyShortening(original, candidate, applied)
  draft.applied = applied
  draft.texts.set('a', 'Sửa tiếp chưa áp dụng.')
  draft.texts.set('b', '')
  const saved = serializeShorteningReview(candidate, draft)
  const restored = readShorteningReview(saved, candidate)
  assert.deepEqual(restored.texts, draft.texts)
  assert.deepEqual(restored.accepted, new Set(['a']))
  assert.equal(restored.applied.get('a'), 'Lời duyệt tay.')
  assert.equal(restoreShortening(current, candidate, restored.applied).document.cues[0].text, original.cues[0].text)
  const updated = applyShortening(current, candidate, new Map([['a', restored.texts.get('a')!]]), restored.applied)
  assert.equal(updated.cues[0].text, 'Sửa tiếp chưa áp dụng.')
  assert.equal(restoreShortening({ ...current, timeline: 'other' }, candidate, restored.applied).count, 0)
  assert.equal(readShorteningReview(saved, { ...candidate, id: 'next' }).texts.get('a'), 'Chào!')
})

test('review draft handles malformed storage without accepting other IDs or invalid text', () => {
  const original = document([cue('a', 0, 1)])
  const candidate = { id: 'review', original, document: original, selection: { a: 20 } }
  const defaults = readShorteningReview(null, candidate)
  for (const raw of ['{', 'null', '[]', '42', '{"version":2}']) assert.deepEqual(readShorteningReview(raw, candidate), defaults)
  for (const text of [null, 42, [], {}, 'x'.repeat(1501)]) {
    const saved = JSON.stringify({ version: 1, candidate: 'review', texts: { a: text, other: 'bad' }, applied: [], accepted: ['a', 'other', null, 4] })
    assert.deepEqual(readShorteningReview(saved, candidate), defaults)
  }
  const empty = { ...defaults, accepted: new Set<string>() }
  assert.equal(readShorteningReview(serializeShorteningReview(candidate, empty), candidate).accepted.size, 0)
})
