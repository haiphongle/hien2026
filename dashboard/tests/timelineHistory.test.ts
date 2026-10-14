import assert from 'node:assert/strict'
import test from 'node:test'
import { readHistory, writeHistory } from '../src/components/projects/postproduction/timeline/history.ts'
import type { Document } from '../src/components/projects/postproduction/timeline/types.ts'

function storage() {
  const values = new Map<string, string>()
  return { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value) }, values }
}
function document(at = 0): Document {
  return { version: 1, fps: 30, width: 160, height: 96, tracks: [
    { id: 'picture', name: 'Hình', kind: 'video', muted: false, locked: false },
    { id: 'sound', name: 'Tiếng', kind: 'audio', muted: false, locked: false },
  ], clips: [{ id: 'clip', asset_id: 'asset', track_id: 'picture', start: at, source_in: 0, duration: 1, gain: 1, group: null }] }
}

test('undo and redo survive reopening, including server property-order normalization', () => {
  const disk = storage(), saved = document(1), previous = document(0), next = document(2)
  assert.ok(writeHistory(disk, 'video', saved, { undo: [previous], redo: [next] }))
  const reordered = { clips: saved.clips, tracks: saved.tracks, height: 96, width: 160, fps: 30, version: 1 } as Document
  assert.deepEqual(readHistory(disk, 'video', reordered), { undo: [previous], redo: [next] })
  assert.deepEqual(readHistory(disk, 'another-video', saved), { undo: [], redo: [] })
})

test('changed server document invalidates stale local undo without replacing the server draft', () => {
  const disk = storage(), saved = document(1)
  writeHistory(disk, 'video', saved, { undo: [document()], redo: [] })
  const newer = document(3), snapshot = structuredClone(newer)
  assert.deepEqual(readHistory(disk, 'video', newer), { undo: [], redo: [] })
  assert.deepEqual(newer, snapshot)
})

test('old drafts keep undo when the server adds default fields for audio and overlays', () => {
  const disk = storage(), old = document(1), previous = document()
  writeHistory(disk, 'video', old, { undo: [previous], redo: [] })
  const current = structuredClone(old)
  current.tracks = current.tracks.map(t => ({ ...t, solo: false, role: 'original' }))
  current.clips = current.clips.map(c => ({ ...c, anchor: null, speed: 1, fade_in: 0, fade_out: 0,
    envelope: [], text: '', caption: { x: .5, y: .85 }, effect: null, colors: null, origin: '', transition: null }))
  current.ducking = { enabled: false, amount: .25, attack: .2, release: .35 }
  assert.deepEqual(readHistory(disk, 'video', current), { undo: [previous], redo: [] })
})

test('corrupt storage and denied writes do not affect saving the timeline document', () => {
  const disk = storage(), saved = document()
  writeHistory(disk, 'video', saved, { undo: [], redo: [] })
  const key = [...disk.values.keys()][0]
  const data = JSON.parse(disk.getItem(key)!)
  disk.setItem(key, JSON.stringify({ ...data, undo: [null] }))
  assert.deepEqual(readHistory(disk, 'video', saved), { undo: [], redo: [] })
  disk.setItem(key, '{broken')
  assert.deepEqual(readHistory(disk, 'video', saved), { undo: [], redo: [] })
  assert.equal(writeHistory({ getItem: () => null, setItem: () => { throw new Error('QuotaExceededError') } }, 'video', saved, { undo: [], redo: [] }), false)
})

test('transition defaults in an older signature keep undo, but changed effects invalidate stale history', () => {
  const disk = storage(), saved = document()
  writeHistory(disk, 'video', saved, { undo: [document(1)], redo: [] })
  const [key, raw] = [...disk.values.entries()][0]
  const data = JSON.parse(raw), oldSignature = JSON.parse(data.saved)
  oldSignature.clips.forEach((c: Record<string, unknown>) => { c.transition = null })
  data.saved = JSON.stringify(oldSignature)
  disk.setItem(key, JSON.stringify(data))
  assert.equal(readHistory(disk, 'video', saved).undo.length, 1)
  saved.clips[0].transition = { kind: 'dissolve', duration: .4 }
  assert.equal(readHistory(disk, 'video', saved).undo.length, 0)
})

test('large histories keep recent steps within a bounded storage budget', () => {
  const disk = storage(), saved = document(), snapshots = Array.from({ length: 50 }, (_, at) => ({ ...document(at), clips: Array.from({ length: 300 }, (_, index) => ({ ...saved.clips[0], id: `clip-${index}` })) }))
  assert.ok(writeHistory(disk, 'video', saved, { undo: snapshots, redo: [] }))
  const restored = readHistory(disk, 'video', saved)
  assert.ok(restored.undo.length > 0 && restored.undo.length < 50)
  assert.equal(restored.undo.at(-1)!.clips.length, 300)
  assert.equal(restored.undo.at(-1)!.clips[0].id, 'clip-0')
  assert.ok([...disk.values.values()][0].length <= 750_000)
})

test('output frame and legacy migration share persistent undo; new defaults preserve old histories', () => {
  const disk = storage(), old = document()
  writeHistory(disk, 'video', old, { undo: [document(1)], redo: [] })
  assert.equal(readHistory(disk, 'video', { ...old, frame: null, legacy_imported: false }).undo.length, 1)
  const merged: Document = { ...old, legacy_imported: true, frame: { preset: '9:16', width: 1080, height: 1920, fit: 'contain', background: 'blur', color: '#000000', zoom: 1.2, x: .3, y: .5 } }
  writeHistory(disk, 'video', merged, { undo: [old], redo: [] })
  assert.deepEqual(readHistory(disk, 'video', merged).undo[0], old)
  assert.equal(readHistory(disk, 'video', { ...merged, frame: { ...merged.frame!, zoom: 1 } }).undo.length, 0)
})
