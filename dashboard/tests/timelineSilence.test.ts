import assert from 'node:assert/strict'
import test from 'node:test'
import { MAX_AUDIO_CLIPS } from '../src/lib/mediaLimits.ts'
import { planSilenceCuts } from '../src/components/projects/postproduction/timeline/silence.ts'
import type { SilenceOptions } from '../src/components/projects/postproduction/timeline/silence.ts'
import { clipSilences } from '../src/components/projects/postproduction/timeline/audio.ts'
import * as op from '../src/components/projects/postproduction/timeline/operations.ts'
import type { Asset, Clip, Document, Visuals } from '../src/components/projects/postproduction/timeline/types.ts'

const source: Asset = { id: 'source', owner: 'video', ref: 'original', name: 'Gốc', kind: 'video', category: 'source',
  duration: 60, width: 160, height: 96, fps: 30, has_audio: true, url: '/source', visuals_ready: true }
const speech: Asset = { ...source, id: 'speech', name: 'Giọng', kind: 'audio', category: 'voice' }
const music: Asset = { ...speech, id: 'music', category: 'music' }
const assets = [source, speech, music]
const opts: SilenceOptions = { source: 'voices', threshold: -35, minimum: .4, padding: .1, trimEdges: false }
const cue = (id: string, start: number, duration: number, track = 'voice'): Clip => ({ id, asset_id: track === 'music' ? 'music' : 'speech',
  track_id: track, start, duration, source_in: 0, gain: 1, group: null })
function setup(): Document {
  return { version: 1, fps: 30, width: 160, height: 96, tracks: [
    { id: 'v', name: 'Hình', kind: 'video', locked: false, muted: false },
    { id: 'a', name: 'Âm gốc', kind: 'audio', role: 'original', locked: false, muted: false },
    { id: 'voice', name: 'Giọng', kind: 'audio', role: 'voice', locked: false, muted: false },
    { id: 'music', name: 'Nhạc', kind: 'audio', role: 'music', locked: false, muted: false },
  ], clips: [
    { ...cue('pic', 0, 10), asset_id: 'source', track_id: 'v', group: 'av' },
    { ...cue('original', 0, 10), asset_id: 'source', track_id: 'a', group: 'av' },
    cue('s1', 0, 2), cue('s2', 5, 2), cue('bg', 0, 10, 'music'),
  ] }
}
const waveform = (peaks: number[], interval = .1): Visuals => ({ peaks, interval, duration: peaks.length * interval, thumbnail_count: 0 })
const pairs = (ranges: { start: number; end: number }[]) => ranges.map(r => [r.start, r.end])

test('voice pauses need no waveforms, preserve complete overlapping sentences and ignore backing music', () => {
  const d = setup()
  assert.deepEqual(pairs(planSilenceCuts(d, assets, {}, opts).ranges), [[2.1, 4.9]])
  d.clips.push(cue('overlap', 1, 3))
  assert.deepEqual(pairs(planSilenceCuts(d, assets, {}, opts).ranges), [[4.1, 4.9]])
  assert.deepEqual(planSilenceCuts(d, assets, {}, opts).missingIds, [])
  const edges = planSilenceCuts(d, assets, {}, { ...opts, trimEdges: true }).ranges
  assert.deepEqual(pairs(edges), [[4.1, 4.9], [7.1, 9.9]])
})

test('quiet original audio never cuts an overlapping narration, even when original volume is lowered', () => {
  const d = setup()
  d.clips.find(c => c.id === 'original')!.gain = .01
  const wave = waveform([...Array(10).fill(.5), ...Array(50).fill(0), ...Array(40).fill(.5)])
  const plan = planSilenceCuts(d, assets, { source: wave }, { ...opts, source: 'original' })
  assert.deepEqual(pairs(plan.ranges), [[2.1, 4.9]])
  d.clips.find(c => c.id === 'original')!.gain = 1
  assert.deepEqual(planSilenceCuts(d, assets, { source: wave }, { ...opts, source: 'original' }).ranges, plan.ranges)
})

test('analysis covers all trimmed/speed-changed original clips and combines overlapping sound', () => {
  const d = setup(); d.clips = []
  d.clips.push({ ...cue('a1', 0, 5, 'a'), asset_id: 'source', speed: 2 },
    { ...cue('a2', 5, 5, 'a'), asset_id: 'source', source_in: 0, speed: 2 })
  const wave = waveform([...Array(20).fill(.5), ...Array(20).fill(0), ...Array(60).fill(.5)])
  assert.deepEqual(pairs(planSilenceCuts(d, assets, { source: wave }, { ...opts, source: 'original' }).ranges), [[1.1, 1.9], [6.1, 6.9]])
  d.clips.push({ ...cue('loud-overlay', 1, 1, 'a'), asset_id: 'source', source_in: 0 })
  assert.deepEqual(pairs(planSilenceCuts(d, assets, { source: wave }, { ...opts, source: 'original' }).ranges), [[6.1, 6.9]])
})

