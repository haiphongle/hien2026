import assert from 'node:assert/strict'
import test from 'node:test'
import * as op from '../src/components/projects/postproduction/timeline/operations.ts'
import { clipGain, clipSilences, envelopeGain } from '../src/components/projects/postproduction/timeline/audio.ts'
import type { Asset, Document } from '../src/components/projects/postproduction/timeline/types.ts'

const asset:Asset={id:'media',owner:'vid',ref:'original',name:'Source',kind:'video',category:'source',duration:10,width:160,height:96,fps:30,has_audio:true,url:'',visuals_ready:true}
function setup():Document {
  let d:Document={version:1,fps:30,width:160,height:96,tracks:[{id:'v',name:'Video',kind:'video',muted:false,locked:false},{id:'a',name:'Audio',kind:'audio',muted:false,locked:false}],clips:[]}
  d=op.insert(d,asset,0)
  d=op.layer(d,'caption',3)
  return d
}
const picture=(d:Document)=>d.clips.filter(c=>c.track_id==='v').sort((a,b)=>a.start-b.start)
const captions=(d:Document)=>d.clips.filter(c=>!c.asset_id)

test('attached captions follow reorder, trim, split, duplicate and reopen data',()=>{
  const original=setup(), id=picture(original)[0].id
  let d=op.split(original,[id],4)
  assert.deepEqual(captions(d).map(c=>[c.start,c.duration]),[[3,1],[4,2]])
  assert.equal(captions(d)[1].anchor,picture(d)[1].id)
  d=op.reorder(d,picture(d)[1].id,-1)
  assert.deepEqual(captions(d).map(c=>[c.start,c.duration]),[[9,1],[0,2]])
  op.validate(JSON.parse(JSON.stringify(d)),[asset])
  const right=picture(d)[1]
  d=op.trim(d,right.id,'right',-.5)
  assert.equal(captions(d)[0].duration,.5)
  const duplicated=op.duplicate(d,[picture(d)[0].id])
  const copied=picture(duplicated).at(-1)!
  assert.ok(captions(duplicated).some(c=>c.anchor===copied.id&&c.start===copied.start))
  assert.equal(captions(original)[0].duration,3)
})

test('global range cut closes all tracks and retains source timing and caption anchors',()=>{
  let d=setup()
  d=op.cutRange(d,4,5)
  assert.equal(op.length(d),9)
  assert.deepEqual(picture(d).map(c=>[c.start,c.source_in,c.duration]),[[0,0,4],[4,5,5]])
  assert.deepEqual(captions(d).map(c=>[c.start,c.duration]),[[3,1],[4,1]])
  assert.equal(captions(d)[1].anchor,picture(d)[1].id)
  op.validate(d,[asset])
  const kept=op.keepRange(d,2,7)
  assert.equal(op.length(kept),5)
  op.validate(kept,[asset])
  const locked={...d,tracks:d.tracks.map(t=>t.kind==='caption'?{...t,locked:true}:t)}
  assert.throws(()=>op.cutRange(locked,0,1),/đang khóa/)
})

test('speed keeps AV linked and rescales attached cue timing, gain automation survives slicing',()=>{
  const d=setup(),id=picture(d)[0].id,sped=op.speed(d,id,2)
  assert.equal(op.length(sped),5)
  assert.deepEqual(captions(sped).map(c=>[c.start,c.duration]),[[1.5,1.5]])
  assert.ok(sped.clips.filter(c=>c.asset_id).every(c=>c.speed===2&&c.duration===5))
  op.validate(sped,[asset])
  const audio={...d.clips.find(c=>c.track_id==='a')!,fade_in:2,fade_out:2}
  const cropped=op.slice(audio,1,9,30)
  assert.equal(envelopeGain(cropped,0),.5)
  assert.equal(envelopeGain(cropped,1),1)
  assert.equal(envelopeGain(cropped,8),.5)
  assert.equal(cropped.source_in,1)
})

