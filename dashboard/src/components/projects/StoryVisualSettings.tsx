import { Button } from '../ui/button'
import { defaultVisualLocks, visualChoices, type VisualLocks } from './stickmanOptions'

const labels = { anatomy: 'Hình thể', background: 'Phông nền', rendering: 'Nét vẽ', costume: 'Trang phục chung' }
const control = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

export function StoryVisualSettings({ value, disabled, onChange }: {
  value: VisualLocks; disabled: boolean; onChange: (value: VisualLocks) => void
}) {
  const changed = JSON.stringify(value) !== JSON.stringify(defaultVisualLocks)
  return <details className="rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
    <summary className="cursor-pointer text-sm font-medium">Tinh chỉnh phong cách & bối cảnh {changed && '· Đã tùy chỉnh'}</summary>
    <fieldset disabled={disabled} className="mt-3 space-y-3">
      <p className="text-xs" style={{ color: 'var(--muted)' }}>Để “Theo mẫu” nếu muốn giữ phong cách đã chọn. Trang phục riêng của A/B được ưu tiên; ảnh đã chọn vẫn giữ tạo hình gốc.</p>
      <div className="grid gap-3 sm:grid-cols-2">{(Object.keys(labels) as (keyof typeof labels)[]).map(key =>
        <label key={key} className="block text-xs">{labels[key]}<select aria-label={labels[key]} value={value[key]} onChange={e => onChange({ ...value, [key]: e.target.value })} className="mt-1 w-full rounded-lg p-2" style={control}>
          {Object.entries(visualChoices[key]).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </select></label>)}
      </div>
      <label className="block text-xs">Ghi chú phong cách<textarea aria-label="Ghi chú phong cách" rows={2} maxLength={500} value={value.notes} onChange={e => onChange({ ...value, notes: e.target.value })} placeholder="Ví dụ: Nét mềm, ánh sáng ấm, tránh tay chân như lắp ghép…" className="mt-1 w-full rounded-lg p-2" style={control} /></label>
      <label className="block text-xs">Bối cảnh câu chuyện<textarea aria-label="Bối cảnh câu chuyện" rows={2} maxLength={700} value={value.context} onChange={e => onChange({ ...value, context: e.target.value })} placeholder="Ví dụ: Gia đình Việt hiện đại; các cảnh nối tiếp trong cùng một căn nhà." className="mt-1 w-full rounded-lg p-2" style={control} /></label>
      <Button type="button" size="sm" variant="outline" disabled={!changed || disabled} onClick={() => onChange({ ...defaultVisualLocks })}>Về phong cách mặc định</Button>
    </fieldset>
  </details>
}
