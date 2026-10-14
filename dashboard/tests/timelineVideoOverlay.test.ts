import assert from 'node:assert/strict'
import test from 'node:test'
import * as op from '../src/components/projects/postproduction/timeline/operations.ts'
import { applyReviewPreset, entranceOpacity, insertOverlay, linkedAudio, mainPaneWidth, overlayGeometry, DEFAULT_OVERLAY, moveOverlay, resizeOverlay, overlayPreset } from '../src/components/projects/postproduction/timeline/videoOverlay.ts'
import { DEFAULT_FRAME, frameGeometry } from '../src/components/projects/postproduction/editingTypes.ts'
import { clipGain, voiceIntervals } from '../src/components/projects/postproduction/timeline/audio.ts'
import { nextPreparation } from '../src/components/projects/postproduction/timeline/preparation.ts'
import { readHistory, writeHistory } from '../src/components/projects/postproduction/timeline/history.ts'
import type { Asset, Document } from '../src/components/projects/postproduction/timeline/types.ts'

const asset: Asset = {id:'source',owner:'v',ref:'original',name:'Movie',kind:'video',category:'source',duration:12,width:1280,height:720,fps:30,has_audio:true,url:'movie.mp4',visuals_ready:false}
const narrator: Asset = {...asset,id:'narrator',duration:4,width:720,height:1280}
function base(): Document {
  return op.insert({version:1,fps:30,width:1280,height:720,tracks:[{id:'video',name:'Video',kind:'video',locked:false,muted:false},{id:'audio',name:'Audio',kind:'audio',locked:false,muted:false}],clips:[]},asset,0)
}
test('insert creates separate linked video/audio, leaves the main edit intact and bounds duration',()=>{
  const before=base(), doc=insertOverlay(before,narrator,10), pip=doc.clips.find(c=>c.overlay)!, voice=linkedAudio(doc,pip)!
  op.validate(doc,[asset,narrator])
  assert.deepEqual(doc.clips.slice(0,2),before.clips)
  assert.equal(pip.duration,2);assert.equal(voice.duration,2);assert.equal(voice.group,pip.group)
  assert.notEqual(voice.track_id,'audio');assert.equal(pip.overlay!.width/pip.overlay!.height,720/1280/(1280/720))
  assert.throws(()=>insertOverlay(doc,narrator,11),/khoảng trống/)
  const silent=insertOverlay(before,{...narrator,has_audio:false},0)
  assert.equal(silent.clips.length,3)
})
test('trim, split, move, speed, remove retain PiP audio sync and never ripple the main movie',()=>{
  let doc=insertOverlay(base(),narrator,2), pip=doc.clips.find(c=>c.overlay)!
  const main=doc.clips.slice(0,2)
  doc=op.trim(doc,pip.id,'left',.5)
  doc=op.move(doc,[pip.id],1)
  doc=op.speed(doc,pip.id,2)
  pip=doc.clips.find(c=>c.id===pip.id)!
  assert.deepEqual([pip.start,pip.source_in,pip.duration],[3.5,.5,1.7333333333333334])
  doc=op.split(doc,[pip.id],4)
  const pictures=doc.clips.filter(c=>c.overlay)
  for(const c of pictures){const a=linkedAudio(doc,c)!;assert.deepEqual([a.start,a.source_in,a.duration,a.speed],[c.start,c.source_in,c.duration,c.speed]);assert.deepEqual(c.overlay,pip.overlay)}
  assert.deepEqual(doc.clips.slice(0,2),main)
  op.validate(doc,[asset,narrator])
  doc=op.remove(doc,pictures.map(c=>c.id),false)
  assert.deepEqual(doc.clips,main)
})
test('position/resize stay inside frame, presets leave split mode, locks and overlap are enforced',()=>{
  const moved=moveOverlay(DEFAULT_OVERLAY,8,-2)
  assert.equal(moved.x,1-moved.width);assert.equal(moved.y,0)
  for(const corner of ['nw','ne','sw','se']){
    const resized=resizeOverlay(DEFAULT_OVERLAY,corner,10,10)
    assert.ok(resized.x>=0&&resized.y>=0&&resized.x+resized.width<=1.000001&&resized.y+resized.height<=1.000001)
    assert.ok(Math.abs(resized.width/resized.height-DEFAULT_OVERLAY.width/DEFAULT_OVERLAY.height)<1e-6)
  }
  assert.equal(overlayPreset(overlayPreset(DEFAULT_OVERLAY,'split'),'top-left').layout,'free')
  const doc=insertOverlay(base(),narrator,2), pip=doc.clips.find(c=>c.overlay)!, voice=linkedAudio(doc,pip)!
  const locked={...doc,tracks:doc.tracks.map(t=>({...t,locked:t.id===voice.track_id}))}
  assert.throws(()=>op.trim(locked,pip.id,'left',1),/khóa/)
  const duplicate={...doc,clips:[...doc.clips,{...pip,id:'overlap',group:null,start:3}]}
  assert.throws(()=>op.validate(duplicate,[asset,narrator]),/chồng thời gian/)
})
test('PiP style and independent clip mute survive saved history normalization',()=>{
  const before=base(),doc=insertOverlay(before,narrator,2),pip=doc.clips.find(c=>c.overlay)!,voice=linkedAudio(doc,pip)!
  const muted=op.patch(doc,voice.id,{muted:true})
  let data='';const storage={getItem:()=>data,setItem:(_key:string,value:string)=>{data=value}}
  writeHistory(storage,'v',muted,{undo:[before,doc],redo:[]})
  const restored=readHistory(storage,'v',{...muted,clips:muted.clips.map(c=>({muted:false,overlay:null,...c}))})
  assert.equal(restored.undo.length,2);assert.deepEqual(restored.undo[1],doc)
  assert.equal(readHistory(storage,'v',doc).undo.length,0)
})

