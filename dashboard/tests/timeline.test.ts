import assert from 'node:assert/strict'
import test from 'node:test'
import * as op from '../src/components/projects/postproduction/timeline/operations.ts'
import type { Asset, Clip, Document } from '../src/components/projects/postproduction/timeline/types.ts'

const assets: Asset[] = [{ id: 'a', owner: 'v', ref: 'original', name: 'Video', kind: 'video', category: 'source', duration: 10, width: 160, height: 96, fps: 30, has_audio: true, url: '', visuals_ready: true }]
function document(): Document {
  return { version: 1, fps: 30, width: 160, height: 96, tracks: [{ id: 'v', name: 'Hình', kind: 'video', locked: false, muted: false }, { id: 'a', name: 'Tiếng', kind: 'audio', locked: false, muted: false }], clips: [] }
}
const video = (doc: Document) => doc.clips.filter(c => c.track_id === 'v').sort((a,b) => a.start-b.start)

test('splits preserve linked AV source times and trim changes both together', () => {
  const original = op.insert(document(), assets[0], 0)
  const first = video(original)[0]
  const split = op.split(original, [first.id], 4)
  assert.equal(split.clips.length, 4)
  assert.equal(original.clips.length, 2)
  const right = video(split)[1]
  assert.equal(right.source_in, 4)
  assert.equal(right.duration, 6)
  assert.equal(op.members(split, [right.id]).length, 2)
  const trimmed = op.trim(split, right.id, 'left', 1)
  const parts = trimmed.clips.filter(c => c.group === right.group)
  assert.deepEqual(parts.map(c => [c.start, c.source_in, c.duration]), [[5, 5, 5], [5, 5, 5]])
  assert.throws(() => op.validate(op.trim(split, right.id, 'right', 1), assets), /vượt thời lượng/)
})

test('consecutive cuts keep the remaining piece selected and linked AV in sync', () => {
  const original = op.insert(document(), assets[0], 0)
  let doc = original, selection = [video(original)[0].id]
  for (const at of [2, 4, 6, 8]) {
    const result = op.splitSelection(doc, selection, at)
    doc = op.validate(result.document, assets); selection = result.selection
    const remaining = doc.clips.find(c => c.id === selection[0])!
    assert.equal(remaining.start, at)
    assert.equal(remaining.source_in, at)
    assert.equal(remaining.duration, 10 - at)
    assert.equal(op.members(doc, selection).length, 2)
    assert.deepEqual(doc.clips.filter(c => c.group === remaining.group).map(c => [c.start, c.source_in, c.duration]), [[at, at, 10 - at], [at, at, 10 - at]])
  }
  assert.deepEqual(video(doc).map(c => [c.start, c.source_in, c.duration]), [[0,0,2],[2,2,2],[4,4,2],[6,6,2],[8,8,2]])
  assert.equal(original.clips.length, 2)
  assert.throws(() => op.splitSelection(doc, selection, 8), /bên trong/)
  assert.throws(() => op.splitSelection(doc, selection, 10), /bên trong/)
  const locked = { ...doc, tracks: doc.tracks.map(t => t.id === 'a' ? { ...t, locked: true } : t) }
  assert.throws(() => op.splitSelection(locked, selection, 9), /đang khóa/)
})

test('split selection follows each selected track and retains pieces outside the cut', () => {
  let doc = op.insert(document(), assets[0], 0)
  doc = op.layer(doc, 'caption', 1)
  const ids = doc.clips.map(c => c.id)
  const first = op.splitSelection(doc, ids, 2)
  assert.ok(first.selection.every(id => first.document.clips.find(c => c.id === id)!.start === 2))
  const second = op.splitSelection(first.document, first.selection, 5)
  assert.equal(second.selection[2], first.selection[2]) // The caption ends before this cut.
  const picture = video(second.document).at(-1)!
  assert.ok(second.document.clips.filter(c => c.group === picture.group).every(c => second.selection.includes(c.id)))
  for (const c of second.document.clips.filter(c => c.anchor)) {
    const parent = second.document.clips.find(p => p.id === c.anchor)!
    assert.ok(parent.start <= c.start && op.end(parent) >= op.end(c))
  }
  op.validate(second.document, assets)
})

