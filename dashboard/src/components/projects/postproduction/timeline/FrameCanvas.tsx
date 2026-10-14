import { useEffect, useEffectEvent, useRef } from 'react'
import { frameGeometry, type FrameSettings } from '../editingTypes'
import { mainPaneWidth } from './videoOverlay'
import type { Transition } from './types'
import { transitionWeights } from './transitions'

/** Reuse the foreground decoder for the blurred background. */
export function FrameCanvas({ video, frame, width, height, playing, time, filter, split = false, transition }: {
  video: React.RefObject<HTMLVideoElement | null>; frame: FrameSettings; width: number; height: number
  playing: boolean; time: number; filter?: string; split?: boolean
  transition?: { kind: Transition['kind']; progress: number; media: HTMLVideoElement; split: boolean }
}) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const pausedTime = playing ? null : time
  const pausedTransition = playing ? null : `${transition?.kind ?? ''}:${transition?.progress ?? 0}:${transition?.split ?? false}`
  const currentTransition = useEffectEvent(() => transition)
  const previousMedia = transition?.media
  useEffect(() => {
    const media = video.current, surface = canvas.current
    if (!media || !surface) return
    const scale = Math.min(1, 960 / Math.max(width, height))
    if (surface.width !== Math.round(width * scale)) surface.width = Math.round(width * scale)
    if (surface.height !== Math.round(height * scale)) surface.height = Math.round(height * scale)
    const ctx = surface.getContext('2d')
    if (!ctx) return
    const incoming = document.createElement('canvas'), outgoing = document.createElement('canvas')
    for (const layer of [incoming, outgoing]) { layer.width = surface.width; layer.height = surface.height }
    const incomingContext = incoming.getContext('2d')!, outgoingContext = outgoing.getContext('2d')!
    let request: number | null = null
    function source(ctx: CanvasRenderingContext2D, media: HTMLVideoElement, split: boolean) {
      ctx.setTransform(scale, 0, 0, scale, 0, 0)
      const paneWidth = split ? mainPaneWidth(width) : width
      ctx.filter = 'none'; ctx.fillStyle = frame.color; ctx.fillRect(0, 0, width, height)
      if (media.readyState >= 2 && media.videoWidth) {
        const geometry = frameGeometry({ ...frame, preset: 'custom', width: paneWidth, height }, { width: media.videoWidth, height: media.videoHeight, duration: 0 }, 2)
        if (frame.background === 'blur') {
          const ratio = Math.max(paneWidth / media.videoWidth, height / media.videoHeight)
          const bw = media.videoWidth * ratio, bh = media.videoHeight * ratio
          ctx.save(); ctx.beginPath(); ctx.rect(0, 0, paneWidth, height); ctx.clip()
          ctx.filter = `blur(${25*scale}px)`; ctx.drawImage(media, (paneWidth - bw) / 2, (height - bh) / 2, bw, bh); ctx.restore()
        }
        if (geometry) ctx.drawImage(media, geometry.sx, geometry.sy, geometry.sourceWidth, geometry.sourceHeight, geometry.dx, geometry.dy, geometry.dw, geometry.dh)
      }
    }
    function draw() {
      if (!ctx || !surface || !media) return
      const value = currentTransition()
      if (!value || value.media.readyState < 2 || value.media.seeking) { source(ctx, media, split); return }
      source(incomingContext, media, split); source(outgoingContext, value.media, value.split)
      const p = Math.max(0, Math.min(1, value.progress))
      ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.filter = 'none'; ctx.fillStyle = '#000'; ctx.fillRect(0, 0, surface.width, surface.height)
      if (value.kind === 'slide') {
        ctx.drawImage(outgoing, -Math.round(p*surface.width), 0)
        ctx.drawImage(incoming, surface.width-Math.round(p*surface.width), 0)
      } else {
        const weights = transitionWeights(value.kind, p)
        if (value.kind === 'light') { ctx.fillStyle = `rgb(${255*weights.background},${255*weights.background},${255*weights.background})`; ctx.fillRect(0, 0, surface.width, surface.height) }
        ctx.save(); ctx.globalCompositeOperation = 'lighter'
        ctx.globalAlpha = weights.next; ctx.drawImage(incoming, 0, 0)
        ctx.globalAlpha = weights.old
        if (value.kind === 'blur') ctx.filter = `blur(${4*height/720*scale}px)`
        ctx.drawImage(outgoing, 0, 0); ctx.restore()
      }
    }
    function tick() { request = null; draw(); if (playing) schedule() }
    function schedule() { if (request === null) request = requestAnimationFrame(tick) }
    // Data/seek events must share the same pending frame, never start extra loops.
    schedule()
    media.addEventListener('loadeddata', schedule); media.addEventListener('seeked', schedule)
    previousMedia?.addEventListener('loadeddata', schedule); previousMedia?.addEventListener('seeked', schedule)
    return () => { if (request !== null) cancelAnimationFrame(request); media.removeEventListener('loadeddata', schedule); media.removeEventListener('seeked', schedule); previousMedia?.removeEventListener('loadeddata', schedule); previousMedia?.removeEventListener('seeked', schedule) }
  }, [video, frame, width, height, playing, pausedTime, pausedTransition, split, previousMedia])
  return <canvas ref={canvas} className="tl-frame-canvas" style={{ filter }} aria-label="Khung hình đang chỉnh" />
}
