import assert from 'node:assert/strict'
import test from 'node:test'
import { PreviewPlayback } from '../src/components/projects/postproduction/timeline/playback.ts'
import type { Asset, Clip, Document } from '../src/components/projects/postproduction/timeline/types.ts'

class Media extends EventTarget {
  readyState = 4
  seeking = false
  paused = true
  ended = false
  duration = 30
  playbackRate = 1
  src = ''
  error: MediaError | null = null
  current = 0
  seeks: number[] = []
  plays = 0
  disposed = false
  gain = 0
  get currentTime() { return this.current }
  set currentTime(value: number) { this.current = value; this.seeks.push(value); this.ended = false }
  play() { this.plays++; this.paused = false; return Promise.resolve() }
  pause() { this.paused = true }
  load() { this.error = null }
}
const clip = (id: string, start: number, duration: number, extra: Partial<Clip> = {}): Clip =>
  ({ id, asset_id: id, start, duration, track_id: 'voice', source_in: 0, gain: 1, group: null, ...extra })
function fixture(clips: Clip[]) {
  const document: Document = { version: 1, fps: 30, width: 1280, height: 720,
    tracks: [{ id: 'voice', name: 'Giọng', kind: 'audio', role: 'voice', muted: false, locked: false }], clips }
  const assets: Asset[] = clips.map(c => ({ id: c.asset_id!, owner: 'test', ref: c.id, name: c.id, kind: 'audio', category: 'voice',
    duration: 30, width: 0, height: 0, fps: 0, has_audio: true, url: c.id, visuals_ready: false }))
  const overlays = new Map<string, Media>()
  const transitions = new Map<string, Media>()
  const sounds = new Map<string, Media>(), video = new Media(), positions: number[] = [], buffering: boolean[] = [], errors: string[] = []
  let now = 0, nextFrame: FrameRequestCallback | null = null, stopped = 0
  const player = new PreviewPlayback({
    video: video as unknown as HTMLVideoElement,
    audio: asset => {
      const media = new Media(); sounds.set(asset.id, media)
      return { media: media as unknown as HTMLAudioElement, setGain: gain => { media.gain = gain }, dispose: () => { media.pause(); media.disposed = true } }
    },
    overlay: (_asset,clip) => {
      const media=new Media();overlays.set(clip.id,media)
      return {media:media as unknown as HTMLVideoElement,setGain:()=>{},dispose:()=>{media.pause();media.disposed=true}}
    },
    transition: (_asset, clip) => {
      const media = new Media(); transitions.set(clip.id, media)
      return { media: media as unknown as HTMLVideoElement, setGain: () => {}, dispose: () => { media.pause(); media.disposed = true } }
    },
    url: url => url, onTime: time => positions.push(time), onBuffering: value => buffering.push(value),
    onStop: () => { stopped++ }, onError: error => errors.push(error),
    request: callback => { nextFrame = callback; return 1 }, cancel: () => { nextFrame = null },
  })
  player.configure(document, assets)
  async function frame(milliseconds = 20) {
    for (const media of [video, ...sounds.values(), ...overlays.values()]) {
      if (!media.paused && media.readyState >= 3 && !media.seeking) {
        media.current += milliseconds / 1000 * media.playbackRate
        if (media.current >= media.duration) { media.current = media.duration; media.ended = true; media.paused = true }
      }
    }
    now += milliseconds
    const callback = nextFrame; nextFrame = null; callback?.(now)
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve()
  }
  return { player, sounds, overlays, transitions, video, positions, buffering, errors, frame, document, assets, stopped: () => stopped, scheduled: () => nextFrame !== null }
}

test('transition uses one paused decoder, seeks final source frame and freezes shared clock until ready', async () => {
  const f = fixture([clip('voice', 0, 4), clip('first', 0, 2, { track_id: 'picture', source_in: 3, speed: .5 }), clip('second', 2, 2, { track_id: 'picture', transition: { kind: 'dissolve', duration: .4 } })])
  f.document.tracks.unshift({ id: 'picture', kind: 'video', name: 'Hình', locked: false, muted: false })
  f.assets.forEach(a => { a.kind = 'video'; a.has_audio = true })
  f.player.configure(f.document, f.assets)
  f.player.seek(1); await f.frame(); await f.frame()
  const held = f.transitions.get('second')!
  assert.equal(held.currentTime, 3 + (2-1/30)*.5)
  assert.equal(held.plays, 0)
  held.readyState = 0
  f.player.play(2, 4); await f.frame(); await f.frame(2000)
  assert.equal(f.positions.at(-1), 2)
  assert.equal(f.buffering.at(-1), true)
  held.readyState = 4; await f.frame(); await f.frame(100)
  assert.ok(f.positions.at(-1)! > 2)
  assert.equal(held.plays, 0)
  f.player.seek(2.5); await f.frame()
  assert.equal(held.disposed, true)
  assert.deepEqual(f.errors, [])
  f.player.dispose()
})

