import type { Asset, Clip, Document, Visuals } from './types.ts'
import { audible, clipGain, voiceIntervals } from './audio.ts'
import { transitionAt, transitionJoins } from './transitions.ts'

export const PRELOAD_SECONDS = 8
const EPSILON = 0.000001
export const containsTime = (clip: Clip, time: number) => time >= clip.start && time < clip.start + clip.duration - EPSILON

export interface MediaOutput {
  media: HTMLMediaElement
  setGain: (gain: number) => void
  dispose: () => void
}
interface Slot extends MediaOutput {
  key: string
  positioned: boolean
  pending: Promise<void> | null
  failure: string
  lastSeek: number
}
interface Options {
  video: HTMLVideoElement
  audio: (asset: Asset) => MediaOutput
  overlay?: (asset: Asset, clip: Clip) => MediaOutput
  transition?: (asset: Asset, clip: Clip) => MediaOutput
  url: (url: string) => string
  onTime: (time: number) => void
  onBuffering: (buffering: boolean) => void
  onStop: () => void
  onError: (error: string) => void
  request: (callback: FrameRequestCallback) => number
  cancel: (id: number) => void
}

/** One clock for all layers. Media loading must never consume a spoken cue. */
export class PreviewPlayback {
  private options: Options
  private doc: Document | null = null
  private assets = new Map<string, Asset>()
  private audioClips: Clip[] = []
  private pictures: Clip[] = []
  private overlays: Clip[] = []
  private overlaySlots = new Map<string, Slot>()
  private transitionSlot: Slot | null = null
  private joins: ReturnType<typeof transitionJoins> = []
  private boundaries: number[] = []
  private voices: number[][] = []
  private sounds = new Map<string, Slot>()
  private picture: Slot
  private pictureUrl = ''
  private position = 0
  private finish = 0
  private running = false
  private advancing = false
  private remainder = 0
  private previous: number | null = null
  private bufferedAt: number | null = null
  private buffering = false
  private requestId: number | null = null
  private disposed = false
  private publishedAt = -Infinity