test('extending a full scene to eight seconds retimes narration/captions and ripples later scenes',()=>{
  const image: Asset = {...asset,id:'image',has_audio:false,duration:5}
  const voice: Asset = {...asset,id:'voice',kind:'audio',category:'voice',width:0,height:0,duration:5}
  let d = op.insert({...setup(),clips:[]},image,0)
  d = op.insert(d,image,5)
  const first = picture(d)[0], second = picture(d)[1]
  d.tracks.push({id:'voice',name:'Voice',kind:'audio',role:'voice',muted:false,locked:false})
  d.clips.push({...first,id:'narration',asset_id:voice.id,track_id:'voice',duration:5,anchor:first.id},
    {...second,id:'next-voice',asset_id:voice.id,track_id:'voice',anchor:second.id})
  d = op.layer(d,'caption',1)
  const before = JSON.stringify(d), resized = op.validate(op.setDuration(d,first.id,8,[image,voice]),[image,voice])
  assert.equal(picture(resized)[0].duration,8)
  assert.equal(picture(resized)[0].speed,.625)
  assert.equal(picture(resized)[1].start,8)
  assert.equal(resized.clips.find(c=>c.id==='narration')!.duration,8)
  assert.equal(resized.clips.find(c=>c.id==='narration')!.speed,.625)
  assert.equal(resized.clips.find(c=>c.id==='next-voice')!.start,8)
  assert.deepEqual(captions(resized).map(c=>[c.start,c.duration]),[[1.6,4.8]])
  assert.equal(JSON.stringify(d),before)
  op.validate(JSON.parse(JSON.stringify(resized)),[image,voice])
})

test('duration fitting respects frame rounding, source offsets and existing speed',()=>{
  const image: Asset = {...asset,duration:5.933008,has_audio:false}
  const d = {...setup(),clips:[{...picture(setup())[0],duration:178/30,group:null}]}
  const first = picture(d)[0], resized = op.validate(op.setDuration(d,first.id,8,[image]),[image])
  assert.equal(picture(resized)[0].duration,8)
  assert.ok(Math.abs(picture(resized)[0].speed-178/240)<1e-10)
  const trimmed = {...d,clips:[{...first,source_in:2,duration:4,speed:.75}]}
  const slower = op.validate(op.setDuration(trimmed,first.id,6,[{...image,duration:5}]),[{...image,duration:5}])
  assert.equal(picture(slower)[0].source_in,2)
  assert.equal(picture(slower)[0].duration,6)
  assert.equal(picture(slower)[0].speed,.5)
  assert.throws(()=>op.setDuration(d,first.id,20,[image]),/tối đa/)
  for(const value of [0,NaN,Infinity])assert.throws(()=>op.setDuration(d,first.id,value,[image]),/khung hình/)
})

test('duration edits with available source still trim; audio never reads beyond the source',()=>{
  const d = setup(), first = picture(d)[0]
  const shortened = op.setDuration(d,first.id,5,[asset])
  assert.equal(picture(shortened)[0].duration,5)
  assert.equal(picture(shortened)[0].speed??1,1)
  const extended = op.validate(op.setDuration(shortened,first.id,8,[asset]),[asset])
  assert.equal(picture(extended)[0].duration,8)
  assert.equal(picture(extended)[0].speed??1,1)
  const sound = d.clips.find(c=>c.track_id==='a')!
  assert.throws(()=>op.validate(op.setDuration(d,sound.id,11,[asset]),[asset]),/vượt thời lượng/)
  const locked = {...d,tracks:d.tracks.map(t=>t.id==='v'?{...t,locked:true}:t)}
  assert.throws(()=>op.setDuration(locked,first.id,16,[asset]),/đang khóa/)
  const slowVoice = {...d,clips:[...d.clips,{...sound,id:'voice',group:null,anchor:first.id,speed:.5,duration:5}]}
  assert.throws(()=>op.setDuration(slowVoice,first.id,16,[asset]),/đoạn liên quan vượt tốc độ/)
})

