import { useRef, useState } from 'react'
import { fetchAPI, resolveMediaUrl } from '../../api/client'
import { Button } from '../ui/button'
import { imageBase64, type StoryPreset } from './storyStyleLibrary'

export type PreviewPresentation = Pick<StoryPreset, 'sample_prompt' | 'preview' | 'preview_override'>
const control = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

export function StoryPresetPreviewEditor({ projectId, preset, disabled, onSaved, onReference, onClose }: {
  projectId: string; preset: StoryPreset; disabled: boolean
  onSaved: (data: PreviewPresentation) => void; onReference: (image: File) => void; onClose: () => void
}) {
  const [url, setUrl] = useState(''), [busy, setBusy] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const file = useRef<HTMLInputElement>(null), prompt = useRef<HTMLTextAreaElement>(null)
  const blocked = busy || disabled
  const path = `/api/projects/${projectId}/stickman/presets/${preset.id}/preview`

  async function copy() {
    setError(''); setNotice('')
    try {
      await navigator.clipboard.writeText(preset.sample_prompt || '')
      setNotice('Đã sao chép prompt. Tạo ảnh bên ngoài rồi tải ảnh về đây.')
    } catch {
      prompt.current?.focus(); prompt.current?.select()
      setNotice('Đã chọn prompt. Nhấn Cmd+C hoặc Ctrl+C để sao chép.')
    }
  }
  async function upload(image?: File) {
    if (!image || blocked) return
    setBusy(true); setError(''); setNotice('')
    try {
      const image_base64 = await imageBase64(image)
      onSaved(await fetchAPI<PreviewPresentation>(path, { method: 'PUT', body: JSON.stringify({ image_base64 }) }))
      setNotice('Đã lưu ảnh minh họa. Tải lại trang vẫn dùng ảnh này.')
    } catch (cause) { setError(String(cause)) }
    finally { setBusy(false); if (file.current) file.current.value = '' }
  }
  async function importURL() {
    if (blocked || !url.trim()) return
    setBusy(true); setError(''); setNotice('')
    try {
      onSaved(await fetchAPI<PreviewPresentation>(path, { method: 'PUT', body: JSON.stringify({ url: url.trim() }) }))
      setUrl(''); setNotice('Đã tải và lưu ảnh trên máy. Không phụ thuộc thời hạn của URL.')
    } catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }
  async function reset() {
    if (blocked) return
    setBusy(true); setError(''); setNotice('')
    try {
      onSaved({ ...await fetchAPI<PreviewPresentation>(path, { method: 'DELETE' }), preview: undefined })
      setNotice('Đã bỏ ảnh thay thế, dùng lại hình minh họa mặc định.')
    } catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }
  async function createReference() {
    if (blocked || !preset.preview_override || !preset.preview) return
    setBusy(true); setError(''); setNotice('')
    try {
      const response = await fetch(resolveMediaUrl(preset.preview)!)
      if (!response.ok) throw new Error('Không đọc được ảnh đã lưu. Thử tải lại ảnh.')
      const image = new File([await response.blob()], `${preset.name}.webp`, { type: 'image/webp' })
      onReference(image)
    } catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }
  return <section role="dialog" aria-label={`Ảnh và prompt · ${preset.name}`} className="space-y-4 rounded-xl border p-4" style={{ borderColor: 'var(--accent)', background: 'var(--surface)' }}>
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-medium">{preset.name}</h3><p className="mt-1 text-xs" style={{ color: 'var(--muted)' }}>Sao chép prompt → tạo ảnh bên ngoài → thay ảnh minh họa.</p></div><Button type="button" variant="outline" size="sm" disabled={busy} onClick={onClose}>Đóng</Button></div>
    <div className="grid gap-4 lg:grid-cols-[240px_1fr]">
      <div className="space-y-3">
        {preset.preview ? <img src={resolveMediaUrl(preset.preview)} alt={`Ảnh mẫu ${preset.name}`} className="max-h-64 w-full rounded-lg object-contain" style={{ background: preset.background }} /> : <div className="rounded-lg border border-dashed p-6 text-center text-xs" style={{ borderColor: 'var(--border)', color: 'var(--muted)' }}>Chưa có ảnh riêng. Tạo ảnh từ prompt bên cạnh rồi tải lên.</div>}
        <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" aria-label="Chọn ảnh minh họa HTML" className="sr-only" disabled={blocked} onChange={e => void upload(e.target.files?.[0])} />
        <Button type="button" size="sm" disabled={blocked} onClick={() => file.current?.click()}>Tải ảnh từ máy</Button>
        <label className="block text-xs">Hoặc URL ảnh trực tiếp<input type="url" aria-label="URL ảnh minh họa HTML" maxLength={4096} placeholder="https://…/anh.png" disabled={blocked} value={url} onChange={e => setUrl(e.target.value)} className="mt-1 w-full rounded-lg p-2 text-sm" style={control} /></label>
        <Button type="button" size="sm" variant="outline" disabled={blocked || !url.trim()} onClick={() => void importURL()}>Lưu ảnh từ URL</Button>
        <p className="text-xs" style={{ color: 'var(--muted)' }}>PNG/JPEG/WebP · tối đa 10 MB / 16 MP. Ảnh minh họa được dùng chung cho mẫu này trong các dự án trên máy.</p>
        {preset.preview_override && <Button type="button" size="sm" variant="outline" disabled={blocked} onClick={() => void reset()}>Bỏ ảnh thay thế</Button>}
      </div>
      <div className="space-y-3"><p className="text-sm">{preset.description}</p><p className="text-xs" style={{ color: 'var(--muted)' }}>Prompt lấy từ cấu hình gốc của mẫu: tạo hình, nền, nét vẽ và trang phục. Khung 3:2 chỉ dành cho ảnh minh họa; tỷ lệ video chọn riêng ở Studio.</p>
        <textarea ref={prompt} aria-label="Prompt tạo ảnh mẫu HTML" readOnly rows={16} value={preset.sample_prompt || ''} className="w-full rounded-lg p-3 font-mono text-xs" style={control} />
        <Button type="button" size="sm" variant="outline" disabled={blocked || !preset.sample_prompt} onClick={() => void copy()}>Sao chép prompt mẫu</Button>
      </div>
    </div>
    <div className="space-y-2 rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
      <p className="text-xs">Ảnh trên thẻ chỉ minh họa. Thay ảnh không đổi prompt hoặc nhân vật của video đã có.</p>
      <Button type="button" size="sm" variant="outline" disabled={blocked || !preset.preview_override} onClick={() => void createReference()}>Tạo mẫu tham chiếu từ ảnh này</Button>
      <p className="text-xs" style={{ color: 'var(--muted)' }}>Tùy chọn: mở luồng Thêm mẫu để AI phân tích ảnh. Bạn chỉnh mô tả, duyệt nhân vật và cảnh thử rồi lưu vào Mẫu của tôi trước khi sử dụng.</p>
    </div>
    {busy && <p role="status" className="text-xs">Đang xử lý ảnh…</p>}
    {notice && <p role="status" className="text-xs" style={{ color: 'var(--accent)' }}>{notice}</p>}
    {error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
  </section>
}
