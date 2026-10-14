import assert from 'node:assert/strict'
import test from 'node:test'
import { dragFrame, frameGeometry } from '../src/components/projects/postproduction/editingTypes.ts'
import type { FrameSettings } from '../src/components/projects/postproduction/editingTypes.ts'

const source = { width: 576, height: 576, duration: 318 }
const frame: FrameSettings = { preset: '9:16', width: 1080, height: 1920, fit: 'contain', background: 'color', color: '#101827', zoom: 1, x: .5, y: .5 }

test('square source letterboxes in portrait and landscape with output-specific offsets', () => {
  const portrait = frameGeometry(frame, source)!
  assert.deepEqual([portrait.width, portrait.height, portrait.dw, portrait.dh, portrait.dx, portrait.dy], [1080, 1920, 1080, 1080, 0, 420])
  const landscape = frameGeometry({ ...frame, preset: '16:9' }, source)!
  assert.deepEqual([landscape.width, landscape.height, landscape.dw, landscape.dh, landscape.dx, landscape.dy], [1920, 1080, 1080, 1080, 420, 0])
  assert.deepEqual([portrait.sx, portrait.sy, portrait.sourceWidth, portrait.sourceHeight], [0, 0, 576, 576])
})

test('cover and zoom crop the source instead of stretching it; position follows the export', () => {
  const center = frameGeometry({ ...frame, fit: 'cover' }, source)!
  assert.equal(center.dw, 1080); assert.equal(center.dh, 1920)
  assert.equal(center.sx, 126); assert.equal(center.sourceWidth, 324)
  const right = frameGeometry({ ...frame, fit: 'cover', x: 1 }, source)!
  assert.equal(right.sx, 252)
  const zoom = frameGeometry({ ...frame, preset: 'source', zoom: 2 }, source)!
  assert.deepEqual([zoom.sourceWidth, zoom.sourceHeight, zoom.sx, zoom.sy], [288, 288, 144, 144])
})

test('dragging shifts letterboxed footage and crops in opposite directions, bounded to the frame', () => {
  const moved = dragFrame(frame, source, 0, .1)
  assert.ok(frameGeometry(moved, source)!.dy > frameGeometry(frame, source)!.dy)
  const covered = { ...frame, fit: 'cover' as const }
  const panned = dragFrame(covered, source, .1, 0)
  assert.ok(frameGeometry(panned, source)!.sx < frameGeometry(covered, source)!.sx)
  assert.equal(dragFrame(frame, source, 0, 10).y, 1)
})

test('incomplete or oversized custom sizes do not allocate a canvas', () => {
  assert.equal(frameGeometry(frame, null), null)
  for (const width of [0, NaN, Infinity, 10000]) assert.equal(frameGeometry({ ...frame, preset: 'custom', width }, source), null)
  assert.equal(frameGeometry({ ...frame, preset: 'custom', width: 3840, height: 3840 }, source), null)
})
