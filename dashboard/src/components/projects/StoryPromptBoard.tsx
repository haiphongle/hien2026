import { useEffect, useState } from 'react'
import { fetchAPI } from '../../api/client'
import { Button } from '../ui/button'
import type { StoryAnalysis, StoryCharacter } from './stickmanOptions'

interface PromptReference { role: string; name: string; media_id: string | null }
interface PromptScene { scene: number; narration: string; cast: string[]; image_prompt: string; video_prompt: string; references: PromptReference[] }
interface PromptBundle {
  style: { name: string }; orientation: string; motion: string
  analysis: StoryAnalysis; character_bible: StoryCharacter[]
  characters: { role: string; name: string; reference_id: string | null; media_id: string | null; prompt: string }[]
  scenes: PromptScene[]
}
const control = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

export function StoryPromptBoard({ projectId, request, title, disabled, contextKey }: {
  projectId: string; request: string; title: string; disabled: boolean; contextKey: string
}) {
  const [open, setOpen] = useState(false), [retry, setRetry] = useState(0), [notice, setNotice] = useState('')
  const [state, setState] = useState<{ key: string; result?: PromptBundle; error?: string } | null>(null)
  const key = JSON.stringify([projectId, request, contextKey, retry])
  const result = state?.key === key ? state.result : undefined
  const error = state?.key === key ? state.error : undefined
  useEffect(() => {
    if (!open) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      fetchAPI<PromptBundle>(`/api/projects/${projectId}/stickman/prompts`, { method: 'POST', body: request, signal: controller.signal })
        .then(result => { if (!controller.signal.aborted) setState({ key, result }) })
        .catch(cause => { if (!controller.signal.aborted) setState({ key, error: String(cause) }) })
    }, 300)
    return () => { clearTimeout(timer); controller.abort() }
  }, [open, key, projectId, request])

  async function copy(value: string) {
    try { await navigator.clipboard.writeText(value); setNotice('Đã sao chép prompt.') }
    catch { setNotice('Không sao chép được. Bạn có thể chọn nội dung trong ô hoặc tải file TXT.') }
  }
  function download(format: 'txt' | 'json') {
    if (!result) return
    const exported = { title, ...result }
    const content = format === 'json' ? JSON.stringify(exported, null, 2) : [title, `${result.style.name} · ${result.orientation === 'VERTICAL' ? '9:16' : '16:9'}`,
      result.analysis.summary && `STORY ANALYSIS\n${result.analysis.summary}\n${result.analysis.environment}\n${result.analysis.era}\n${result.analysis.continuity}`,
      ...result.character_bible.map(c => `CHARACTER ${c.role}\nName: ${c.name}\nRole: ${c.story_role || c.name}\nRelationship: ${c.relationship || 'Unspecified'}\nGender: ${c.gender || 'unknown'}\nAge group: ${c.age_group || 'unknown'}\nAppearance: ${c.description}\nFixed outfit: ${c.clothing || 'From approved reference'}`),
      ...result.characters.map(c => `Nhân vật ${c.role}: ${c.name}\nTham chiếu: ${c.media_id || 'Cần tạo ảnh'}\n${c.prompt || 'Dùng ảnh đã tải lên.'}`),
      ...result.scenes.map(s => `Cảnh ${s.scene}\nLời đọc: ${s.narration}\nTham chiếu: ${s.references.map(r => `${r.role}: ${r.name} (${r.media_id || 'chưa tạo ảnh'})`).join('; ') || 'Không có nhân vật'}\nPrompt ảnh:\n${s.image_prompt}\nPrompt video:\n${s.video_prompt}`)].filter(Boolean).join('\n\n')
    const url = URL.createObjectURL(new Blob([content], { type: format === 'json' ? 'application/json;charset=utf-8' : 'text/plain;charset=utf-8' }))
    const link = document.createElement('a')
    link.href = url; link.download = `frameweaver-prompts.${format}`; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  }

  return <section aria-label="Prompt ảnh và video" className="space-y-3 rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
    <div className="flex flex-wrap items-center justify-between gap-2"><Button type="button" size="sm" variant="outline" onClick={() => setOpen(!open)}>{open ? 'Ẩn prompt ảnh & video' : 'Xem prompt ảnh & video'}</Button>
      {open && <div className="flex gap-2"><Button type="button" size="sm" variant="outline" disabled={!result || disabled} onClick={() => download('txt')}>Tải TXT</Button><Button type="button" size="sm" variant="outline" disabled={!result || disabled} onClick={() => download('json')}>Tải JSON</Button></div>}
    </div>
    {open && <>
      <p className="text-xs" style={{ color: 'var(--muted)' }}>Prompt dùng chung với lúc tạo thật. Hồ sơ ngoại hình dùng tạo ảnh tham chiếu; prompt cảnh gắn đúng ảnh đó và mô tả hành động, bối cảnh, bố trí. File xuất kèm hồ sơ nhân vật. Sửa bên trên để tự cập nhật; xem prompt không tốn tín dụng.</p>
      {!result && !error && <p role="status" className="text-xs">Đang cập nhật prompt…</p>}
      {error && <div role="alert" className="space-y-2 text-xs" style={{ color: 'var(--red)' }}><p>{error}</p><Button type="button" size="sm" variant="outline" onClick={() => setRetry(n => n+1)}>Nạp lại prompt</Button></div>}
      {result && <>
        {result.motion !== 'ai' && <p className="text-xs" style={{ color: 'var(--muted)' }}>Đang dùng {result.motion === 'talk' ? 'nhân vật vector: không gửi prompt ảnh/clip' : 'ảnh chuyển động: chỉ gửi prompt ảnh'}. Prompt video dành cho lựa chọn Hoạt hình AI.</p>}
        {!!result.characters.length && <details className="rounded-lg p-2" style={{ background: 'var(--surface)' }}><summary className="cursor-pointer text-xs font-medium">Prompt tạo ảnh nhân vật · {result.characters.length} vai</summary><div className="mt-2 space-y-3">{result.characters.map(c => <div key={c.role} className="space-y-1"><p className="text-xs font-medium">{c.role} · {c.name} · {c.media_id ? 'Có ảnh tham chiếu' : 'Cần tạo ảnh trước'}</p><textarea aria-label={`Prompt nhân vật ${c.role}`} readOnly rows={4} value={c.prompt || 'Dùng ảnh tham chiếu đã tải lên; không tạo lại nhân vật.'} className="w-full rounded-lg p-2 text-xs" style={control} /></div>)}</div></details>}
        {result.scenes.map(s => <details key={s.scene} className="rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
          <summary className="cursor-pointer text-xs font-medium">Cảnh {s.scene} · {s.cast.length ? `Nhân vật ${s.cast.join(' + ')}` : 'Bối cảnh / đồ vật'} · {s.narration.slice(0, 70)}</summary>
          <div className="mt-3 space-y-2"><p className="text-xs" style={{ color: 'var(--muted)' }}>Tham chiếu: {s.references.map(r => `${r.role} · ${r.name}${r.media_id ? '' : ' (cần tạo ảnh)'}`).join('; ') || 'Không có nhân vật trong cảnh.'}</p>
            <label className="block text-xs">Prompt tạo ảnh<textarea aria-label={`Prompt ảnh cảnh ${s.scene}`} readOnly rows={10} value={s.image_prompt} className="mt-1 w-full rounded-lg p-2 text-xs" style={control} /></label>
            <Button type="button" size="sm" variant="outline" onClick={() => void copy(s.image_prompt)}>Sao chép prompt ảnh {s.scene}</Button>
            <label className="block text-xs">Prompt chuyển động · 0–3s / 3–6s / 6–8s<textarea aria-label={`Prompt video cảnh ${s.scene}`} readOnly rows={10} value={s.video_prompt} className="mt-1 w-full rounded-lg p-2 text-xs" style={control} /></label>
            <Button type="button" size="sm" variant="outline" onClick={() => void copy(s.video_prompt)}>Sao chép prompt video {s.scene}</Button>
          </div>
        </details>)}
      </>}
      {notice && <p role="status" className="text-xs">{notice}</p>}
    </>}
  </section>
}