  constructor(options: Options) {
    this.options = options
    this.picture = this.slot({ media: options.video, setGain: () => {}, dispose: () => options.video.pause() })
  }
  private slot(output: MediaOutput): Slot {
    const wake = () => this.schedule()
    const events = ['loadedmetadata', 'loadeddata', 'canplay', 'seeked', 'error']
    for (const event of events) output.media.addEventListener(event, wake)
    return { ...output, key: '', positioned: false, pending: null, failure: '', lastSeek: -Infinity,
      dispose: () => {
        for (const event of events) output.media.removeEventListener(event, wake)
        output.dispose()
      },
    }
  }
  configure(doc: Document, assets: Asset[], visuals: Record<string, Visuals> = {}) {
    this.doc = doc
    this.joins = transitionJoins(doc).filter(j => j.next.transition)
    this.assets = new Map(assets.map(asset => [asset.id, asset]))
    const tracks = new Map(doc.tracks.map(track => [track.id, track]))
    this.audioClips = doc.clips.filter(clip => {
      const track = tracks.get(clip.track_id)
      return track?.kind === 'audio' && audible(doc, track) && !clip.muted && clip.gain > 0
    }).sort((a, b) => a.start - b.start)
    this.pictures = doc.clips.filter(clip => tracks.get(clip.track_id)?.kind === 'video' && !tracks.get(clip.track_id)?.muted)
    this.overlays = doc.clips.filter(c => tracks.get(c.track_id)?.kind === 'overlay' && !tracks.get(c.track_id)?.muted && !c.overlay?.hidden).sort((a,b) => a.start-b.start)
    this.boundaries = [...new Set(doc.clips.flatMap(clip => [clip.start, clip.start + clip.duration]))].sort((a, b) => a - b)
    this.voices = voiceIntervals(doc, visuals)
    this.schedule()
  }
  seek(time: number) {
    this.pause()
    this.position = time
    for (const slot of [...this.sounds.values(), ...this.overlaySlots.values(), ...(this.transitionSlot ? [this.transitionSlot] : []), this.picture]) slot.positioned = false
    this.schedule()
  }
  play(time: number, finish: number) {
    this.seek(time)
    for (const slot of [...this.sounds.values(), ...this.overlaySlots.values(), ...(this.transitionSlot ? [this.transitionSlot] : []), this.picture]) {
      if (slot.failure || slot.media.error) { slot.failure = ''; slot.media.load() }
    }
    this.finish = finish
    this.running = true
    this.schedule()
  }
  pause() {
    this.running = false
    this.advancing = false
    this.remainder = 0
    this.previous = null
    this.bufferedAt = null
    this.setBuffering(false)
    this.pauseMedia()
  }
  private pauseMedia() {
    for (const slot of [...this.sounds.values(), ...this.overlaySlots.values(), this.picture]) if (!slot.media.paused) slot.media.pause()
  }
  private setBuffering(value: boolean) {
    if (this.buffering !== value) { this.buffering = value; this.options.onBuffering(value) }
  }
  private schedule() {
    if (!this.disposed && this.requestId === null) this.requestId = this.options.request(this.tick)
  }
  private window() {
    // Bound future decoders, but never drop an overlapping active layer.
    const future = this.audioClips.filter(c => c.start > this.position && c.start <= this.position + PRELOAD_SECONDS).slice(0, 12)
    const clips = this.audioClips.filter(c => c.start <= this.position && c.start + c.duration > this.position - .5).concat(future)
    const wanted = new Set(clips.map(c => c.id))
    for (const [id, slot] of this.sounds) {
      if (!wanted.has(id)) { slot.dispose(); this.sounds.delete(id) }
    }
    for (const clip of clips) {
      const asset = this.assets.get(clip.asset_id ?? '')
      if (!asset || asset.missing) {
        this.sounds.get(clip.id)?.dispose(); this.sounds.delete(clip.id)
        continue
      }
      const key = `${asset.id}:${clip.start}:${clip.source_in}:${clip.speed ?? 1}`
      let slot = this.sounds.get(clip.id)
      if (slot && slot.key !== key) { slot.dispose(); this.sounds.delete(clip.id); slot = undefined }
      if (!slot) { slot = this.slot(this.options.audio(asset)); slot.key = key; this.sounds.set(clip.id, slot) }
    }
    return clips
  }
  private overlayWindow() {
    const clips = this.overlays.filter(c => containsTime(c, this.position)).concat(this.overlays.filter(c => c.start > this.position && c.start <= this.position + PRELOAD_SECONDS).slice(0, 1))
    const wanted = new Set(clips.map(c => c.id))
    for (const [id, slot] of this.overlaySlots) if (!wanted.has(id)) { slot.dispose(); this.overlaySlots.delete(id) }
    for (const c of clips) {
      const asset = this.assets.get(c.asset_id ?? '')
      const key = `${asset?.id}:${asset?.proxy_url ?? asset?.url}:${c.start}:${c.source_in}:${c.speed ?? 1}`
      let slot = this.overlaySlots.get(c.id)
      if (slot && (slot.key !== key || !asset || asset.missing)) { slot.dispose(); this.overlaySlots.delete(c.id); slot = undefined }
      if (!slot && asset && !asset.missing && this.options.overlay) { slot = this.slot(this.options.overlay(asset, c)); slot.key = key; this.overlaySlots.set(c.id, slot) }
    }
    return clips
  }
  private prepare(slot: Slot, clip: Clip, now: number, active: boolean) {
    const media = slot.media, speed = clip.speed ?? 1
    const target = clip.source_in + (active ? Math.max(0, this.position - clip.start) * speed : 0)
    if (media.error || slot.failure) throw new Error(slot.failure || `Không đọc được tư liệu: ${this.assets.get(clip.asset_id ?? '')?.name ?? clip.id}`)
    if (media.playbackRate !== speed) media.playbackRate = speed
    if (media.readyState < 1) return false
    // Short files can finish a fraction of a frame before the edit ends. Do not replay them.
    if (media.ended && target >= media.duration - .05) return true
    if (media.seeking) return false
    const drift = Math.abs(media.currentTime - target)
    if ((!slot.positioned && drift > .025) || (active && this.advancing && drift > .5 && now - slot.lastSeek > 2000)) {
      media.currentTime = target
      slot.lastSeek = now
      slot.positioned = true
      return false
    }
    slot.positioned = true
    return media.readyState >= (this.running ? 3 : 2)
  }
  private prepareTransition(now: number) {
    if (!this.doc || !this.options.transition) return true
    const join = transitionAt(this.doc, this.position, PRELOAD_SECONDS, this.joins)
    const asset = this.assets.get(join?.previous.asset_id ?? '')
    const key = join && asset ? `${join.next.id}:${asset.id}:${asset.proxy_url ?? asset.url}:${join.previous.source_in}:${join.previous.duration}:${join.previous.speed ?? 1}` : ''
    if (this.transitionSlot && this.transitionSlot.key !== key) { this.transitionSlot.dispose(); this.transitionSlot = null }
    if (!join || !asset || asset.missing) return true
    if (!this.transitionSlot) { this.transitionSlot = this.slot(this.options.transition(asset, join.next)); this.transitionSlot.key = key }
    const held = { ...join.previous, source_in: Math.max(0, Math.min(asset.duration - 1/this.doc.fps, join.previous.source_in + (join.previous.duration-1/this.doc.fps)*(join.previous.speed ?? 1))), speed: 1 }
    this.transitionSlot.media.pause()
    if (this.position < join.next.start && (this.transitionSlot.media.error || this.transitionSlot.failure)) return true
    const ready = this.prepare(this.transitionSlot, held, now, false)
    return this.position < join.next.start || ready
  }
  private start(slot: Slot) {
    const media = slot.media
    if (media.ended) return true
    if (media.paused && !slot.pending) {
      const pending = media.play()
      slot.pending = pending
      void pending.catch((cause: unknown) => {
        if ((cause as { name?: string }).name !== 'AbortError') slot.failure = 'Trình duyệt không phát được tư liệu. Bấm phát lại hoặc kiểm tra file nguồn.'
      }).finally(() => { if (slot.pending === pending) slot.pending = null })
    }
    return !media.paused
  }
  private tick = (now: number) => {
    this.requestId = null
    if (!this.doc || this.disposed) return
    let crossed = false
    if (this.running && this.advancing && this.previous !== null) {
      const proposed = Math.min(this.finish, this.position + (now - this.previous) / 1000 + this.remainder)
      // A slow UI frame must not jump over an entire short sentence.
      const boundary = this.boundaries.find(at => at > this.position + EPSILON && at <= proposed)
      crossed = boundary !== undefined
      this.position = boundary ?? proposed
      // Retain a normal frame's fractional time at boundaries so 100 cues do
      // not accumulate drift. Do not catch up seconds of a suspended browser.
      this.remainder = Math.min(.05, proposed - this.position)
    }
    this.previous = now
    try {
      const clips = this.window(), current: Slot[] = []
      let ready = true
      for (const clip of clips) {
        const isActive = containsTime(clip, this.position), slot = this.sounds.get(clip.id)
        if (!slot) { if (isActive) throw new Error('Đoạn âm thanh bị thiếu tư liệu. Nạp lại thư viện hoặc chọn lại file.'); continue }
        if (!isActive) {
          slot.media.pause()
          // Seek upcoming trimmed clips in advance, without blocking the current sentence.
          if (clip.start > this.position && !slot.media.error && !slot.failure) this.prepare(slot, clip, now, false)
          continue
        }
        current.push(slot)
        slot.setGain(clipGain(this.doc, clip, this.position, this.voices))
        if (!this.prepare(slot, clip, now, true)) ready = false
      }
      for (const clip of this.overlayWindow()) {
        const slot = this.overlaySlots.get(clip.id), isActive = containsTime(clip, this.position)
        if (!slot) { if (isActive) throw new Error('Video chèn bị thiếu tư liệu. Nạp lại thư viện hoặc chọn lại file.'); continue }
        if (!isActive) { slot.media.pause(); if (!slot.media.error && !slot.failure) this.prepare(slot, clip, now, false); continue }
        current.push(slot)
        if (!this.prepare(slot, clip, now, true)) ready = false
      }
      const picture = this.pictures.find(clip => containsTime(clip, this.position))
      if (picture) {
        const asset = this.assets.get(picture.asset_id ?? '')
        if (!asset || asset.missing) throw new Error('Video nguồn không còn trong thư viện.')
        const url = this.options.url(asset.proxy_url ?? asset.url)
        if (url !== this.pictureUrl) {
          this.picture.media.pause()
          this.picture.media.src = url
          this.pictureUrl = url
          this.picture.positioned = false
        }
        const key = `${picture.id}:${picture.start}:${picture.source_in}:${picture.speed ?? 1}`
        if (key !== this.picture.key) { this.picture.key = key; this.picture.positioned = false }
        current.push(this.picture)
        if (!this.prepare(this.picture, picture, now, true)) ready = false
      } else this.picture.media.pause()
      if (!this.prepareTransition(now)) ready = false

      if (this.running && this.position >= this.finish - EPSILON) {
        this.position = this.finish
        this.pause()
        this.options.onTime(this.position)
        this.options.onStop()
        return
      }
      if (!ready || !this.running) this.pauseMedia()
      if (ready && this.running) {
        // The pending promise deduplicates play calls. Once ready media are
        // unpaused, keep the clock moving instead of losing a frame per cue.
        for (const slot of current) if (!this.start(slot)) ready = false
      }
      this.advancing = this.running && ready
      if (!this.advancing) this.remainder = 0
      this.setBuffering(this.running && !ready)
      if (this.running && !ready) {
        this.bufferedAt ??= now
        if (now - this.bufferedAt > 15000) throw new Error('Tư liệu tải quá lâu. Đã dừng để tránh bỏ câu; bấm Phát để thử lại.')
      } else this.bufferedAt = null
      if (this.running && (crossed || now - this.publishedAt >= 1000 / 30)) {
        this.options.onTime(this.position)
        this.publishedAt = now
      }
      // Paused decoders wake us through media events; do not spin at 60 fps
      // forever when a slow file is still loading at a paused playhead.
      if (this.running) this.schedule()
    } catch (cause) {
      this.pause()
      this.options.onTime(this.position)
      this.options.onError(cause instanceof Error ? cause.message : String(cause))
      this.options.onStop()
    }
  }
  dispose() {
    this.disposed = true
    this.pause()
    if (this.requestId !== null) this.options.cancel(this.requestId)
    for (const slot of this.sounds.values()) slot.dispose()
    this.sounds.clear()
    for (const slot of this.overlaySlots.values()) slot.dispose()
    this.overlaySlots.clear()
    this.transitionSlot?.dispose(); this.transitionSlot = null
    this.picture.dispose()
  }
}
