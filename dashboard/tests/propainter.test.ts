import test from 'node:test'
import assert from 'node:assert/strict'
import { maskFromDrag, maskProblem, trialDraft, trialLimit, type RestorationResult } from '../src/components/projects/postproduction/propainter.ts'
import { validate, length } from '../src/components/projects/postproduction/timeline/operations.ts'
import type { Asset, Document } from '../src/components/projects/postproduction/timeline/types.ts'
import { applyRestorations, replaceRestorations } from '../src/components/projects/postproduction/timeline/restoreResult.ts'

const source = { width: 1920, height: 1080, duration: 20, fps_number: 30, fingerprint: 'sample' }
test('legacy trials respect 60 frames at 60, 30 and fractional FPS', () => {
  assert.equal(trialLimit({ ...source, fps_number: 60 }), 1)
  assert.equal(trialLimit(source), 2)
  assert.equal(trialLimit({ ...source, fps_number: 20 }), 3)
  assert.ok(trialLimit({ ...source, fps_number: 30000 / 1001 }) * 30000 / 1001 <= 60)
  assert.equal(trialLimit({ ...source, duration: .8 }), .8)
  assert.deepEqual(trialDraft(source, { start: 200, duration: 3, dilation: 20, box: { x: .9, y: 0, width: .2, height: .2 } }), { start: 18, duration: 2, dilation: 8, maxSide: 192, box: null })
})
test('mask clamps movement and rejects unsafe bounds', () => {
  const box = maskFromDrag({ x: .8, y: .6 }, { x: .2, y: .4 })
  assert.ok(Math.abs(box.width - .6) < 1e-10)
  assert.equal(maskProblem(box), '')
  assert.deepEqual(maskFromDrag({ x: .1, y: .1 }, { x: -5, y: 2 }), { x: 0, y: .1, width: .1, height: .9 })
  assert.ok(maskProblem({ x: 0, y: 0, width: .8, height: .8 }).includes('35%'))
  assert.ok(maskProblem({ x: NaN, y: 0, width: .2, height: .2 }))
})
function fixture() {
  const original: Asset = { id: 'original', owner: 'video', ref: 'original', name: 'Source', kind: 'video', category: 'source', duration: 30, width: 1920, height: 1080, fps: 30, has_audio: true, url: '/source', visuals_ready: false }
  const repaired: Asset = { ...original, id: 'repair', ref: 'propainter:result', category: 'export', duration: 8 }
  const base = { asset_id: original.id, start: 2, duration: 4, source_in: 6, speed: 2, gain: .4, group: 'av' }
  const document: Document = { version: 1, fps: 30, width: 1920, height: 1080, tracks: [
    { id: 'v', kind: 'video', name: 'Video', locked: false, muted: false },
    { id: 'a', kind: 'audio', name: 'Original', locked: false, muted: false },
    { id: 'c', kind: 'caption', name: 'Captions', locked: false, muted: false },
    { id: 'voice', kind: 'audio', role: 'voice', name: 'Voice', locked: false, muted: false },
  ], clips: [
    { ...base, id: 'picture', track_id: 'v', transition: { kind: 'dissolve', duration: .4 } },
    { ...base, id: 'sound', track_id: 'a', gain: .2, fade_in: .1 },
    { ...base, id: 'caption', track_id: 'c', group: null, asset_id: null, text: 'Keep this subtitle', anchor: 'picture' },
    { ...base, id: 'narrator', track_id: 'voice', group: null, source_in: 0, speed: 1, anchor: 'picture' },
  ] }
  const result: RestorationResult = { trial_id: 'result', url: '/file', start: 6, created_at: 1, width: 1920, height: 1080, duration: 8, inference_seconds: 0, device: 'test', peak_mps_driver_bytes: 0, peak_rss_bytes: 0, full_resolution: true,
    targets: [{ clip_id: 'picture', asset_id: original.id, source_in: 6, duration: 4, speed: 2 }] }
  return { document, assets: [original, repaired], result }
}
test('in-place replacement keeps duration, edits, linked sound, narration and captions; restores exactly', () => {
  const { document, assets, result } = fixture(), before = structuredClone(document)
  const after = replaceRestorations(document, assets, [result])
  assert.equal(after.clips.length, before.clips.length)
  assert.equal(length(after), length(before))
  for (let i=0; i<2; i++) assert.deepEqual(after.clips[i], { ...before.clips[i], asset_id: 'repair', source_in: 0 })
  assert.deepEqual(after.clips.slice(2), before.clips.slice(2))
  assert.deepEqual(document, before)
  assert.equal(replaceRestorations(after, assets, [result]), after)
  assert.deepEqual(replaceRestorations(after, assets, [result], true), document)
  validate(after, assets)
})
test('changed trims, speeds, missing media, locked linked tracks and old trials are rejected', () => {
  const { document, assets, result } = fixture()
  assert.throws(() => replaceRestorations({ ...document, clips: document.clips.map(c => c.id === 'picture' ? { ...c, duration: 3 } : c) }, assets, [result]), /cắt hoặc đổi tốc độ/)
  assert.throws(() => replaceRestorations({ ...document, clips: document.clips.map(c => c.id === 'picture' ? { ...c, speed: 1 } : c) }, assets, [result]), /cắt hoặc đổi tốc độ/)
  assert.throws(() => replaceRestorations(document, assets.slice(0, 1), [result]), /Không đọc được/)
  assert.throws(() => replaceRestorations({ ...document, tracks: document.tracks.map(t => t.id === 'a' ? { ...t, locked: true } : t) }, assets, [result]), /Mở khóa/)
  assert.throws(() => replaceRestorations(document, assets, [{ ...result, full_resolution: false }]), /đoạn thử cũ/)
  assert.throws(() => replaceRestorations(document, assets.map(a => a.id === 'repair' ? { ...a, has_audio: false } : a), [result]), /thiếu tiếng/)
})
test('failed save retry does not create duplicate clips or undo steps', async () => {
  const f = fixture(); let document = f.document, saves = 0, edits = 0, selected = 0
  const editor = { refresh: async () => ({ document, revision: 1, assets: f.assets, warnings: [], source_available: true }),
    getDocument: () => document, apply: (next: Document) => { document = next; edits++ },
    save: async () => { if (++saves === 1) throw new Error('API 502'); return saves } }
  await assert.rejects(applyRestorations(editor, [f.result], false, () => { selected++ }), /API 502/)
  const applied = structuredClone(document)
  await applyRestorations(editor, [f.result], false, () => { selected++ })
  assert.deepEqual(document, applied)
  assert.equal(edits, 1)
  assert.equal(selected, 1)
})
test('batch replacement is atomic if one result is stale', () => {
  const { document, assets, result } = fixture(), before = structuredClone(document)
  assert.throws(() => replaceRestorations(document, assets, [result, { ...result, targets: [{ ...result.targets![0], speed: 1 }] }]), /cắt hoặc đổi tốc độ/)
  assert.deepEqual(document, before)
})
