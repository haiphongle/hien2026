import { useEffect, useRef, useState } from 'react'
import { Loader2, Plus, RotateCcw, Scan, Trash2, Upload } from 'lucide-react'
import { fetchAPI } from '../../../api/client'
import { Button } from '../../ui/button'
import { DEFAULT_COLORS } from './editingTypes'
import type { ColorSettings, EditRegion, EditingSettings } from './editingTypes'

interface Props { videoId: string; settings: EditingSettings; duration: number; onChange: (settings: EditingSettings) => void; selectedRegion: string | null; onSelectRegion: (id: string | null) => void }
const names = { blur: 'Làm mờ', color: 'Phủ màu', image: 'Phủ ảnh / logo', delogo: 'Delogo' }

export function ColorRegionPanel({ videoId, settings, duration, onChange, selectedRegion, onSelectRegion }: Props) {
  const [error, setError] = useState('')
  const [uploading, setUploading] = useState('')
  const controller = useRef<AbortController | null>(null)
  const latest = useRef(settings)
  useEffect(() => { latest.current = settings }, [settings])
  useEffect(() => () => controller.current?.abort(), [])
  const colors = settings.colors ?? DEFAULT_COLORS, regions = settings.regions ?? []
  const limit = duration || 8
  const changeColor = (patch: Partial<ColorSettings>) => onChange({ ...settings, colors: { ...colors, ...patch } })
  function changeRegion(id: string, patch: Partial<EditRegion>) {
    onChange({ ...settings, regions: regions.map(r => r.id === id ? { ...r, ...patch } : r) })
  }
  async function upload(regionId: string, file?: File) {
    if (!file) return
    if (file.size > 5 * 1024 * 1024) { setError('Ảnh/logo tối đa 5 MB.'); return }
    controller.current?.abort()
    const request = new AbortController(); controller.current = request
    setUploading(regionId); setError('')
    try {
      const data = new FormData(); data.append('file', file)
      const asset = await fetchAPI<{ id: string }>(`/api/videos/${videoId}/editing/assets`, { method: 'POST', body: data, signal: request.signal })
      if (!request.signal.aborted) {
        const draft = latest.current
        onChange({ ...draft, regions: (draft.regions ?? []).map(r => r.id === regionId ? { ...r, image_id: asset.id } : r) })
      }
    } catch (cause) { if (!request.signal.aborted) setError(String(cause)) }
    finally { if (!request.signal.aborted) setUploading('') }
  }

  return <section className="pp-edit-card">
    <h3>4. Màu sắc & vùng che</h3><p>Màu và vùng làm mờ/phủ cập nhật trực tiếp. Vùng cố định được tính theo khung đầu ra; khi đổi tỷ lệ, kiểm tra lại vị trí vùng.</p>
    <div className="pp-edit-actions">
      <Button variant="outline" onClick={() => onChange({ ...settings, colors: { ...DEFAULT_COLORS } })}><RotateCcw size={14} />Khôi phục màu gốc</Button>
      <Button variant="outline" onClick={() => changeColor({ brightness: .04, contrast: 1.12, saturation: 1.15 })}>Tươi sáng</Button>
      <Button variant="outline" onClick={() => changeColor({ brightness: 0, contrast: 1.1, saturation: .8 })}>Dịu nhẹ</Button>
      <Button variant="outline" onClick={() => changeColor({ brightness: 0, contrast: 1, saturation: 0 })}>Đen trắng</Button>
    </div>
    <div className="pp-edit-grid">{([
      ['brightness', 'Độ sáng', -.5, .5], ['contrast', 'Tương phản', .25, 2], ['saturation', 'Bão hòa', 0, 2],
    ] as const).map(([key, name, min, max]) => <label className="pp-edit-slider" key={key}><span>{name}<output>{key === 'brightness' ? `${Math.round(colors[key] * 100)}%` : `${colors[key].toFixed(2)}×`}</output></span>
      <input type="range" aria-label={name} min={min} max={max} step={.01} value={colors[key]} onChange={e => changeColor({ [key]: Number(e.target.value) })} /></label>)}</div>
    <div className="pp-edit-actions"><Button variant="outline" disabled={regions.length >= 8} onClick={() => {
      const id = crypto.randomUUID()
      onChange({ ...settings, regions: [...regions, { id, kind: 'blur', x: .65, y: .75, width: .25, height: .15, start: 0, end: limit, strength: 12, color: '#000000', opacity: 1, image_id: null }] })
      onSelectRegion(id)
    }}><Plus size={15} />Thêm vùng che ({regions.length}/8)</Button></div>
    {regions.map((r, i) => <div key={r.id} className="pp-region-card" data-selected={selectedRegion === r.id}>
      <div className="pp-region-heading"><strong>Vùng {i + 1}</strong><Button variant="outline" aria-pressed={selectedRegion === r.id} onClick={() => onSelectRegion(selectedRegion === r.id ? null : r.id)}><Scan size={14} />{selectedRegion === r.id ? 'Đang vẽ trên xem trước' : 'Vẽ lại vùng'}</Button>
        <Button variant="outline" aria-label={`Xóa vùng ${i + 1}`} onClick={() => { onChange({ ...settings, regions: regions.filter(item => item.id !== r.id) }); if (selectedRegion === r.id) onSelectRegion(null) }}><Trash2 size={14} /></Button></div>
      {selectedRegion === r.id && <p role="status">Kéo một hình chữ nhật trên khung xem trước bên phải. Xong sẽ tự quay lại chế độ di chuyển video.</p>}
      <div className="pp-edit-grid">
        <label>Hiệu ứng vùng {i + 1}<select value={r.kind} onChange={e => changeRegion(r.id, { kind: e.target.value as EditRegion['kind'] })}>{Object.entries(names).map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></label>
        <label>Bắt đầu vùng {i + 1} (giây)<input type="number" min={0} max={limit - .05} step={.1} value={r.start} onChange={e => { const start = Math.max(0, Math.min(limit - .05, Number(e.target.value))); changeRegion(r.id, { start, end: Math.max(r.end, start + .05) }) }} /></label>
        <label>Kết thúc vùng {i + 1} (giây)<input type="number" min={r.start + .05} max={limit} step={.1} value={r.end} onChange={e => changeRegion(r.id, { end: Math.max(r.start + .05, Math.min(limit, Number(e.target.value))) })} /></label>
        {(['x', 'y', 'width', 'height'] as const).map(key => <label key={key}>{({ x: 'Vị trí ngang', y: 'Vị trí dọc', width: 'Rộng', height: 'Cao' })[key]} vùng {i + 1} (%)<input type="number" min={key === 'width' || key === 'height' ? 1 : 0} max={100} step={1} value={Math.round(r[key] * 100)} onChange={e => {
          const value = Math.max(key === 'width' || key === 'height' ? .01 : 0, Math.min(1, Number(e.target.value) / 100))
          const bound = key === 'x' ? 1 - r.width : key === 'y' ? 1 - r.height : key === 'width' ? 1 - r.x : 1 - r.y
          changeRegion(r.id, { [key]: Math.min(value, bound) })
        }} /></label>)}
        {r.kind === 'blur' && <label>Độ mờ vùng {i + 1}<input type="range" min={1} max={40} value={r.strength} onChange={e => changeRegion(r.id, { strength: Number(e.target.value) })} /></label>}
        {r.kind === 'color' && <label>Màu phủ vùng {i + 1}<input type="color" value={r.color} onInput={e => changeRegion(r.id, { color: e.currentTarget.value })} onChange={() => { /* Live picker. */ }} /></label>}
        {(r.kind === 'color' || r.kind === 'image') && <label>Độ đậm vùng {i + 1}<input type="range" min={0} max={1} step={.01} value={r.opacity} onChange={e => changeRegion(r.id, { opacity: Number(e.target.value) })} /></label>}
      </div>
      {r.kind === 'image' && <label className="pp-upload-region">{uploading === r.id ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}{r.image_id ? 'Đã lưu ảnh/logo · chọn ảnh khác' : 'Tải ảnh/logo PNG, JPG, WebP · tối đa 5 MB'}<input type="file" accept="image/png,image/jpeg,image/webp" disabled={Boolean(uploading)} onChange={e => { void upload(r.id, e.target.files?.[0]); e.target.value = '' }} /></label>}
      {r.kind === 'delogo' && <p>Khung trực tiếp đánh dấu vùng Delogo. Dựng đoạn xem thử bên dưới để kiểm tra nội suy nền thật.</p>}
    </div>)}
    {error && <p role="alert" className="pp-edit-warning">{error}</p>}
  </section>
}