test('portrait main geometry matches exported split pane, including even pixel boundaries',()=>{
  const pane=mainPaneWidth(640)
  const geometry=frameGeometry({...DEFAULT_FRAME,preset:'custom',width:pane,height:360},{width:96,height:160,duration:4},2)!
  assert.deepEqual([geometry.dx,geometry.dy,geometry.dw,geometry.dh],[52,0,216,360])
  assert.equal(mainPaneWidth(642),320)
  assert.deepEqual(overlayGeometry({...DEFAULT_OVERLAY,layout:'split'},642,360),{x:320,y:0,width:322,height:360})
})

test('ultrawide and narrow sources always insert a valid frame in vertical and landscape outputs',()=>{
  for(const [width,height] of [[7680,1080],[96,2160],[1920,1080],[720,1280]]){
    for(const preset of ['9:16','16:9'] as const){
      const source={...narrator,width,height}
      const doc=insertOverlay({...base(),frame:{...DEFAULT_FRAME,preset}},source,0)
      op.validate(doc,[asset,source])
      const o=doc.clips.find(c=>c.overlay)!.overlay!
      assert.ok(o.width>=.05&&o.height>=.05&&o.x+o.width<=1&&o.y+o.height<=1)
    }
  }
})

test('review presets are undoable, keep edits and set music/original ducking without changing voice gain',()=>{
  const doc=insertOverlay(base(),narrator,2), pip=doc.clips.find(c=>c.overlay)!, before=structuredClone(doc)
  let next=applyReviewPreset(doc,pip.id,'corner')
  assert.equal(next.clips.find(c=>c.id===pip.id)!.overlay!.shape,'rounded')
  assert.equal(next.ducking!.original_amount,.2);assert.equal(next.ducking!.detect_pauses,true)
  assert.deepEqual(linkedAudio(next,pip),linkedAudio(doc,pip));assert.deepEqual(doc,before)
  next=applyReviewPreset(next,pip.id,'voice');assert.ok(next.clips.find(c=>c.id===pip.id)!.overlay!.hidden)
  next=applyReviewPreset(next,pip.id,'split');assert.equal(next.clips.find(c=>c.id===pip.id)!.overlay!.hidden,false)
  const saved={...next,clips:next.clips.map(c=>c.overlay?{...c,overlay:{radius:12,entrance:0,entrance_offset:0,...c.overlay}}:c)}
  let data='';const storage={getItem:()=>data,setItem:(_key:string,value:string)=>{data=value}}
  writeHistory(storage,'review',next,{undo:[doc],redo:[]})
  assert.equal(readHistory(storage,'review',saved).undo.length,1)
})

