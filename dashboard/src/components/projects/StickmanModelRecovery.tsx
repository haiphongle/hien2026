import { useState } from 'react'
import { Button } from '../ui/button'

export interface VideoModelOption {
  id: string; name: string; duration_seconds?: number; input?: 'first_frame'; orientations?: string[]
  camera_control?: 'prompt'; end_frame?: boolean; access?: 'unverified'; credits?: number | null
}
export interface ModelRecovery {
  current_model: string; retry_model: string; auto_switch: boolean; models: VideoModelOption[]
}

export function StickmanModelRecovery({ recovery, disabled, onRetry }: {
  recovery: ModelRecovery; disabled: boolean; onRetry: (model?: string) => void
}) {
  const [choice, setChoice] = useState('auto')
  const target = choice === 'auto' ? recovery.retry_model : choice
  const name = recovery.models.find(m => m.id === target)?.name || target
  const changing = target !== recovery.current_model || choice === 'auto' && recovery.auto_switch
  return <div className="space-y-3 rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
    <label className="block text-xs">Model khi thử lại
      <select aria-label="Model khi thử lại" disabled={disabled} value={choice} onChange={e => setChoice(e.target.value)} className="mt-1 w-full rounded p-2 text-sm" style={{ background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }}>
        <option value="auto">{recovery.auto_switch ? 'Tự chọn model thay thế' : 'Giữ model hiện tại'} · {recovery.models.find(m => m.id === recovery.retry_model)?.name}</option>
        {recovery.models.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
      </select>
    </label>
    <p role="status" className="text-sm">{changing ? `Thử lại sẽ dùng ${name} cho clip còn thiếu.` : `Tiếp tục bằng ${name}.`}</p>
    <p className="text-xs" style={{ color: 'var(--muted)' }}>Giữ ảnh, giọng và clip hoàn tất. Model mới có thể dùng tín dụng; quyền truy cập và mức phí do Google Flow quyết định.</p>
    <Button type="button" disabled={disabled} onClick={() => onRetry(choice === 'auto' ? undefined : choice)}>{disabled ? 'Đang gửi yêu cầu…' : changing ? 'Thử lại · đổi model' : 'Tiếp tục phần còn thiếu'}</Button>
  </div>
}
