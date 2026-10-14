import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchAPI } from '../../api/client'
import type { Character, Orientation, ProvidersResponse, RoleAssignment } from '../../types'
import { Button } from '../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/dialog'

export interface PromptPair { image_prompt: string; video_prompt: string }
interface Props {
  projectId: string; orientation: Orientation; material: string; characters: Character[]
  scene: { action: string; character_ids: string[]; speaker_id: string; dialogue: string }
  onClose: () => void; onApply: (pair: PromptPair) => void
}
const control = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }
function message(cause: unknown) {
  const raw = cause instanceof Error ? cause.message : String(cause)
  try { const detail = JSON.parse(raw.replace(/^API \d+: /, '')).detail; return typeof detail === 'string' ? detail : raw }
  catch { return raw }
}

export default function PromptSuggestionDialog({ projectId, orientation, material, characters, scene, onClose, onApply }: Props) {
  const [role, setRole] = useState<RoleAssignment | null>(null)
  const [installed, setInstalled] = useState(false)
  const [includeImages, setIncludeImages] = useState(false)
  const [result, setResult] = useState<PromptPair | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const request = useRef<AbortController | null>(null)
  const selected = characters.filter(c => scene.character_ids.includes(c.id))

  useEffect(() => {
    const controller = new AbortController()
    fetchAPI<ProvidersResponse>('/api/providers', { signal: controller.signal }).then(data => {
      const assignment = data.roles.prompt_suggestion
      if (!assignment) throw new Error('Backend chưa có Gợi ý prompt. Cần khởi động lại FrameWeaver.')
      setRole(assignment); setInstalled(Boolean(data.providers[assignment.provider]?.installed))
    }).catch(cause => { if (!controller.signal.aborted) setError(message(cause)) })
    return () => { controller.abort(); request.current?.abort() }
  }, [])
  useEffect(() => {
    if (!pending) return
    const started = Date.now()
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [pending])

  async function suggest() {
    if (!role || pending) return
    const controller = new AbortController()
    request.current = controller
    setPending(true); setError(''); setResult(null); setElapsed(0)
    try {
      const answer = await fetchAPI<PromptPair>(`/api/projects/${projectId}/suggest-prompts`, {
        method: 'POST', signal: controller.signal,
        body: JSON.stringify({ ...scene, speaker_id: scene.speaker_id || null, orientation, material,
          include_images: includeImages, expected_role: role }),
      })
      setResult({ image_prompt: answer.image_prompt, video_prompt: answer.video_prompt })
    } catch (cause) { if (!controller.signal.aborted) setError(message(cause)) }
    finally { if (!controller.signal.aborted) setPending(false) }
  }

  return <Dialog open onOpenChange={open => { if (!open && !pending) onClose() }}>
    <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl" showCloseButton={!pending}>
      <DialogHeader>
        <DialogTitle>Gợi ý prompt cho cảnh · 8 giây</DialogTitle>
        <DialogDescription>Xem và sửa prompt trước khi áp dụng. Chỉ tạo ảnh/video khi bạn bấm Tạo và ghép video trong Studio.</DialogDescription>
      </DialogHeader>
      <div className="rounded-lg border p-3 text-sm" style={{ borderColor: 'var(--border)' }}>
        <p>{scene.action}</p>
        <p className="mt-2 text-xs" style={{ color: 'var(--muted)' }}>Tham chiếu: {selected.map(c => c.name).join(', ') || 'Chưa chọn'} · {orientation === 'VERTICAL' ? 'Dọc' : 'Ngang'}</p>
        {scene.dialogue && <p className="mt-1 text-xs">Lời thoại: “{scene.dialogue}”</p>}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <span>AI: <strong>{role ? `${role.provider} · ${role.model || 'model mặc định'} · ${role.effort || 'mức suy luận mặc định'}` : 'Đang đọc cấu hình…'}</strong></span>
        {!pending && <Link className="underline" to="/settings">Đổi AI / model</Link>}
      </div>
      <p className="text-xs" style={{ color: 'var(--muted)' }}>Gửi hành động, tên, mô tả tham chiếu và lời thoại tới AI đã chọn. Yêu cầu này dùng hạn mức tài khoản CLI.</p>
      {selected.length > 0 && <label className="flex items-start gap-2 text-xs">
        <input type="checkbox" checked={includeImages} disabled={pending} onChange={e => { setIncludeImages(e.target.checked); setResult(null); setError('') }} />
        <span>Gửi thêm {selected.length} ảnh tham chiếu đã chọn để {role?.provider || 'AI'} phân tích bố cục. Mặc định chỉ gửi mô tả chữ.</span>
      </label>}
      {role && !installed && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>Chưa tìm thấy CLI {role.provider}. Chọn AI sẵn sàng trong Cài đặt.</p>}
      {error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
      {pending && <p role="status" className="text-sm animate-pulse">Đang chờ AI gợi ý · {elapsed}s / tối đa 240s</p>}
      {result && <div className="grid gap-4">
        <label className="grid gap-1.5 text-xs">Prompt ảnh · hành động và bố cục
          <textarea rows={3} maxLength={3000} value={result.image_prompt} onChange={e => setResult({ ...result, image_prompt: e.target.value })} className="rounded-md p-3 text-sm" style={control} />
        </label>
        <label className="grid gap-1.5 text-xs">Prompt video · 0–3s, 3–6s, 6–8s
          <textarea rows={5} maxLength={4000} value={result.video_prompt} onChange={e => setResult({ ...result, video_prompt: e.target.value })} className="rounded-md p-3 text-sm" style={control} />
        </label>
        <p className="text-xs" style={{ color: 'var(--muted)' }}>Kiểm tra hành động và lời thoại. Ảnh tham chiếu giữ ngoại hình; prompt không cần mô tả lại khuôn mặt hay trang phục.</p>
      </div>}
      <div className="flex flex-wrap justify-end gap-2">
        <Button type="button" variant="ghost" disabled={pending} onClick={onClose}>Đóng</Button>
        <Button type="button" variant="outline" disabled={!role || !installed || pending} onClick={suggest}>{result ? 'Gợi ý lại' : 'Tạo gợi ý'}</Button>
        <Button type="button" disabled={pending || !result || result.image_prompt.trim().length < 3 || result.video_prompt.trim().length < 3} onClick={() => { if (result) onApply(result) }}>Áp dụng cho cảnh</Button>
      </div>
    </DialogContent>
  </Dialog>
}
