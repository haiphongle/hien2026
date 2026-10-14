import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { Loader2, Pause, Play, Volume2, VolumeX } from 'lucide-react'
import { API_BASE_URL, resolveMediaUrl } from '../../../api/client'
import type { EditRegion, EditingSettings, EditingState } from './editingTypes'
import { dragFrame, frameGeometry } from './editingTypes'

interface Props {
  videoId: string
  settings: EditingSettings
  source: EditingState['source']
  text: string
  showText: boolean
  player: RefObject<HTMLVideoElement | null>
  initialTime: number
  onTime: (time: number) => void
  onPlaying: (playing: boolean) => void
  onChange: (settings: EditingSettings, remember?: boolean) => void
  selectedRegion: string | null
  onRegionChange: (id: string, patch: Partial<EditRegion>) => void
  onSelectionDone: () => void
}

export function LiveEditingPreview({ videoId, settings, source, text, showText, player, initialTime, onTime, onPlaying, onChange, selectedRegion, onRegionChange, onSelectionDone }: Props) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const dragging = useRef<{ x: number; y: number; width: number; height: number; settings: EditingSettings } | null>(null)
  const selection = useRef<{ x: number; y: number } | null>(null)
  const base = useRef<HTMLCanvasElement | null>(null)
  const [selectionBox, setSelectionBox] = useState<{ x: number; y: number; width: number; height: number } | null>(null)
  const [assets, setAssets] = useState<Record<string, HTMLImageElement>>({})
  const [layer, setLayer] = useState<{ key: string; image: HTMLImageElement } | null>(null)
  const [captionFailure, setCaptionFailure] = useState<{ key: string; message: string } | null>(null)
  const [videoError, setVideoError] = useState('')
  const [ready, setReady] = useState(false)
  const [playing, setPlaying] = useState(false)
  const [muted, setMuted] = useState(false)
  const [time, setTime] = useState(initialTime)
  const [duration, setDuration] = useState(source?.duration ?? 0)
  const [retry, setRetry] = useState(0)
  const geometry = frameGeometry(settings.frame, source)
  const width = geometry?.width ?? 640, height = geometry?.height ?? 360
  // Video frames stay on the client. Only the transparent text layer needs the server.
  const captionKey = geometry && showText && text.trim()
    ? JSON.stringify({ style: settings.captions, width, height, text }) : ''
  const captionBusy = Boolean(captionKey && layer?.key !== captionKey && captionFailure?.key !== captionKey)
  const scale = Math.min(1, 720 / Math.max(width, height))
  const imageIds = JSON.stringify([...new Set((settings.regions ?? []).filter(r => r.kind === 'image' && r.image_id).map(r => r.image_id!))])

  useEffect(() => {
    let disposed = false
    const images: HTMLImageElement[] = []
    for (const id of JSON.parse(imageIds) as string[]) {
      const image = new window.Image(); images.push(image)
      image.crossOrigin = 'anonymous'
      image.onload = () => { if (!disposed) setAssets(previous => ({ ...previous, [id]: image })) }
      image.src = resolveMediaUrl(`/api/videos/${videoId}/editing/assets/${id}`)!
    }
    return () => { disposed = true; images.forEach(image => { image.onload = null }) }
  }, [imageIds, videoId])

  useEffect(() => {
    if (!captionKey) return
    const controller = new AbortController()
    let blobUrl = ''
    const timer = window.setTimeout(async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/videos/${videoId}/editing/caption-preview`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: captionKey, signal: controller.signal,
        })
        if (!response.ok) throw new Error(`Không nạp được mẫu chữ (API ${response.status}).`)
        const blob = await response.blob()
        if (controller.signal.aborted) return
        blobUrl = URL.createObjectURL(blob)
        const image = new window.Image()
        image.src = blobUrl
        await image.decode()
        if (!controller.signal.aborted) { setLayer({ key: captionKey, image }); setCaptionFailure(null) }
      } catch (cause) {
        if (!controller.signal.aborted) setCaptionFailure({ key: captionKey, message: cause instanceof Error ? cause.message : String(cause) })
      } finally { if (blobUrl) URL.revokeObjectURL(blobUrl) }
    }, 120)
    return () => { window.clearTimeout(timer); controller.abort() }
  }, [captionKey, videoId, retry])

  const draw = useCallback(() => {
    const video = player.current, surface = canvas.current, g = frameGeometry(settings.frame, source)
    const output = surface?.getContext('2d')
    if (!surface || !output || !g) return
    if (!base.current) base.current = document.createElement('canvas')
    const buffer = base.current
    if (buffer.width !== surface.width || buffer.height !== surface.height) { buffer.width = surface.width; buffer.height = surface.height }
    const context = buffer.getContext('2d')
    if (!context) return
    const factor = surface.width / g.width
    context.clearRect(0, 0, surface.width, surface.height)
    context.fillStyle = settings.frame.color
    context.fillRect(0, 0, surface.width, surface.height)
    if (video && video.readyState >= 2) {
      if (settings.frame.background === 'blur' && (g.dw < g.width || g.dh < g.height)) {
        const ratio = Math.max(g.width / video.videoWidth, g.height / video.videoHeight)
        const bw = video.videoWidth * ratio * factor, bh = video.videoHeight * ratio * factor
        context.save()
        context.filter = `blur(${25 * factor}px)`
        // Extend the blurred background beyond the border to avoid a dark fringe.
        context.drawImage(video, (surface.width - bw) / 2 - 25 * factor, (surface.height - bh) / 2 - 25 * factor, bw + 50 * factor, bh + 50 * factor)
        context.restore()
      }
      context.drawImage(video, g.sx, g.sy, g.sourceWidth, g.sourceHeight, g.dx * factor, g.dy * factor, g.dw * factor, g.dh * factor)
    }
    output.clearRect(0, 0, surface.width, surface.height)
    const colors = settings.colors
    output.filter = colors ? `brightness(${1 + colors.brightness}) contrast(${colors.contrast}) saturate(${colors.saturation})` : 'none'
    output.drawImage(buffer, 0, 0)
    output.filter = 'none'
    const position = video?.currentTime ?? 0
    for (const region of settings.regions ?? []) {
      const x = region.x * surface.width, y = region.y * surface.height, w = region.width * surface.width, h = region.height * surface.height
      if (position >= region.start && position < region.end) {
        if (region.kind === 'blur') {
          output.save(); output.beginPath(); output.rect(x, y, w, h); output.clip(); output.filter = `blur(${region.strength * factor}px)`; output.drawImage(surface, 0, 0); output.restore()
        } else if (region.kind === 'color') {
          output.save(); output.globalAlpha = region.opacity; output.fillStyle = region.color; output.fillRect(x, y, w, h); output.restore()
        } else if (region.kind === 'image' && region.image_id && assets[region.image_id]) {
          const image = assets[region.image_id], ratio = Math.min(w / image.width, h / image.height)
          output.save(); output.globalAlpha = region.opacity; output.drawImage(image, x + (w - image.width * ratio) / 2, y + (h - image.height * ratio) / 2, image.width * ratio, image.height * ratio); output.restore()
        } else if (region.kind === 'delogo') {
          output.save(); output.setLineDash([5, 3]); output.strokeStyle = '#f8c576'; output.strokeRect(x, y, w, h); output.restore()
        }
      }
      if (selectedRegion === region.id) {
        output.save(); output.strokeStyle = '#71b3e5'; output.lineWidth = 2; output.strokeRect(x, y, w, h); output.restore()
      }
    }
    if (selectionBox) {
      output.save(); output.strokeStyle = '#71b3e5'; output.setLineDash([5, 3]); output.strokeRect(selectionBox.x * surface.width, selectionBox.y * surface.height, selectionBox.width * surface.width, selectionBox.height * surface.height); output.restore()
    }
    // Never display a text layer from an earlier style while the new one is loading.
    if (captionKey && layer?.key === captionKey) output.drawImage(layer.image, 0, 0, surface.width, surface.height)
  }, [player, settings.frame, settings.colors, settings.regions, source, captionKey, layer, assets, selectedRegion, selectionBox])

  useEffect(() => {
    const video = player.current
    if (!video) return
    let frame = 0, lastPaint = 0
    const animate = (now: number) => {
      if (now - lastPaint >= 1000 / 30) { draw(); lastPaint = now }
      if (!video.paused && !video.ended) frame = requestAnimationFrame(animate)
    }
    const start = () => { cancelAnimationFrame(frame); frame = requestAnimationFrame(animate) }
    const stop = () => { cancelAnimationFrame(frame); draw() }
    draw()
    if (!video.paused) start()
    video.addEventListener('play', start)
    video.addEventListener('pause', stop)
    video.addEventListener('loadeddata', draw)
    video.addEventListener('seeked', draw)
    return () => {
      cancelAnimationFrame(frame)
      video.removeEventListener('play', start); video.removeEventListener('pause', stop)
      video.removeEventListener('loadeddata', draw); video.removeEventListener('seeked', draw)
    }
  }, [draw, player])

  useEffect(() => { if (player.current) player.current.volume = Math.min(1, settings.source_volume) }, [player, settings.source_volume])
  const clock = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`

  return <div className="pp-live-preview">
    <video ref={player} className="pp-live-source" src={resolveMediaUrl(`/api/videos/${videoId}/editing/source`)} crossOrigin="anonymous" preload="auto" playsInline muted={muted} aria-hidden="true" tabIndex={-1}
      onLoadedMetadata={e => { setDuration(e.currentTarget.duration); e.currentTarget.currentTime = Math.min(initialTime, Math.max(0, e.currentTarget.duration - .1)) }}
      onLoadedData={() => { setReady(true); setVideoError('') }}
      onTimeUpdate={e => { setTime(e.currentTarget.currentTime); onTime(e.currentTarget.currentTime) }}
      onPlay={() => { setPlaying(true); onPlaying(true) }} onPause={() => { setPlaying(false); onPlaying(false) }} onEnded={() => { setPlaying(false); onPlaying(false) }}
      onError={() => setVideoError('Không nạp được video nguồn. Thử nạp lại trang.')} />
    <div className="pp-preview-screen pp-live-screen">
      <canvas ref={canvas} width={Math.round(width * scale)} height={Math.round(height * scale)} aria-label={`Xem trước trực tiếp ${width} × ${height}`} style={{ aspectRatio: `${width} / ${height}`, cursor: selectedRegion ? 'crosshair' : 'grab' }}
        onPointerDown={e => {
          e.currentTarget.setPointerCapture(e.pointerId)
          const box = e.currentTarget.getBoundingClientRect()
          if (selectedRegion) { selection.current = { x: Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)), y: Math.max(0, Math.min(1, (e.clientY - box.top) / box.height)) }; return }
          dragging.current = { x: e.clientX, y: e.clientY, width: box.width, height: box.height, settings }
          onChange(settings)
        }}
        onPointerMove={e => {
          if (selection.current) {
            const box = e.currentTarget.getBoundingClientRect(), start = selection.current
            const x = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width)), y = Math.max(0, Math.min(1, (e.clientY - box.top) / box.height))
            setSelectionBox({ x: Math.min(x, start.x), y: Math.min(y, start.y), width: Math.abs(x - start.x), height: Math.abs(y - start.y) }); return
          }
          const drag = dragging.current; if (drag) onChange({ ...drag.settings, frame: dragFrame(drag.settings.frame, source, (e.clientX - drag.x) / drag.width, (e.clientY - drag.y) / drag.height) }, false)
        }}
        onPointerUp={() => { dragging.current = null; selection.current = null; if (selectedRegion && selectionBox && selectionBox.width >= .01 && selectionBox.height >= .01) { onRegionChange(selectedRegion, selectionBox); onSelectionDone() } setSelectionBox(null) }}
        onPointerCancel={() => { dragging.current = null; selection.current = null; setSelectionBox(null) }} />
      {!ready && !videoError && <span className="pp-live-loading"><Loader2 className="animate-spin" size={17} />Đang nạp video…</span>}
    </div>
    <div className="pp-live-controls">
      <button type="button" aria-label={playing ? 'Tạm dừng xem trước' : 'Phát xem trước'} disabled={!ready} onClick={() => {
        const video = player.current
        if (!video) return
        if (!video.paused) video.pause()
        else void video.play().catch(() => setVideoError('Không phát được video. Thử lại.'))
      }}>{playing ? <Pause size={16} /> : <Play size={16} />}</button>
      <input aria-label="Vị trí xem trước" type="range" min={0} max={duration || 1} step={.1} value={time} disabled={!ready} onChange={e => { if (player.current) player.current.currentTime = Number(e.target.value); setTime(Number(e.target.value)); onTime(Number(e.target.value)) }} />
      <span>{clock(time)} / {clock(duration)}</span>
      <button type="button" aria-label={muted ? 'Bật âm gốc xem trước' : 'Tắt âm gốc xem trước'} onClick={() => setMuted(!muted)}>{muted ? <VolumeX size={16} /> : <Volume2 size={16} />}</button>
    </div>
    {captionBusy && <p className="pp-live-status" role="status"><Loader2 size={12} className="animate-spin" />Đang cập nhật mẫu chữ…</p>}
    {captionFailure?.key === captionKey && <p className="pp-live-status" role="alert">{captionFailure.message}<button onClick={() => { setCaptionFailure(null); setRetry(v => v + 1) }}>Thử lại</button></p>}
    {(videoError || !geometry) && <p className="pp-live-status" role="alert">{videoError || 'Nhập kích thước hợp lệ để xem trước.'}</p>}
  </div>
}