test('missing waveforms block partial analysis; clipped pauses recheck minimum output duration', () => {
  const d = setup()
  const plan = planSilenceCuts(d, assets, {}, { ...opts, source: 'original' })
  assert.deepEqual(plan.missingIds, ['source']); assert.deepEqual(plan.ranges, [])
  const c = { ...d.clips[1], source_in: 2.9, duration: .1 }
  assert.deepEqual(clipSilences(c, waveform(Array(30).fill(0)), -35, .4), [])
  assert.deepEqual(planSilenceCuts(d, assets, { source: waveform([]) }, { ...opts, source: 'original' }).ranges, [])
})

test('selection mode stays inside the selected clip and preserves a frame-safe breathing margin', () => {
  const d = setup(); d.clips = [{ ...d.clips[1], start: 2, duration: 4, source_in: 1 }]
  const wave = waveform([...Array(20).fill(.5), ...Array(20).fill(0), ...Array(20).fill(.5)])
  const plan = planSilenceCuts(d, assets, { source: wave }, { ...opts, source: 'selection', selectedId: 'original', padding: .11 })
  assert.deepEqual(pairs(plan.ranges), [[94 / 30, 146 / 30]])
  assert.ok(plan.ranges.every(r => Number.isInteger(r.start * 30) && Number.isInteger(r.end * 30)))
})

test('many cuts keep AV, caption anchors, speed and volume automation together without mutating the draft', () => {
  let d = setup()
  d = op.layer(d, 'caption', 1)
  const cap = d.clips.at(-1)!
  d = op.patch(d, cap.id, { duration: 7 })
  d = op.patch(d, 'bg', { fade_in: 2, fade_out: 2 })
  const before = JSON.stringify(d), cuts = [{ start: 2.1, end: 4.9 }, { start: 8, end: 9 }]
  const actual = op.cutRanges(d, cuts)
  let expected = d
  for (const r of [...cuts].reverse()) expected = op.cutRange(expected, r.start, r.end)
  const timing = (doc: Document) => doc.clips.map(c => [c.track_id, c.asset_id, ...[c.start, c.source_in, c.duration].map(n => Math.round(n * 1e6) / 1e6)]).sort((a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b)))
  assert.deepEqual(timing(actual), timing(expected))
  assert.ok(Math.abs(op.length(actual) - 6.2) < 1e-6)
  for (const c of actual.clips.filter(c => c.anchor)) {
    const parent = actual.clips.find(p => p.id === c.anchor)!
    assert.ok(parent && c.start >= parent.start - 1e-6 && op.end(c) <= op.end(parent) + 1e-6)
  }
  for (const c of actual.clips.filter(c => c.group)) {
    const linked = actual.clips.filter(p => p.group === c.group)
    assert.equal(new Set(linked.map(p => `${p.start}:${p.duration}:${p.source_in}`)).size, 1)
  }
  op.validate(actual, assets)
  assert.equal(JSON.stringify(d), before)
})

test('bulk cuts merge overlapping selections, reject locked layers and never partially delete on failure', () => {
  const d = setup(), before = JSON.stringify(d)
  const next = op.cutRanges(d, [{ start: 2, end: 4 }, { start: 3, end: 5 }])
  assert.equal(op.length(next), 7)
  d.tracks[3].locked = true
  assert.throws(() => op.cutRanges(d, [{ start: 2, end: 3 }]), /đang khóa/)
  d.tracks[3].locked = false
  assert.throws(() => op.cutRanges(d, [{ start: 0, end: 10 }]), /giữ lại/)
  assert.equal(JSON.stringify(d), before)
})

test('a 60-minute draft can close a thousand gaps in one operation and still satisfy audio limits', () => {
  const d = setup(); d.clips = d.clips.slice(0, 2).map(c => ({ ...c, duration: 3600 }))
  const cuts = Array.from({ length: 1000 }, (_, i) => ({ start: i * 3 + 1, end: i * 3 + 2 }))
  const result = op.cutRanges(d, cuts)
  assert.equal(op.length(result), 2600)
  assert.equal(result.clips.length, 2002)
  op.validate(result, [{ ...source, duration: 3600 }])
  const audio = result.clips.find(c => c.track_id === 'a')!
  const overLimit = { ...result, clips: [...result.clips, ...Array.from({ length: MAX_AUDIO_CLIPS }, (_, i) => ({ ...audio, id: `extra-${i}`, group: null }))] }
  assert.throws(() => op.validate(overLimit, [{ ...source, duration: 3600 }]), new RegExp(`${MAX_AUDIO_CLIPS} đoạn âm thanh`))
})
