import { useEffect, useRef, useState } from 'react'
import type { Clip, VideoOverlay } from './types'
import { entranceOpacity, moveOverlay, overlayGeometry, resizeOverlay } from './videoOverlay'

type Gesture = { x: number; y: number; width: number; height: number; mode: string; value: VideoOverlay }
export function VideoOverlayPreview({ clip, media, width, height, time, playing, selected, locked, onSelect, onChange }: {
  clip: Clip; media: HTMLVideoElement | null; width: number; height: number; time: number; playing: boolean;
  selected: boolean; locked: boolean; onSelect: () => void; onChange: (value: VideoOverlay) => void
}) {
  const canvas = useRef<HTMLCanvasElement>(null), root = useRef<HTMLDivElement>(null), gesture = useRef<Gesture | null>(null)
  const [draft, setDraft] = useState<VideoOverlay | null>(null)
  const value = draft ?? clip.overlay!, box = value, geometry = overlayGeometry(value,width,height)
  const pausedTime = playing ? null : time
  useEffect(() => {
    const surface = canvas.current
    if (!surface || !media) return
    const w = geometry.width, h = geometry.height
    const scale = Math.min(1, 960/Math.max(w,h))
    surface.width = Math.round(w*scale); surface.height = Math.round(h*scale)
    const ctx = surface.getContext('2d')
    const shape = box.layout === 'split' ? 'rectangle' : box.shape ?? 'rectangle'
    function outline(ctx: CanvasRenderingContext2D, inset = 0) {
      ctx.beginPath()
      if (shape === 'circle') ctx.arc(w/2,h/2,Math.max(0,Math.min(w,h)/2-inset),0,Math.PI*2)
      else ctx.roundRect(inset,inset,Math.max(0,w-2*inset),Math.max(0,h-2*inset),shape==='rounded'?Math.max(0,Math.min(w,h)*(box.radius??12)/100-inset):0)
    }
    let request: number | null = null
    function draw() {
      request = null
      if (!ctx || !media || !surface) return
      ctx.setTransform(scale,0,0,scale,0,0); ctx.clearRect(0,0,w,h)
      ctx.save(); outline(ctx); ctx.clip(); ctx.fillStyle = '#000'; ctx.fillRect(0,0,w,h)
      if (media.readyState >= 2 && media.videoWidth) {
        const ratio = (box.fit === 'cover' ? Math.max : Math.min)(w/media.videoWidth,h/media.videoHeight)
        const dw = media.videoWidth*ratio, dh = media.videoHeight*ratio
        ctx.save()
        if (box.flip) { ctx.translate(w,0); ctx.scale(-1,1) }
        ctx.drawImage(media,(w-dw)/2,(h-dh)/2,dw,dh); ctx.restore()
      }
      ctx.restore()
      const border = Math.min(Math.round(box.border*height/100),w/2,h/2)
      if (border) { ctx.strokeStyle = box.border_color; ctx.lineWidth = border; outline(ctx,border/2); ctx.stroke() }
      if (playing) schedule()
    }
    function schedule() { if (request === null) request = requestAnimationFrame(draw) }
    schedule(); media.addEventListener('loadeddata',schedule); media.addEventListener('seeked',schedule)
    return () => { if (request !== null) cancelAnimationFrame(request); media.removeEventListener('loadeddata',schedule); media.removeEventListener('seeked',schedule) }
  }, [media, geometry.width, geometry.height, height, box.layout, box.shape, box.radius, box.fit, box.flip, box.border, box.border_color, playing, pausedTime])
  function begin(e: React.PointerEvent<HTMLButtonElement>, mode: string) {
    e.stopPropagation(); e.preventDefault(); onSelect()
    if (locked || value.layout === 'split' || e.button !== 0) return
    const rect = root.current!.parentElement!.getBoundingClientRect()
    gesture.current = { x: e.clientX, y: e.clientY, width: rect.width, height: rect.height, mode, value }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  function calculate(e: React.PointerEvent, g: Gesture) {
    const dx = (e.clientX-g.x)/g.width, dy = (e.clientY-g.y)/g.height
    return g.mode === 'move' ? moveOverlay(g.value,dx,dy) : resizeOverlay(g.value,g.mode,dx,dy)
  }
  function finish(e: React.PointerEvent<HTMLButtonElement>, cancel = false) {
    const g = gesture.current
    if (!g) return
    gesture.current = null; setDraft(null)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    if (!cancel && Math.hypot(e.clientX-g.x,e.clientY-g.y) >= 2) onChange(calculate(e,g))
  }
  const handlers = { onPointerMove: (e: React.PointerEvent<HTMLButtonElement>) => { if (gesture.current) setDraft(calculate(e,gesture.current)) }, onPointerUp: (e: React.PointerEvent<HTMLButtonElement>) => finish(e), onPointerCancel: (e: React.PointerEvent<HTMLButtonElement>) => finish(e,true), onLostPointerCapture: (e: React.PointerEvent<HTMLButtonElement>) => finish(e,true) }
  return <div ref={root} className={`tl-pip ${selected ? 'selected' : ''}`} style={{ left: `${geometry.x/width*100}%`, top: `${geometry.y/height*100}%`, width: `${geometry.width/width*100}%`, height: `${geometry.height/height*100}%` }}>
    <canvas ref={canvas} aria-label="Hình video chèn" style={{opacity:entranceOpacity({...clip,overlay:value},time)}} />
    <button className="tl-pip-move" aria-label="Chọn và kéo khung video chèn" onClick={onSelect} onPointerDown={e=>begin(e,'move')} {...handlers}/>
    {selected && !locked && value.layout !== 'split' && ['nw','ne','sw','se'].map(corner=><button key={corner} className={`tl-pip-handle ${corner}`} aria-label={`Đổi kích thước khung ${corner}`} onPointerDown={e=>begin(e,corner)} {...handlers}/>)}
    {selected && <span className="tl-pip-label">Video chèn</span>}
  </div>
}