test('preloads upcoming voices; a slow short sentence freezes the shared clock instead of being skipped', async () => {
  const f = fixture([clip('first', 0, 1), clip('short', 1, .4)])
  f.player.seek(0); await f.frame()
  assert.equal(f.sounds.size, 2)
  assert.equal(f.sounds.get('short')!.plays, 0)
  f.sounds.get('short')!.readyState = 0
  f.player.play(0, 1.4); await f.frame(); await f.frame(); await f.frame(1100)
  assert.equal(f.positions.at(-1), 1)
  await f.frame(5000)
  assert.equal(f.positions.at(-1), 1, 'loading must not consume the 400 ms cue')
  assert.equal(f.sounds.get('short')!.plays, 0)
  f.sounds.get('short')!.readyState = 4
  await f.frame(); await f.frame(); await f.frame(100)
  assert.ok(f.positions.at(-1)! > 1 && f.positions.at(-1)! < 1.4)
  assert.equal(f.sounds.get('short')!.plays, 1)
  assert.deepEqual(f.sounds.get('short')!.seeks, [], 'start from the first word after loading')
  assert.deepEqual(f.errors, [])
  f.player.dispose()
})

test('ordinary decoder drift does not trigger repeated seeks or repeated play calls', async () => {
  const f = fixture([clip('voice', 0, 10)])
  f.player.play(0, 10); await f.frame(); await f.frame()
  f.sounds.get('voice')!.current += .2
  for (let i = 0; i < 50; i++) await f.frame()
  assert.equal(f.sounds.get('voice')!.plays, 1)
  assert.deepEqual(f.sounds.get('voice')!.seeks, [])
  f.player.dispose()
})

test('regenerating a voice replaces its decoder and undo restores the previous recording', async () => {
  const f = fixture([clip('voice', 0, 4)])
  f.player.play(0, 4); await f.frame(); await f.frame()
  const old = f.sounds.get('voice')!
  const replacement = { ...f.assets[0], id: 'new-voice', url: 'new.wav' }
  const assets = [...f.assets, replacement]
  f.player.configure({ ...f.document, clips: [clip('voice', 0, 3, { asset_id: replacement.id })] }, assets)
  await f.frame(); await f.frame()
  assert.equal(old.disposed, true)
  assert.equal(f.sounds.get('new-voice')!.plays, 1)
  f.player.configure(f.document, assets)
  await f.frame(); await f.frame()
  assert.equal(f.sounds.get('new-voice')!.disposed, true)
  assert.equal(f.sounds.get('voice')!.plays, 1)
  assert.deepEqual(f.errors, [])
  f.player.dispose()
})

test('many cue boundaries do not accumulate drift or force seeks in continuous source audio', async () => {
  const cues = Array.from({ length: 50 }, (_, i) => clip(`cue-${i}`, i * .3, .3))
  const f = fixture([clip('original', 0, 16), ...cues])
  f.player.play(0, 16)
  await f.frame()
  for (let i = 0; i < 750; i++) await f.frame(19)
  assert.deepEqual(f.sounds.get('original')!.seeks, [])
  assert.ok(Math.abs(f.sounds.get('original')!.currentTime - f.positions.at(-1)!) < .08)
  f.player.dispose()
})

test('a delayed animation frame stops at each clip boundary and plays even a 100 ms cue', async () => {
  const f = fixture([clip('a', 0, 1), clip('tiny', 1, .1), clip('c', 1.1, 2)])
  f.player.play(0, 3.1); await f.frame(); await f.frame(); await f.frame(2000)
  assert.equal(f.positions.at(-1), 1)
  assert.equal(f.sounds.get('tiny')!.plays, 1)
  assert.equal(f.sounds.get('c')!.plays, 0)
  f.player.dispose()
})

test('buffering pauses the other layers and resumes without skipping forward', async () => {
  const f = fixture([clip('a', 0, 8), clip('b', 0, 8)])
  f.player.play(0, 8); await f.frame(); await f.frame(); await f.frame(100)
  const stalled = f.sounds.get('b')!
  stalled.readyState = 2
  await f.frame()
  const held = f.positions.at(-1)!
  assert.equal(f.sounds.get('a')!.paused, true)
  await f.frame(3000)
  assert.ok(Math.abs(f.positions.at(-1)! - held) < .04)
  stalled.readyState = 4
  await f.frame(); await f.frame(); await f.frame(100)
  assert.ok(f.positions.at(-1)! < held + .2)
  f.player.dispose()
})