test('reorder swaps pictures with their sound; multi-selection move and duplicate retain links', () => {
  const original = op.insert(document(), assets[0], 0)
  const split = op.split(original, [video(original)[0].id], 4)
  const moved = op.reorder(split, video(split)[1].id, -1)
  assert.deepEqual(video(moved).map(c => [c.start,c.source_in,c.duration]), [[0,4,6],[6,0,4]])
  const dragged = op.dragReorder(split, [video(split)[1].id], 0)
  assert.deepEqual(video(dragged).map(c => [c.start,c.source_in,c.duration]), [[0,4,6],[6,0,4]])
  op.validate(moved, assets)
  const duplicated = op.duplicate(moved, [video(moved)[0].id])
  assert.equal(op.length(duplicated), 16)
  assert.equal(duplicated.clips.length, 6)
  assert.equal(op.members(duplicated, [video(duplicated)[2].id]).length, 2)
  assert.equal(op.length(op.move(duplicated, video(duplicated).map(c => c.id), 1)), 17)
})

test('ripple removes a global interval, trims crossing sound and closes the gap exactly', () => {
  let doc = op.insert(document(), assets[0], 0)
  doc = op.split(doc, [video(doc)[0].id], 4)
  doc = op.split(doc, [video(doc)[1].id], 6)
  const middle = video(doc)[1]
  const overlay: Clip = { id: 'overlay', asset_id: 'a', track_id: 'a', start: 2, source_in: 0, duration: 7, gain: .5, group: null }
  doc = { ...doc, clips: [...doc.clips, overlay] }
  const ordinary = op.remove(doc, [middle.id], false)
  assert.equal(op.length(ordinary), 10)
  assert.equal(video(ordinary)[1].start, 6)
  const ripple = op.remove(doc, [middle.id], true)
  assert.equal(op.length(ripple), 8)
  assert.equal(video(ripple)[1].start, 4)
  const overlayParts = ripple.clips.filter(c => !c.group)
  assert.deepEqual(overlayParts.map(c => [c.start,c.source_in,c.duration]), [[2,0,2],[4,4,3]])
  op.validate(ripple, assets)
})

test('locked layers refuse group edits, snap ignores selected members, overlap is rejected', () => {
  const original = op.insert(document(), assets[0], 0)
  const split = op.split(original, [video(original)[0].id], 4)
  const right = video(split)[1]
  assert.equal(op.snap(split, [right.id], 4.09, 6, 8, .15), 4)
  assert.throws(() => op.validate(op.move(split, [right.id], -1), assets), /chồng nhau/)
  const locked = { ...split, tracks: split.tracks.map(t => t.id === 'a' ? { ...t, locked: true } : t) }
  assert.throws(() => op.trim(locked, right.id, 'left', .2), /đang khóa/)
  assert.throws(() => op.remove(locked, [right.id], false), /đang khóa/)
  const unlinked = op.link(split, [right.id], true)
  assert.equal(op.members(unlinked, [right.id]).length, 1)
  assert.throws(() => op.link(split, video(split).map(c => c.id), false), /cùng mốc/)
})

test('frame quantization keeps many tiny cuts aligned and source duration bounded', () => {
  let doc = op.insert(document(), assets[0], 0)
  doc = op.split(doc, [video(doc)[0].id], 1.012)
  assert.equal(video(doc)[0].duration, 1)
  for (const c of doc.clips) for (const value of [c.start, c.source_in, c.duration]) assert.ok(Math.abs(value * 30 - Math.round(value * 30)) < .001)
  assert.throws(() => op.split(doc, [video(doc)[0].id], 0), /bên trong/)
  assert.throws(() => op.validate({ ...doc, clips: doc.clips.map(c => ({ ...c, start: 10799 })) }, assets), /180 phút/)
})
