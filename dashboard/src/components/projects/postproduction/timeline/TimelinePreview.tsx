import { useEffect, useEffectEvent, useMemo, useRef, useState } from 'react'
import { Pause, Play, SkipBack } from 'lucide-react'
import { resolveMediaUrl } from '../../../../api/client'
import type { Asset, Clip, Document, Visuals } from './types'
import { clock, end, length } from './operations'
import { containsTime, PRELOAD_SECONDS, PreviewPlayback } from './playback'
import { AudioMeter } from './AudioMeter'
import { CaptionOverlay, EffectOverlay } from './OverlayPreview'
import { DEFAULT_FRAME, outputSize } from '../editingTypes'
import { VideoOverlayPreview } from './VideoOverlayPreview'
import { FrameCanvas } from './FrameCanvas'
import { transitionAt, transitionJoins } from './transitions'

let context: AudioContext | null = null
let mix: GainNode | null = null, analyser: AnalyserNode | null = null
function audioContext() {
  context ??= new AudioContext()
  if(!mix){mix=context.createGain();analyser=context.createAnalyser();analyser.fftSize=2048;const limiter=context.createDynamicsCompressor();limiter.threshold.value=-.5;limiter.ratio.value=20;limiter.knee.value=0;mix.connect(analyser);analyser.connect(limiter);limiter.connect(context.destination)}
  return context
}

function mediaUrl(url: string) {
  const resolved = resolveMediaUrl(url)
  if (!resolved) throw new Error('Tư liệu chưa có đường dẫn để phát.')
  return resolved
}
function createAudio(asset: Asset, host: HTMLDivElement | null) {
  const media = new Audio()
  media.dataset.timelineMedia = ''; media.dataset.previewAsset = asset.id
  media.crossOrigin = 'anonymous'; media.preload = 'auto'; media.src = mediaUrl(asset.url)
  host?.appendChild(media)
  const ctx = audioContext(), source = ctx.createMediaElementSource(media), gain = ctx.createGain()
  source.connect(gain); gain.connect(mix!)
  return {
    media,
    setGain: (value: number) => gain.gain.setTargetAtTime(value, ctx.currentTime, .01),
    dispose: () => { media.pause(); source.disconnect(); gain.disconnect(); media.removeAttribute('src'); media.load(); media.remove() },
  }
}

