import assert from 'node:assert/strict'
import test from 'node:test'
import { setTransition, transitionAt, transitionJoins, transitionWeights } from '../src/components/projects/postproduction/timeline/transitions.ts'
import * as op from '../src/components/projects/postproduction/timeline/operations.ts'
import type { Asset, Document } from '../src/components/projects/postproduction/timeline/types.ts'

const assets: Asset[] = [{ id: 'source', owner: 'v', ref: '', name: '', kind: 'video', category: 'source', duration: 10, width: 160, height: 96, fps: 30, has_audio: false, url: '', visuals_ready: false }]
function fixture(): Document {
  return { version: 1, fps: 30, width: 160, height: 96, tracks: [
    { id: 'v', name: 'Hình', kind: 'video', locked: false, muted: false },
    { id: 'a', name: 'Giọng', kind: 'audio', locked: false, muted: false },
  ], clips: ['first', 'second', 'third'].map((id, i) => ({ id, asset_id: 'source', track_id: 'v', start: i*2, source_in: 0, duration: 2, gain: 1, group: null })) }
}

test('apply-all only changes joins; durations, source offsets and sound stay identical after reload', () => {
  const original = fixture()
  original.clips.push({ ...original.clips[0], id: 'voice', track_id: 'a', duration: 6 })
  const next = setTransition(original, 'second', { kind: 'dissolve', duration: .4 }, true)
  assert.equal(next.clips[0].transition, undefined)
  assert.equal(next.clips[3], original.clips[3])
  assert.equal(op.length(next), op.length(original))
  for (let i=0; i<3; i++) assert.deepEqual([next.clips[i].start, next.clips[i].duration, next.clips[i].source_in], [original.clips[i].start, original.clips[i].duration, original.clips[i].source_in])
  assert.equal(transitionJoins(JSON.parse(JSON.stringify(next))).filter(j => j.next.transition).length, 2)
  assert.equal(original.clips[1].transition, undefined)
  const removed = setTransition(next, 'second', null, true)
  assert.ok(removed.clips.every(c => !c.transition))
})

test('remove one or all transitions without deleting media, including effects on separated clips', () => {
  const doc = setTransition(fixture(), 'second', { kind:'slide',duration:.7 },true)
  doc.clips.push({...doc.clips[0],id:'voice',track_id:'a',duration:6})
  const before = JSON.stringify(doc)
  const one = setTransition(doc,'second',null)
  assert.equal(one.clips[1].transition,null)
  assert.deepEqual(one.clips[2].transition,doc.clips[2].transition)
  for (const next of [one,setTransition(doc,null,null,true)]) {
    assert.deepEqual(next.clips.map(c=>({...c,transition:null})),doc.clips.map(c=>({...c,transition:null})))
  }
  const gaps = {...doc,clips:doc.clips.map((c,i)=>({...c,start:c.start+i}))}
  assert.equal(transitionJoins(gaps).length,0)
  const cleared = setTransition(gaps,null,null,true)
  assert.ok(cleared.clips.every(c=>!c.transition))
  assert.equal(cleared.clips.length,gaps.clips.length)
  assert.equal(JSON.stringify(doc),before)
  const locked = {...gaps,tracks:gaps.tracks.map(t=>t.kind==='video'?{...t,locked:true}:t)}
  assert.throws(()=>setTransition(locked,null,null,true),/khóa/)
})

test('seek/preload respect effect bounds, gaps, muted picture and short clips', () => {
  const doc = setTransition(fixture(), 'second', { kind: 'black', duration: .4 })
  assert.equal(transitionAt(doc, 2)!.next.id, 'second')
  assert.equal(transitionAt(doc, 1, 8)!.next.id, 'second')
  assert.equal(transitionAt(doc, 2.4), undefined)
  doc.clips[1].duration = .2
  assert.equal(transitionJoins(doc)[0].duration, .2)
  assert.equal(transitionAt(doc, 2.2), undefined)
  doc.tracks[0].muted = true
  assert.equal(transitionAt(doc, 2), undefined)
  doc.tracks[0].muted = false
  doc.clips[1].start = 2.1
  assert.equal(transitionAt(doc, 2.1), undefined)
})

test('splitting or cropping clears the new cut effect; locks and invalid durations are rejected', () => {
  const doc = setTransition(fixture(), 'second', { kind: 'slide', duration: .4 })
  const split = op.splitSelection(doc, ['second'], 3).document
  assert.equal(split.clips.find(c => c.id === 'second')!.transition!.kind, 'slide')
  assert.equal(split.clips.find(c => c.start === 3)!.transition, null)
  const cropped = op.trim(doc, 'second', 'left', .2)
  assert.equal(cropped.clips.find(c => c.id === 'second')!.transition, null)
  for (const duration of [0, .09, 2.1, NaN, Infinity]) assert.throws(() => setTransition(doc, 'second', { kind: 'light', duration }), /0.1/)
  assert.throws(() => setTransition(doc, 'first', null), /chỗ nối/)
  doc.tracks[0].locked = true
  assert.throws(() => setTransition(doc, 'second', null), /khóa/)
  op.validate(split, assets)
})

test('fade weights match FFmpeg and conserve luminance at endpoints and throughout the join', () => {
  for (const kind of ['dissolve', 'blur', 'black', 'light'] as const) {
    assert.deepEqual(transitionWeights(kind, 0), { old: 1, next: 0, background: 0 })
    assert.deepEqual(transitionWeights(kind, 1), { old: 0, next: 1, background: 0 })
    for (let i=0;i<=100;i++) {
      const w = transitionWeights(kind, i/100)
      assert.ok(Math.abs(w.old+w.next+w.background-1)<1e-10)
      assert.ok(Object.values(w).every(v => v>=0 && v<=1))
    }
  }
  assert.ok(transitionWeights('black', .5).background > .5)
})