test('entrance remains continuous after trimming, splitting, and moving a retained piece',()=>{
  let doc=insertOverlay(base(),narrator,2), pip=doc.clips.find(c=>c.overlay)!
  doc=op.patch(doc,pip.id,{overlay:{...pip.overlay!,entrance:.8}})
  doc=op.split(doc,[pip.id],2.4)
  pip=doc.clips.find(c=>c.overlay&&c.start===2.4)!
  assert.ok(Math.abs(entranceOpacity(pip,2.4)-.5)<1e-8)
  doc=op.move(doc,[pip.id],4);pip=doc.clips.find(c=>c.id===pip.id)!
  assert.ok(Math.abs(entranceOpacity(pip,pip.start)-.5)<1e-8)
  assert.equal(entranceOpacity(pip,pip.start+1),1)
})

test('waveform pauses drive both original and music gains through trimmed and sped up narration',()=>{
  const doc=insertOverlay(base(),narrator,2), pip=doc.clips.find(c=>c.overlay)!, voice=linkedAudio(doc,pip)!
  doc.ducking={enabled:true,amount:.25,original_amount:.2,attack:.1,release:.1,detect_pauses:true}
  const visuals={[narrator.id]:{peaks:[],interval:.032,duration:4,thumbnail_count:0,silences:[{start:1,end:2}]}}
  const intervals=voiceIntervals(doc,visuals)
  assert.deepEqual(intervals,[[2,3],[4,6]])
  assert.ok(Math.abs(clipGain(doc,doc.clips[1],2.5,intervals)-.2)<1e-8)
  assert.equal(clipGain(doc,doc.clips[1],3.5,intervals),1)
  const adjusted={...doc,clips:doc.clips.map(c=>c.id===voice.id?{...c,source_in:1,speed:2,duration:1.5}:c)}
  assert.deepEqual(voiceIntervals(adjusted,visuals),[[2.5,3.5]])
  voice.muted=true;assert.deepEqual(voiceIntervals(doc,visuals),[])
})

test('background preparation includes long inserted videos, reuses cache, and never retries a cancelled job automatically',()=>{
  const long={...narrator,owner:'another-video',duration:1800,visuals_ready:true}
  const doc=insertOverlay(base(),long,0), assets=[{...asset,visuals_ready:true},long]
  assert.deepEqual(nextPreparation(doc,assets,new Set(),new Set()),{kind:'proxies',ids:[long.id]})
  assert.equal(nextPreparation(doc,assets,new Set(),new Set([long.id])),null)
  assert.equal(nextPreparation(doc,assets.map(a=>({...a,proxy_url:'cached.mp4'})),new Set(),new Set()),null)
  assert.equal(nextPreparation(doc,assets.map(a=>a.id===long.id?{...a,missing:true}:a),new Set(),new Set()),null)
})

test('pause analysis batches used voices within the API limit and excludes unused library assets',()=>{
  const doc=insertOverlay(base(),narrator,0), voice=linkedAudio(doc,doc.clips.find(c=>c.overlay)!)!
  doc.ducking={enabled:true,amount:.25,attack:.2,release:.35,detect_pauses:true}
  const voices=Array.from({length:12},(_,i)=>({...narrator,id:`voice-${i}`,kind:'audio' as const}))
  doc.clips.push(...voices.map((a,i)=>({...voice,id:`clip-${i}`,asset_id:a.id,start:i*4})))
  const assets=[{...asset,visuals_ready:true},{...narrator,visuals_ready:true},...voices,{...narrator,id:'unused'}]
  const first=nextPreparation(doc,assets,new Set(),new Set())!
  assert.deepEqual(first,{kind:'visuals',ids:voices.slice(0,8).map(a=>a.id)})
  assert.deepEqual(nextPreparation(doc,assets,new Set(first.ids),new Set()),{kind:'visuals',ids:voices.slice(8).map(a=>a.id)})
})
