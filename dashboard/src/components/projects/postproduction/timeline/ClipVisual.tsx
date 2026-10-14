import { useEffect, useRef } from 'react'
import { resolveMediaUrl } from '../../../../api/client'
import type { Clip, Visuals } from './types'
import { clipSilences, envelopeGain } from './audio'

/** Only draw visible pixels; source summaries are reused across every split. */
export function ClipVisual({ clip, visuals, scale, scroll, viewport, audio,threshold=-40,minimum=.35 }: { clip: Clip; visuals?: Visuals; scale: number; scroll: number; viewport: number; audio: boolean;threshold?:number;minimum?:number }) {
  const canvas = useRef<HTMLCanvasElement>(null)
  const x = Math.max(0, scroll - clip.start * scale), width = Math.max(0, Math.min(clip.duration * scale - x, viewport + 100))
  useEffect(() => {
    const element = canvas.current, ctx = element?.getContext('2d')
    if (!element || !ctx || !visuals || !audio || width <= 0) return
    element.width = Math.ceil(width); element.height = 40
    ctx.clearRect(0, 0, width, 40)
    ctx.fillStyle='#c9a55755'
    for(const range of clipSilences(clip,visuals,threshold,minimum)){
      const a=Math.max(0,(range.start-clip.start)*scale-x),b=Math.min(width,(range.end-clip.start)*scale-x)
      if(b>a)ctx.fillRect(a,0,b-a,40)
    }
    for (let px = 0; px < width; px += 3) {
      const local=(x+px)/scale,speed=clip.speed??1
      const a = Math.floor((clip.source_in + local*speed) / visuals.interval)
      const b = Math.max(a + 1, Math.ceil((clip.source_in + (x + px + 3) / scale*speed) / visuals.interval))
      let peak = 0
      for (let i = a; i < b; i++) peak = Math.max(peak, visuals.peaks[i] ?? 0)
      const gain=clip.gain*envelopeGain(clip,local)
      ctx.fillStyle=peak*gain>=.95?'#ff8f92':'#9de2d0'
      const height = Math.min(37, Math.pow(peak * gain, .65) * 37)
      ctx.fillRect(px, 20 - height / 2, 2, Math.max(1, height))
    }
    ctx.beginPath();ctx.strokeStyle='#74efcc';ctx.lineWidth=1
    for(let px=0;px<width;px+=3){const gain=clip.gain*envelopeGain(clip,(x+px)/scale),y=Math.max(0,32-gain*16);if(px===0)ctx.moveTo(px,y);else ctx.lineTo(px,y)}
    ctx.stroke()
  }, [visuals, clip, scale, x, width, audio,threshold,minimum])
  if (!visuals || width <= 0) return null
  if (audio) return <canvas className="tl-wave" ref={canvas} style={{ left: x }} />
  if (!visuals.thumbnail_url || !visuals.thumbnail_interval) return null
  const first = Math.floor(x / 90), count = Math.ceil(width / 90) + 1
  return <div className="tl-thumbnails">{Array.from({ length: count }, (_, i) => {
    const at = (first + i) * 90, source = clip.source_in + at / scale*(clip.speed??1)
    const index = Math.min(visuals.thumbnail_count - 1, Math.floor(source / visuals.thumbnail_interval!))
    return <span key={first + i} style={{ left: at, backgroundImage: `url("${resolveMediaUrl(visuals.thumbnail_url)}")`, backgroundPosition: `${-index * 90}px 0`, backgroundSize: `${visuals.thumbnail_count * 90}px 51px` }} />
  })}</div>
}
