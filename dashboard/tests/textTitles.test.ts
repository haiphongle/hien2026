import assert from 'node:assert/strict'
import test from 'node:test'
import * as op from '../src/components/projects/postproduction/timeline/operations.ts'
import { readHistory, writeHistory } from '../src/components/projects/postproduction/timeline/history.ts'
import { moveText, resizeText } from '../src/components/projects/postproduction/timeline/textTitles.ts'
import type { Document } from '../src/components/projects/postproduction/timeline/types.ts'

function base(): Document {
  return { version: 1, fps: 30, width: 1280, height: 720, clips: [], tracks: [
    { id: 'video', name: 'Hình', kind: 'video', locked: false, muted: false },
    { id: 'audio', name: 'Tiếng', kind: 'audio', locked: false, muted: false },
  ] }
}

test('titles are independently editable; clearing captions keeps titles and line breaks', () => {
  const original = op.layer(op.layer(base(), 'text', 0, 'author'), 'caption', 0)
  const title = original.clips[0], caption = original.clips[1]
  assert.notEqual(title.track_id, caption.track_id)
  assert.ok(title.text!.includes('\n'))
  const next = op.clearTrack(original, caption.track_id)
  assert.deepEqual(next.clips, [title])
  assert.equal(title.duration, 3)
  op.validate(JSON.parse(JSON.stringify(next)), [])
  const locked = { ...next, tracks: next.tracks.map(t => t.kind === 'text' ? { ...t, locked: true } : t) }
  assert.throws(() => op.patch(locked, title.id, { text: 'New' }), /khóa/)
  assert.throws(() => op.layer(locked, 'text', 1), /khóa/)
})

test('title history survives reopening and preserves an empty title style', () => {
  const first = op.layer(base(), 'text', 0, 'film')
  const id = first.clips[0].id
  const empty = op.patch(first, id, { text: '' })
  const next = op.patch(empty, id, { caption: { ...empty.clips[0].caption!, size: 15 } })
  let serialized = ''
  const storage = { getItem: () => serialized, setItem: (_k: string, value: string) => { serialized = value } }
  writeHistory(storage, 'vid', next, { undo: [first, empty], redo: [] })
  assert.deepEqual(readHistory(storage, 'vid', JSON.parse(JSON.stringify(next))).undo, [first, empty])
  assert.deepEqual(readHistory(storage, 'vid', empty).undo, [])
  op.validate(next, [])
})

test('drag and resize stay bounded, including a title clamped against the edge', () => {
  const bounds = { x: 0, y: .1, width: .4, height: .2 }
  assert.deepEqual(moveText(bounds, -.5, 2), { x: .2, y: .9 })
  assert.deepEqual(moveText(bounds, .1, 0), { x: .30000000000000004, y: .2 })
  assert.equal(resizeText(10, 200, 100, 100, 50, 30), 20)
  assert.equal(resizeText(10, 200, 100, -1000, -1000, 30), 1)
  assert.equal(resizeText(10, 200, 100, 1000, 1000, 30), 30)
})

test('text split and trim preserve styling and do not mutate the starting draft', () => {
  const first = op.layer(base(), 'text', 0, 'advert'), id = first.clips[0].id
  const next = op.split(first, [id], 1)
  assert.deepEqual(next.clips.map(c => [c.start, c.duration]), [[0, 1], [1, 2]])
  assert.ok(next.clips.every(c => c.text === first.clips[0].text && c.caption?.color === first.clips[0].caption?.color))
  assert.equal(first.clips.length, 1)
  assert.throws(() => op.layer(first, 'text', 3600), /60 phút/)
})

test('portrait titles start at a readable size relative to the output width', () => {
  const d = base()
  d.frame = {preset:'9:16',width:1080,height:1920,fit:'contain',background:'blur',color:'#000000',zoom:1,x:.5,y:.5}
  assert.equal(op.layer(d, 'text', 0, 'film').clips[0].caption?.size, 5.1)
})