test('duration edits ripple adjacent scenes at either source boundary, including attached voices and captions',()=>{
  const source = {...asset,duration:5.933333333333334,has_audio:false}
  const first = {id:'first',asset_id:source.id,track_id:'v',start:0,source_in:0,duration:5.5,speed:.7416666666666667,gain:1,group:null}
  const second = {...first,id:'second',start:5.5,duration:2.5,speed:.7,transition:{kind:'dissolve' as const,duration:.4}}
  const base = setup()
  const d: Document = {...base,clips:[first,second,
    {...second,id:'voice',track_id:'a',asset_id:source.id,anchor:second.id,transition:null},
    {...second,id:'cue',track_id:base.tracks.find(t=>t.kind==='caption')!.id,asset_id:null,anchor:second.id,text:'Tiếp theo',speed:1,transition:null},
  ]}
  const before = JSON.stringify(d)
  for (const duration of [5,6,8,8.1]) {
    const resized = op.validate(op.setDuration(d,first.id,duration,[source]),[source])
    assert.equal(picture(resized)[0].duration,duration)
    assert.equal(picture(resized)[1].start,duration)
    assert.equal(resized.clips.find(c=>c.id==='voice')!.start,duration)
    assert.equal(resized.clips.find(c=>c.id==='cue')!.start,duration)
    assert.deepEqual(picture(resized)[1].transition,second.transition)
    op.validate(JSON.parse(JSON.stringify(resized)),[source])
  }
  assert.equal(JSON.stringify(d),before)
  assert.equal(op.setDuration(d,first.id,5.5,[source]),d)
  const locked = {...d,tracks:d.tracks.map(t=>t.id==='a'?{...t,locked:true}:t)}
  assert.throws(()=>op.setDuration(locked,first.id,6,[source]),/đang khóa/)
})

test('dragged picture edges stop at neighbors and resize linked layers together',()=>{
  const original = setup(), before = JSON.stringify(original)
  let d = op.split(original,[picture(original)[0].id],4)
  const [first,second] = picture(d)
  const right = op.validate(op.trim(d,first.id,'right',2),[asset])
  assert.equal(picture(right)[0].duration,4)
  assert.ok(right.clips.filter(c=>c.group===first.group).every(c=>c.duration===4))
  const left = op.validate(op.trim(d,second.id,'left',-2),[asset])
  assert.ok(left.clips.filter(c=>c.group===second.group).every(c=>c.start===4&&c.source_in===4))
  d = op.trim(d,first.id,'right',-.5)
  const gap = op.validate(op.trim(d,second.id,'left',-2),[asset])
  assert.ok(gap.clips.filter(c=>c.group===second.group).every(c=>c.start===3.5&&c.source_in===3.5&&c.duration===6.5))
  assert.equal(JSON.stringify(original),before)
})

test('music ducking honors solo/mute and silence regions map through source cuts and speed',()=>{
  const d=setup(),music={...d.clips[1],id:'music',start:0,duration:10,track_id:'music'}
  d.tracks.push({id:'music',name:'Music',kind:'audio',role:'music',muted:false,locked:false},{id:'voice',name:'Voice',kind:'audio',role:'voice',muted:false,locked:false})
  d.clips.push(music,{...d.clips[1],id:'voice',track_id:'voice',start:3,duration:2})
  d.ducking={enabled:true,amount:.25,attack:.2,release:.4}
  assert.equal(clipGain(d,music,4),.25)
  assert.equal(clipGain(d,music,1),1)
  assert.ok(Math.abs(clipGain(d,music,5.2)-.625)<1e-6)
  const muted={...d,tracks:d.tracks.map(t=>t.id==='voice'?{...t,muted:true}:t)}
  assert.equal(clipGain(muted,music,4),1)
  const ranges=clipSilences({...music,start:10,source_in:1,duration:2,speed:2},{peaks:[...Array(10).fill(.2),...Array(10).fill(0),...Array(20).fill(.2)],interval:.1,duration:4,thumbnail_count:0})
  assert.deepEqual(ranges,[{start:10,end:10.5}])
})
