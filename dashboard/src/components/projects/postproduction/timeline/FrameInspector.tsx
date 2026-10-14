import { DEFAULT_FRAME, outputSize, type FrameSettings } from '../editingTypes'
import type { Document } from './types'

export function FrameInspector({ doc, onChange }: { doc: Document; onChange: (frame: FrameSettings | null) => void }) {
  const frame = doc.frame ?? DEFAULT_FRAME
  const [width, height] = outputSize(frame, { width: doc.width, height: doc.height, duration: 0 })!
  const patch = (changes: Partial<FrameSettings>) => onChange({ ...frame, ...changes })
  const slider = (label: string, key: 'zoom' | 'x' | 'y', min: number, max: number) => <label className="tl-field"><span>{label} · {frame[key].toFixed(2)}</span><input aria-label={label} type="range" min={min} max={max} step={.01} value={frame[key]} onChange={e => patch({ [key]: Number(e.target.value) })} /></label>
  return <div className="tl-frame-settings">
    <label className="tl-field"><span>Tỷ lệ đầu ra</span><select aria-label="Tỷ lệ đầu ra" value={frame.preset} onChange={e => patch({ preset: e.target.value as FrameSettings['preset'] })}>
      <option value="source">Giữ tỷ lệ nguồn</option><option value="9:16">9:16 · TikTok / Reels / Shorts</option><option value="16:9">16:9 · YouTube / máy tính</option><option value="1:1">1:1 · Vuông</option><option value="4:5">4:5 · Bài đăng</option><option value="custom">Kích thước tùy chỉnh</option>
    </select></label>
    {frame.preset === 'custom' && (['width', 'height'] as const).map(key => <label key={key} className="tl-field"><span>{key === 'width' ? 'Chiều rộng' : 'Chiều cao'} (px, số chẵn)</span><input aria-label={key === 'width' ? 'Chiều rộng đầu ra' : 'Chiều cao đầu ra'} type="number" key={frame[key]} defaultValue={frame[key]} min={240} max={3840} step={2} onBlur={e => { const value = Number(e.target.value); if (Number.isFinite(value) && value >= 240 && value <= 3840 && value % 2 === 0 && value * frame[key === 'width' ? 'height' : 'width'] <= 3840 * 2160) patch({ [key]: value }); else e.currentTarget.value = String(frame[key]) }} onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }} /></label>)}
    <p className="tl-help">{width} × {height} px · Áp dụng cho cả bản dựng, xem trước và bản xuất.</p>
    <label className="tl-field"><span>Cách đặt hình</span><select aria-label="Cách đặt hình" value={frame.fit} onChange={e => patch({ fit: e.target.value as FrameSettings['fit'] })}><option value="contain">Giữ đủ hình</option><option value="cover">Lấp đầy, cắt mép</option></select></label>
    <label className="tl-field"><span>Nền khung hình</span><select aria-label="Nền khung hình" value={frame.background} onChange={e => patch({ background: e.target.value as FrameSettings['background'] })}><option value="color">Màu nền</option><option value="blur">Nền mờ từ video</option></select></label>
    {frame.background === 'color' && <label className="tl-field"><span>Màu nền khung hình</span><input aria-label="Màu nền khung hình" type="color" value={frame.color} onChange={e => patch({ color: e.target.value })} /></label>}
    {slider('Zoom hình', 'zoom', 1, 3)}{slider('Căn hình ngang', 'x', 0, 1)}{slider('Căn hình dọc', 'y', 0, 1)}
    <button onClick={() => onChange(null)}>Khung hình mặc định</button>
    <p className="tl-help">Chữ và hiệu ứng: chọn khối tương ứng trên timeline. Tắt lớp Phụ đề để xuất chỉ giọng đọc; tắt lớp Giọng để chỉ giữ chữ.</p>
  </div>
}
