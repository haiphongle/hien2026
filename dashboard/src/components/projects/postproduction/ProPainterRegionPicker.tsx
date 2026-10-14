import { useEffect, useRef, useState } from 'react'
import { Loader2, RotateCcw } from 'lucide-react'
import { API_BASE_URL, resolveMediaUrl } from '../../../api/client'
import type { EditingSettings } from './editingTypes'
import { maskFromDrag, type MaskBox } from './propainter'

export function ProPainterRegionPicker({ videoId, settings, sourceUrl, time, box, disabled, onChange, onReady }: {
  videoId: string; settings: EditingSettings; sourceUrl?: string; time: number; box: MaskBox | null; disabled: boolean
  onChange: (box: MaskBox | null) => void; onReady: (ready: boolean) => void
}) {
  const [preview, setPreview] = useState<{ key: string; url: string } | null>(null)
  const [failure, setFailure] = useState<{ key: string; message: string } | null>(null)
  const [retry, setRetry] = useState(0), [drag, setDrag] = useState<MaskBox | null>(null)
  const pointer = useRef<{ x: number; y: number } | null>(null)
  const media = useRef<HTMLVideoElement>(null)
  const [videoKey, setVideoKey] = useState('')
  const key = JSON.stringify({ settings: { ...settings, regions: [], mode: 'original' }, time, text: '', ...(sourceUrl ? { sourceUrl } : {}) })
  const ready = sourceUrl ? videoKey === key : preview?.key === key
  useEffect(() => { onReady(ready) }, [onReady, ready])
  useEffect(() => {
    if (sourceUrl && media.current && media.current.readyState >= 2) media.current.currentTime = time
  }, [sourceUrl, time])
  useEffect(() => {
    if (sourceUrl) return
    const abort = new AbortController()
    let url = ''
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/api/videos/${videoId}/editing/preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: key, signal: abort.signal })
        if (!response.ok) throw new Error(`Không nạp được ảnh xem trước (${response.status}). Thử lại.`)
        const blob = await response.blob()
        if (!abort.signal.aborted) { url = URL.createObjectURL(blob); setPreview({ key, url }); setFailure(null) }
      } catch (cause) { if (!abort.signal.aborted) setFailure({ key, message: String(cause) }) }
    }, 300)
    return () => { clearTimeout(timer); abort.abort(); if (url) URL.revokeObjectURL(url) }
  }, [videoId, key, retry, sourceUrl])
  const shown = drag ?? box
  function point(e: React.PointerEvent<HTMLDivElement>) {
    const bounds = e.currentTarget.getBoundingClientRect()
    return { x: (e.clientX - bounds.left) / bounds.width, y: (e.clientY - bounds.top) / bounds.height }
  }
  return <div className="pp-restore-picker">
    <div className="pp-restore-image" role="group" aria-label="Kéo chuột khoanh vùng cần xóa" aria-disabled={disabled || !ready}
      onPointerDown={e => {
        if (disabled || !ready || e.button !== 0) return
        e.currentTarget.setPointerCapture(e.pointerId); pointer.current = point(e); setDrag(null)
      }}
      onPointerMove={e => { if (pointer.current) setDrag(maskFromDrag(pointer.current, point(e))) }}
      onPointerUp={e => { if (pointer.current) onChange(maskFromDrag(pointer.current, point(e))); pointer.current = null; setDrag(null) }}
      onPointerCancel={() => { pointer.current = null; setDrag(null) }}>
      {sourceUrl ? <video ref={media} key={`${sourceUrl}:${retry}`} src={resolveMediaUrl(sourceUrl)} muted playsInline preload="auto" aria-label={`Khung nguồn tại ${time.toFixed(2)} giây`}
        onLoadedData={e => { e.currentTarget.currentTime = time; if (Math.abs(e.currentTarget.currentTime-time) < .04 && time === 0) setVideoKey(key) }}
        onSeeked={e => { if (Math.abs(e.currentTarget.currentTime-time) < .05) { setVideoKey(key); setFailure(null) } }}
        onError={() => setFailure({ key, message: 'Không đọc được video nguồn. Nạp lại tư liệu.' })} />
        : ready && preview ? <img src={preview.url} alt={`Khung hình tại ${time.toFixed(2)} giây để chọn vật thể`} draggable={false} /> : null}
      {!ready && <div className="pp-restore-placeholder"><Loader2 size={20} className="animate-spin" />Đang nạp khung hình…</div>}
      {ready && shown && <div className="pp-restore-mask" style={{ left: `${shown.x * 100}%`, top: `${shown.y * 100}%`, width: `${shown.width * 100}%`, height: `${shown.height * 100}%` }} />}
    </div>
    {failure?.key === key && <p role="alert">{failure.message} <button onClick={() => setRetry(v => v + 1)}>Nạp lại ảnh</button></p>}
    <div className="pp-edit-actions"><button disabled={disabled || !box} onClick={() => onChange(null)}><RotateCcw size={13} />Xóa vùng để vẽ lại</button><span>{box ? `Đã khoanh ${(box.width * box.height * 100).toFixed(1)}% khung hình` : 'Chưa chọn vùng'}</span></div>
  </div>
}
