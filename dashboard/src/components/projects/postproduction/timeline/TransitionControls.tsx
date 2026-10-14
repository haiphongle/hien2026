import type { Transition } from './types'
import { TRANSITIONS } from './transitions'

export function TransitionControls({ value, duration, disabled, hasAny, change, applyAll, clearAll, preview, close }: {
  value: Transition | null; duration: number; disabled: boolean; hasAny: boolean
  change: (value: Transition | null) => void; applyAll: () => void; clearAll: () => void; preview: () => void; close: () => void
}) {
  return <section className="tl-transition-controls" aria-label="Chuyển cảnh đang chọn">
    <strong>Chuyển cảnh</strong>
    <label>Hiệu ứng<select aria-label="Kiểu chuyển cảnh" disabled={disabled} value={value?.kind ?? ''}
      onChange={e => change(e.target.value ? { kind: e.target.value as Transition['kind'], duration: value?.duration ?? .4 } : null)}>
      <option value="">Không hiệu ứng</option>{TRANSITIONS.map(t => <option key={t.kind} value={t.kind}>{t.name}</option>)}
    </select></label>
    <label>Thời lượng<input aria-label="Thời lượng chuyển cảnh (giây)" type="number" key={value?.duration ?? .4} min={.1} max={2} step={.1} defaultValue={value?.duration ?? .4} disabled={disabled || !value}
      onBlur={e => { const n = Number(e.target.value); if (value && e.target.value.trim() && n !== value.duration) change({ ...value, duration: n }); e.currentTarget.value = String(value?.duration ?? .4) }}
      onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }}/><span>giây</span></label>
    <button disabled={disabled || !value} onClick={applyAll}>Áp dụng cho tất cả cảnh</button>
    <button disabled={disabled || !value} onClick={() => change(null)}>Xóa chuyển cảnh này</button>
    <button disabled={disabled || !hasAny} onClick={clearAll}>Xóa tất cả chuyển cảnh</button>
    <button onClick={preview}>Xem chỗ nối</button><button onClick={close} aria-label="Đóng chọn chuyển cảnh">×</button>
    <small>{value ? <>Hình chuyển trong {duration.toFixed(2)}s{duration < value.duration ? ' (cảnh ngắn tự căn lại)' : ''}.</> : 'Chỗ nối này không có hiệu ứng.'} Giọng, phụ đề và thời lượng được giữ nguyên.</small>
  </section>
}