export function TimelinePreview({ doc, assets, visuals, time, onTime, playing, onPlaying, active,videoId,selection,onSelect,onPatch,range }: {
  doc: Document; assets: Asset[]; visuals: Record<string,Visuals>; time: number; onTime: (n: number) => void; playing: boolean; onPlaying: (b: boolean) => void; active: boolean;videoId:string;selection:string[];onSelect:(id:string)=>void;onPatch:(id:string,changes:Partial<Clip>)=>void;range?:{start:number;end:number}|null
}) {
  const video = useRef<HTMLVideoElement>(null), [error, setError] = useState('')
  const [overlayMedia, setOverlayMedia] = useState<Record<string, HTMLVideoElement>>({})
  const [transitionMedia, setTransitionMedia] = useState<{ id: string; media: HTMLVideoElement } | null>(null)
  const audioHost = useRef<HTMLDivElement>(null)
  const transport = useRef<PreviewPlayback | null>(null), [buffering, setBuffering] = useState(false)
  const duration = length(doc)
  const tracks = useMemo(() => new Map(doc.tracks.map(track => [track.id, track])), [doc.tracks])
  const picture = doc.clips.find(c => tracks.get(c.track_id)?.kind === 'video' && !tracks.get(c.track_id)?.muted && containsTime(c, time))
  const asset = assets.find(a => a.id === picture?.asset_id)
  const frame = doc.frame ?? DEFAULT_FRAME
  const [width, height] = outputSize(frame, { width: doc.width, height: doc.height, duration })!
  const overlays = doc.clips.filter(c => ['caption', 'text', 'effect'].includes(tracks.get(c.track_id)?.kind ?? '') && !tracks.get(c.track_id)?.muted && containsTime(c, time))
  // Prepare upcoming text images before their first frame, like the audio window.
  const captions = doc.clips.filter(c => ['caption', 'text'].includes(tracks.get(c.track_id)?.kind ?? '') && c.text?.trim() && c.caption && !tracks.get(c.track_id)?.muted && end(c) > time && c.start <= time + PRELOAD_SECONDS)
  const pipClips = doc.clips.filter(c => tracks.get(c.track_id)?.kind === 'overlay' && !tracks.get(c.track_id)?.muted && !c.overlay?.hidden && containsTime(c, time))
  const split = pipClips.some(c => c.overlay?.layout === 'split')
  const joins = useMemo(() => transitionJoins(doc).filter(j => j.next.transition), [doc])
  const join = transitionAt(doc, time, 0, joins)
  const previousTime = join ? end(join.previous) - 1/doc.fps : 0
  const previousSplit = join && doc.clips.some(c => tracks.get(c.track_id)?.kind === 'overlay' && !tracks.get(c.track_id)?.muted && !c.overlay?.hidden && c.overlay?.layout === 'split' && containsTime(c, previousTime))
  const colors = overlays.filter(c => c.colors)
  const updateTime = useEffectEvent(onTime)
  const stop = useEffectEvent(() => onPlaying(false))
  useEffect(() => {
    if (!active || !video.current) return
    let alive = true
    const player = new PreviewPlayback({ video: video.current, audio: asset => createAudio(asset, audioHost.current), url: mediaUrl,
      overlay: (asset, clip) => {
        const media = document.createElement('video')
        media.muted = true; media.playsInline = true; media.preload = 'auto'; media.crossOrigin = 'anonymous'
        media.dataset.timelineMedia = ''; media.src = mediaUrl(asset.proxy_url ?? asset.url)
        audioHost.current?.appendChild(media)
        setOverlayMedia(previous => ({...previous,[clip.id]:media}))
        return {media,setGain:()=>{},dispose:()=>{media.pause();media.removeAttribute('src');media.load();media.remove();if(alive)setOverlayMedia(previous=>{const next={...previous};delete next[clip.id];return next})}}
      },
      transition: (asset, clip) => {
        const media = document.createElement('video')
        media.muted = true; media.playsInline = true; media.preload = 'auto'; media.crossOrigin = 'anonymous'
        media.dataset.timelineMedia = ''; media.src = mediaUrl(asset.proxy_url ?? asset.url)
        audioHost.current?.appendChild(media); setTransitionMedia({ id: clip.id, media })
        return { media, setGain: () => {}, dispose: () => { media.pause(); media.removeAttribute('src'); media.load(); media.remove(); if (alive) setTransitionMedia(previous => previous?.id === clip.id ? null : previous) } }
      },
      onTime: value => updateTime(value), onStop: () => stop(), onError: setError, onBuffering: setBuffering,
      request: callback => requestAnimationFrame(callback), cancel: id => cancelAnimationFrame(id) })
    transport.current = player
    const visibility = () => { if (document.hidden) { player.pause(); stop() } }
    document.addEventListener('visibilitychange', visibility)
    return () => { document.removeEventListener('visibilitychange', visibility); alive = false; player.dispose(); transport.current = null }
  }, [active, videoId])
  useEffect(() => { transport.current?.configure(doc, assets, visuals) }, [doc, assets, visuals, active, videoId])
  const control = useEffectEvent(() => {
    if (playing) transport.current?.play(time, range?.end ?? duration)
    else transport.current?.seek(time)
  })
  useEffect(() => { control() }, [playing, active, videoId, range, duration])
  useEffect(() => { if (!playing) transport.current?.seek(time) }, [time, playing, active])
  async function toggle() {
    setError('')
    if (!playing) {
      try { await audioContext().resume() } catch { setError('Trình duyệt chưa cho phép phát âm thanh.'); return }
      if (range&&(time<range.start||time>=range.end))onTime(range.start)
      else if (time >= duration) onTime(0)
    }
    onPlaying(!playing)
  }
  return <section className="tl-viewer" aria-label="Xem trước bản dựng">
    <div className="tl-panel-title"><strong>Xem trước bản dựng</strong><span>{width} × {height} · {doc.fps.toFixed(2)} fps</span></div>
    <div className="tl-monitor"><div style={{ '--tl-aspect': width / height, background: frame.color } as React.CSSProperties} className="tl-screen">
      {colors.length>0&&<svg width="0" height="0" aria-hidden="true"><filter id={`tl-color-${videoId}`} colorInterpolationFilters="sRGB">{colors.flatMap((c,i)=>{const color=c.colors!;return [<feComponentTransfer key={`b${i}`}><feFuncR type="linear" slope={color.contrast} intercept={color.brightness+(1-color.contrast)/2}/><feFuncG type="linear" slope={color.contrast} intercept={color.brightness+(1-color.contrast)/2}/><feFuncB type="linear" slope={color.contrast} intercept={color.brightness+(1-color.contrast)/2}/></feComponentTransfer>,<feColorMatrix key={`s${i}`} type="saturate" values={String(color.saturation)}/>]})}</filter></svg>}
      <video ref={video} className="tl-decoder" data-timeline-media muted playsInline preload="auto" />
      {picture && asset && !asset.missing && <FrameCanvas video={video} frame={frame} width={width} height={height} time={time} playing={playing && active && !buffering} filter={colors.length ? `url(#tl-color-${videoId})` : undefined} split={split}
        transition={join && transitionMedia?.id === join.next.id ? { kind: join.next.transition!.kind, progress: (time-join.next.start)/join.duration, media: transitionMedia.media, split: !!previousSplit } : undefined}/>} 
      {!picture && <span className="tl-gap">Khoảng trống</span>}
      {pipClips.map(c => <VideoOverlayPreview key={c.id} clip={c} media={overlayMedia[c.id] ?? null} width={width} height={height} time={time} playing={playing && active && !buffering} selected={selection.includes(c.id)} locked={!!tracks.get(c.track_id)?.locked} onSelect={()=>onSelect(c.id)} onChange={overlay=>onPatch(c.id,{overlay})}/>)}
      {overlays.filter(c => c.effect).map(c => <EffectOverlay key={c.id} clip={c} videoId={videoId} selected={selection.includes(c.id)} onSelect={() => onSelect(c.id)} onMove={(x, y) => onPatch(c.id, { effect: { ...c.effect!, x, y } })} />)}
      {captions.map(c => <CaptionOverlay key={c.id} clip={c} videoId={videoId} width={width} height={height} visible={containsTime(c, time)} selected={selection.includes(c.id)} onSelect={() => onSelect(c.id)} disabled={!!tracks.get(c.track_id)?.locked} onChange={caption => onPatch(c.id, { caption })} />)}
    </div></div>
    <div ref={audioHost} hidden />
    <div className="tl-transport"><button title="Về đầu" onClick={() => { onPlaying(false); onTime(0) }}><SkipBack size={16} /></button>
      <button className="tl-play" aria-label={playing ? 'Tạm dừng bản dựng' : 'Phát bản dựng'} disabled={!duration} onClick={() => void toggle()}>{playing ? <Pause size={19} /> : <Play size={19} />}</button>
      <span className="tl-time">{clock(time)} <small>/ {clock(duration)}</small></span><span className="tl-preview-note" role="status">{buffering ? 'Đang tải hình và tiếng…' : 'Hình và tiếng theo timeline'}</span>
    </div>
    <AudioMeter analyser={analyser} playing={playing&&active&&!buffering}/>
    {error && <p role="alert" className="tl-error">{error}</p>}
  </section>
}
