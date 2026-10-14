import { useRef, useState } from 'react'
import { fetchAPI, resolveMediaUrl } from '../../api/client'
import { usePolling } from '../../api/usePolling'
import type { Character } from '../../types'
import { Button } from '../ui/button'
import { imageBase64, sampleAction, styleCategories, styleSettings, type StyleProfile, type StyleCategory } from './storyStyleLibrary'

interface Proof { signature: string; character_id: string; character_url: string; scene_url: string }
interface Job { id: string; status: string; progress: number; stage: string; error?: string; result?: Proof }
const control = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

export function StoryStyleEditor({ projectId, initial, sourceImage, characters, onDraft, onSaved, onClose, onCharacter }: {
  projectId: string; initial: StyleProfile | null; sourceImage?: File; characters: Character[]
  onDraft: (style: StyleProfile) => void; onSaved: (style: StyleProfile) => void; onClose: () => void
  onCharacter: (character: Character) => void
}) {
  const [form, setForm] = useState<StyleProfile | null>(initial), [saved, setSaved] = useState<StyleProfile | null>(initial)
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [proof, setProof] = useState<Proof | null>(null)
  const [job, setJob] = useState<Job | null>(null), [approved, setApproved] = useState(false), [useCharacter, setUseCharacter] = useState(true)
  const file = useRef<HTMLInputElement>(null)
  const active = Boolean(job && ['QUEUED', 'PROCESSING'].includes(job.status))
  const dirty = Boolean(form && saved && JSON.stringify(styleSettings(form)) !== JSON.stringify(styleSettings(saved)))
  const base = form ? `/api/projects/${projectId}/stickman/styles/${form.id}` : ''
  usePolling<{ job: Job | null; proof: Proof | null }>(form ? `${base}/preview` : null, active ? 2500 : 10000, result => {
    setJob(result.job)
    if (!dirty) setProof(result.proof)
  }, cause => setError(String(cause)))

  function patch(changes: Partial<StyleProfile>) {
    setForm(value => value && { ...value, ...changes }); setApproved(false); setProof(null); setError('')
  }
  async function upload(image?: File) {
    if (!image || busy) return
    setBusy(true); setError('')
    try {
      const image_base64 = await imageBase64(image)
      const name = image.name.replace(/\.[^.]+$/, '').slice(0, 80).trim() || 'Mẫu phong cách'
      const result = await fetchAPI<StyleProfile>(`/api/projects/${projectId}/stickman/styles`, {
        method: 'POST', body: JSON.stringify({ name: name.length < 3 ? `Mẫu ${name}` : name, image_base64, draft: true }) })
      setForm(result); setSaved(result); onDraft(result)
    } catch (cause) { setError(String(cause)) }
    finally { setBusy(false); if (file.current) file.current.value = '' }
  }
  async function preview() {
    if (!form || busy || active) return
    setBusy(true); setError(''); setApproved(false)
    try {
      const result = await fetchAPI<StyleProfile>(base, { method: 'PATCH', body: JSON.stringify(styleSettings(form)) })
      setForm(result); setSaved(result); onDraft(result)
      const next = await fetchAPI<Job>(`${base}/preview`, { method: 'POST' })
      setJob(next); setProof(next.result || null)
    } catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }
  async function publish() {
    if (!proof || !approved || dirty || busy || active) return
    setBusy(true); setError('')
    try {
      const reference = useCharacter ? await fetchAPI<Character>(`/api/characters/${proof.character_id}`) : null
      const result = await fetchAPI<StyleProfile>(`${base}/publish`, { method: 'POST',
        body: JSON.stringify({ preview_signature: proof.signature, use_preview_character: useCharacter }) })
      if (reference) onCharacter(reference)
      onSaved(result)
    } catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }
  async function stop() {
    if (!job?.id || busy) return
    setBusy(true)
    try { setJob(await fetchAPI<Job>(`${base}/cancel/${job.id}`, { method: 'POST' })) }
    catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }
  async function close() {
    if (busy) return
    if (!form || !dirty) { onClose(); return }
    setBusy(true); setError('')
    try {
      onDraft(await fetchAPI<StyleProfile>(base, { method: 'PATCH', body: JSON.stringify(styleSettings(form)) }))
      onClose()
    } catch (cause) { setError(String(cause)) }
    finally { setBusy(false) }
  }
  return <section role="dialog" aria-label="Thêm mẫu phong cách" className="space-y-4 rounded-xl border p-4" style={{ borderColor: 'var(--accent)', background: 'var(--surface)' }}>
    <div className="flex items-center justify-between gap-3"><div><h3 className="font-medium">Thêm mẫu phong cách</h3><p className="mt-1 text-xs" style={{ color: 'var(--muted)' }}>1. Tải ảnh → 2. Chỉnh mô tả → 3. Duyệt & lưu</p></div><Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void close()}>{form ? 'Đóng · giữ bản nháp' : 'Đóng'}</Button></div>
    {!form ? <div className="rounded-lg border border-dashed p-6 text-center" style={{ borderColor: 'var(--border)' }}>
      <p className="mb-3 text-sm">Chọn riêng phần tranh thể hiện phong cách bạn muốn.</p>
      <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" aria-label="Tải ảnh mẫu phong cách" disabled={busy} onChange={e => void upload(e.target.files?.[0])} />
      {sourceImage && <div className="mt-3 space-y-2"><p className="text-xs">Ảnh đã chọn: {sourceImage.name}</p><Button type="button" size="sm" disabled={busy} onClick={() => void upload(sourceImage)}>Phân tích ảnh này</Button></div>}
      <p className="mt-3 text-xs" style={{ color: 'var(--muted)' }}>{busy ? 'AI đang phân tích và lưu bản nháp…' : 'PNG/JPEG/WebP · tối đa 10 MB. Kết quả phân tích được lưu để dùng lại.'}</p>
    </div> : <>
      <fieldset disabled={busy || active} className="space-y-3">
        <div className="grid gap-4 sm:grid-cols-[140px_1fr]">
          <img src={resolveMediaUrl(form.image_url)} alt="Ảnh phong cách gốc" className="h-36 w-full rounded-lg object-contain" />
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="text-xs">Tên mẫu<input aria-label="Tên mẫu phong cách" maxLength={80} value={form.name} onChange={e => patch({ name: e.target.value })} className="mt-1 w-full rounded p-2 text-sm" style={control} /></label>
            <label className="text-xs">Nhóm<select aria-label="Nhóm mẫu mới" value={form.category || 'drawing'} onChange={e => patch({ category: e.target.value as StyleCategory })} className="mt-1 w-full rounded p-2 text-sm" style={control}>{Object.entries(styleCategories).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
            <label className="text-xs sm:col-span-2">Mô tả ngắn<input aria-label="Mô tả mẫu phong cách" maxLength={300} value={form.description || ''} onChange={e => patch({ description: e.target.value })} className="mt-1 w-full rounded p-2 text-sm" style={control} /></label>
          </div>
        </div>
        <label className="block text-xs">Phong cách AI nhận diện · có thể chỉnh<textarea aria-label="Mô tả phong cách AI" rows={3} maxLength={1400} value={form.style_instruction} onChange={e => patch({ style_instruction: e.target.value })} className="mt-1 w-full rounded p-2 text-sm" style={control} /></label>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs">Nhân vật mẫu<select aria-label="Nhân vật mặc định cho mẫu" value={form.default_character_id || ''} onChange={e => patch({ default_character_id: e.target.value || null })} className="mt-1 w-full rounded p-2 text-sm" style={control}><option value="">Tạo nhân vật theo phong cách</option>{characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          <label className="text-xs">Khung cảnh mẫu<select aria-label="Khung hình mẫu phong cách" value={form.orientation || 'VERTICAL'} onChange={e => patch({ orientation: e.target.value as StyleProfile['orientation'] })} className="mt-1 w-full rounded p-2 text-sm" style={control}><option value="VERTICAL">Dọc · 9:16</option><option value="HORIZONTAL">Ngang · 16:9</option></select></label>
        </div>
        <details><summary className="cursor-pointer text-xs">Tạo hình nhân vật, cảnh thử và ràng buộc</summary><div className="mt-3 space-y-3">
          <label className="block text-xs">Tạo hình nhân vật<textarea aria-label="Tạo hình nhân vật mẫu" rows={2} maxLength={700} value={form.character_design} onChange={e => patch({ character_design: e.target.value })} className="mt-1 w-full rounded p-2 text-sm" style={control} /></label>
          <label className="block text-xs">Hành động và bối cảnh thử<textarea aria-label="Hành động cảnh mẫu" rows={2} maxLength={1000} value={form.scene_action || sampleAction} onChange={e => patch({ scene_action: e.target.value })} className="mt-1 w-full rounded p-2 text-sm" style={control} /></label>
          <label className="block text-xs">Những thứ cần tránh<textarea aria-label="Ràng buộc phong cách" rows={2} maxLength={400} value={form.negative_prompt} onChange={e => patch({ negative_prompt: e.target.value })} className="mt-1 w-full rounded p-2 text-sm" style={control} /></label>
        </div></details>
      </fieldset>
      <div className="flex flex-wrap items-center gap-3"><Button type="button" disabled={busy || active || form.name.trim().length < 3 || form.style_instruction.trim().length < 10} onClick={() => void preview()}>{busy ? 'Đang xử lý…' : proof && !dirty ? 'Dùng lại ảnh mẫu đã lưu' : 'Tạo ảnh nhân vật & cảnh mẫu'}</Button><p className="text-xs" style={{ color: 'var(--muted)' }}>Lưu mô tả trước khi tạo. Tối đa 1 ảnh nhân vật + 1 ảnh cảnh qua Google Flow; dùng lại phần đã có.</p></div>
      {active && <div role="status" className="space-y-2"><p className="text-xs">{job?.stage} · {job?.progress}%</p><progress className="w-full" value={job?.progress} max={100} /><Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void stop()}>Dừng tạo ảnh mẫu</Button></div>}
      {job?.error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{job.error}</p>}
      {proof && !dirty && <div className="space-y-3 border-t pt-4" style={{ borderColor: 'var(--border)' }}>
        <div className="grid gap-3 sm:grid-cols-2">{(['character', 'scene'] as const).map(kind => <figure key={kind}><img src={resolveMediaUrl(proof[`${kind}_url`])} alt={kind === 'character' ? 'Nhân vật cần duyệt' : 'Cảnh mẫu cần duyệt'} className="h-64 w-full rounded-lg object-contain" /><figcaption className="mt-2 text-center text-xs">{kind === 'character' ? 'Nhân vật mẫu' : 'Một cảnh thử phong cách'}</figcaption></figure>)}</div>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={useCharacter} onChange={e => setUseCharacter(e.target.checked)} />Dùng nhân vật này làm mặc định cho mẫu</label>
        <label className="flex items-center gap-2 text-sm"><input aria-label="Duyệt nhân vật và cảnh mẫu" type="checkbox" checked={approved} onChange={e => setApproved(e.target.checked)} />Nhân vật và cảnh mẫu đúng phong cách tôi muốn</label>
        <Button type="button" disabled={!approved || busy || active} onClick={() => void publish()}>Lưu vào Mẫu của tôi</Button>
      </div>}
    </>}
    {error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
  </section>
}
