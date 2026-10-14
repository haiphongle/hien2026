import { useEffect, useState } from 'react'
import { fetchAPI, resolveMediaUrl } from '../../api/client'
import { Button } from '../ui/button'

interface Preview { media_id: string; url: string; selected: boolean }
interface Storyboard { all_scenes: boolean; total: number; scenes: { scene_id: string; scene: number; narration: string; cast: string[]; extras: string; shot_kind: string; url: string }[] }

export function StickmanPreview({ videoId, jobId, url, disabled, onApprove, onRegenerate }: {
  videoId: string; jobId: string; url: string; disabled: boolean
  onApprove: (mediaId?: string) => void; onRegenerate: (sceneId?: string) => void
}) {
  const [versions, setVersions] = useState<Preview[]>([]), [choice, setChoice] = useState(''), [error, setError] = useState('')
  const [board, setBoard] = useState<Storyboard | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    Promise.all([fetchAPI<Preview[]>(`/api/videos/${videoId}/stickman/preview`, { signal: controller.signal }),
      fetchAPI<Storyboard>(`/api/videos/${videoId}/stickman/storyboard`, { signal: controller.signal })]).then(([items, storyboard]) => {
      if (controller.signal.aborted) return
      setVersions(items); setBoard(storyboard); setChoice(items.find(v => v.selected)?.media_id || ''); setError('')
    }).catch(cause => { if (!controller.signal.aborted) setError(String(cause)) })
    return () => controller.abort()
  }, [videoId, jobId])
  if (board?.all_scenes) return <div className="space-y-3">
    <p className="text-sm font-medium">Duyệt ảnh các cảnh · {board.scenes.length}/{board.total}</p>
    <p className="text-xs" style={{ color: 'var(--muted)' }}>Kiểm tra nhân vật, bối cảnh và đồ vật một lần. Ảnh của từng cảnh sẽ được dùng làm khung hình đầu cho video; lời đọc được tạo sau khi duyệt.</p>
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{board.scenes.map(scene => <section key={scene.scene_id} className="space-y-2 rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
      <a href={resolveMediaUrl(scene.url)} target="_blank" rel="noreferrer"><img src={resolveMediaUrl(scene.url)} alt={`Ảnh cảnh ${scene.scene}`} loading="lazy" className="h-48 w-full rounded-lg object-contain" /></a>
      <strong className="text-xs">Cảnh {scene.scene} · {scene.cast.length ? scene.cast.join(', ') : 'Bối cảnh / đồ vật'}{scene.extras && ' · Có người phụ'}</strong>
      <p className="text-xs leading-relaxed">{scene.narration}</p>
      <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => onRegenerate(scene.scene_id)}>Tạo lại ảnh cảnh {scene.scene}</Button>
    </section>)}</div>
    <p className="text-xs" style={{ color: 'var(--muted)' }}>Tạo lại chỉ dùng lượt tạo ảnh cho cảnh đã chọn; giữ nguyên ảnh các cảnh còn lại. Bỏ chọn “Xem toàn bộ ảnh” trước khi tạo nếu muốn chạy tự động đến video hoàn chỉnh.</p>
    {error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
    <Button type="button" disabled={disabled || Boolean(error) || board.scenes.length !== board.total} onClick={() => onApprove()}>Duyệt tất cả & tạo video</Button>
  </div>
  return <div className="space-y-3">
    <p className="text-sm font-medium">Duyệt ảnh mẫu trước khi tạo clip</p>
    <img src={resolveMediaUrl(versions.find(v => v.media_id === choice)?.url || url)} alt="Ảnh mẫu phong cách" className="mx-auto max-h-[480px] rounded-xl" />
    {versions.length > 1 && <div className="flex gap-2 overflow-x-auto pb-2" aria-label="Các phiên bản ảnh mẫu">{versions.map((v, i) => <button key={v.media_id} type="button" disabled={disabled} aria-label={`Chọn ảnh mẫu ${i+1}`} aria-pressed={choice === v.media_id} onClick={() => setChoice(v.media_id)} className="w-24 shrink-0 overflow-hidden rounded-lg border-2" style={{ borderColor: choice === v.media_id ? 'var(--accent)' : 'var(--border)' }}><img src={resolveMediaUrl(v.url)} alt={`Ảnh mẫu ${i+1}`} loading="lazy" className="h-24 w-full object-cover" /><span className="block py-1 text-xs">Mẫu {i+1}</span></button>)}</div>}
    {error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
    <p className="text-xs" style={{ color: 'var(--muted)' }}>Ảnh mẫu được lưu cùng video; giữ tối đa 8 phiên bản trong danh sách. Tạo mẫu khác dùng lượt tạo ảnh Google Flow. Các clip chỉ được tạo sau khi bạn duyệt.</p>
    <div className="flex flex-wrap gap-2"><Button type="button" disabled={disabled || !board || Boolean(error)} onClick={() => onApprove(choice || undefined)}>Dùng ảnh đã chọn & tạo video</Button><Button type="button" variant="outline" disabled={disabled || !board || Boolean(error)} onClick={() => onRegenerate()}>Tạo ảnh mẫu khác</Button></div>
  </div>
}