test('seeks respect source in and speed; natural endings never loop the last words', async () => {
  const f = fixture([clip('voice', 2, 3, { source_in: 1, speed: 2 })])
  f.player.seek(3); await f.frame(); await f.frame()
  const media = f.sounds.get('voice')!
  assert.equal(media.currentTime, 3)
  f.player.play(3, 5); await f.frame(); await f.frame()
  media.duration = 7
  media.current = 7; media.ended = true; media.paused = true
  await f.frame(2000)
  assert.equal(media.plays, 1)
  assert.equal(f.stopped(), 1)
  assert.equal(f.positions.at(-1), 5)
  f.player.dispose()
})

test('a broken cue stops playback with an error, and disposal releases all decoders', async () => {
  const f = fixture([clip('broken', 0, 1)])
  f.player.seek(0); await f.frame()
  f.sounds.get('broken')!.error = { code: 4 } as MediaError
  f.player.play(0, 1)
  f.sounds.get('broken')!.error = { code: 4 } as MediaError
  await f.frame()
  assert.equal(f.stopped(), 1)
  assert.match(f.errors[0], /Không đọc được tư liệu/)
  f.player.dispose()
  assert.ok([...f.sounds.values()].every(media => media.disposed && media.paused))
})

test('future preload is bounded, and muted tracks do not fetch voices', async () => {
  const f = fixture(Array.from({ length: 100 }, (_, i) => clip(String(i), 1 + i / 100, .01)))
  f.player.seek(0); await f.frame()
  assert.equal(f.sounds.size, 12)
  f.document.tracks[0].muted = true
  f.player.configure(f.document, f.assets); await f.frame()
  assert.ok([...f.sounds.values()].every(media => media.disposed))
  f.player.dispose()
})

test('a paused loading decoder sleeps until a media event, and removes listeners on dispose', async () => {
  const f = fixture([clip('voice', 0, 5)])
  await f.frame()
  const media = f.sounds.get('voice')!
  media.readyState = 0
  f.player.seek(2); await f.frame()
  assert.equal(f.scheduled(), false, 'waiting for a paused decoder must not spin an animation loop')
  media.readyState = 4
  media.dispatchEvent(new Event('loadeddata'))
  assert.equal(f.scheduled(), true)
  await f.frame()
  assert.equal(media.currentTime, 2)
  assert.equal(media.paused, true)
  f.player.dispose()
  media.dispatchEvent(new Event('seeked'))
  assert.equal(f.scheduled(), false)
})

test('a cached voice cannot keep playing after the library marks its source missing', async () => {
  const f = fixture([clip('voice', 0, 5)])
  f.player.play(0, 5); await f.frame()
  f.assets[0].missing = true
  f.player.configure(f.document, f.assets); await f.frame()
  assert.equal(f.sounds.get('voice')!.disposed, true)
  assert.equal(f.stopped(), 1)
  assert.match(f.errors[0], /thiếu tư liệu/)
  f.player.dispose()
})


test('PiP joins shared clock, pauses voices when loading, and hidden picture keeps its voice', async()=>{
  const pip=clip('pip',1,2,{track_id:'overlay',source_in:2,speed:2})
  const f=fixture([clip('voice',0,6),pip])
  f.document.tracks.push({id:'overlay',name:'PiP',kind:'overlay',locked:false,muted:false})
  f.player.configure(f.document,f.assets)
  f.player.seek(0);await f.frame();await f.frame()
  const media=f.overlays.get('pip')!
  assert.equal(media.currentTime,2);assert.equal(media.plays,0)
  media.readyState=0
  f.player.play(0,6);await f.frame();await f.frame(1100);await f.frame(1000)
  assert.equal(f.positions.at(-1),1);assert.ok(f.sounds.get('voice')!.paused)
  media.readyState=4;await f.frame();await f.frame(100)
  assert.equal(media.playbackRate,2);assert.equal(media.plays,1)
  f.player.seek(2);await f.frame();assert.equal(media.currentTime,4)
  f.document.tracks[1].muted=true;f.player.configure(f.document,f.assets);await f.frame()
  assert.ok(media.disposed);assert.ok(!f.sounds.get('voice')!.disposed)
  f.player.dispose();assert.deepEqual(f.errors,[])
})

test('clip mute removes its audio decoder and resuming preloads from correct source time', async()=>{
  const f=fixture([clip('voice',0,6)])
  f.player.play(0,6);await f.frame()
  const first=f.sounds.get('voice')!
  f.document.clips[0].muted=true;f.player.configure(f.document,f.assets);await f.frame()
  assert.ok(first.disposed)
  f.player.seek(3);f.document.clips[0].muted=false;f.player.configure(f.document,f.assets);await f.frame()
  assert.equal(f.sounds.get('voice')!.currentTime,3)
  f.player.dispose()
})
