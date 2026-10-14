import { useRef, useState } from 'react'
import { MoreHorizontal, Trash2 } from 'lucide-react'
import { fetchAPI } from '../../../api/client'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../../ui/dialog'
import './mediaLibrary.css'

interface Item { id: string; kind: 'video' | 'audio'; name: string; size_bytes: number; trashed_at: string | null; restore_available: boolean; restore_reason: string | null }
interface ItemDetails extends Item { uses: { id: string; name: string; trashed: boolean }[] }
export interface LibraryChange { id: string; kind: 'video' | 'audio'; action: 'rename' | 'trash' | 'restore' | 'purge' }
interface Shared { projectId: string; disabled?: boolean; beforeChange: () => Promise<unknown>; onChanged: (change: LibraryChange) => Promise<void> }
const base = (pid: string) => `/api/projects/${pid}/library`
function bytes(value: number) { return value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GB` : `${(value / 1024 ** 2).toFixed(1)} MB` }
function errorText(cause: unknown) {
  const text = String(cause), json = text.indexOf('{')
  try {
    const detail: unknown = JSON.parse(text.slice(json)).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) return detail.map(value => typeof value?.msg === 'string' ? value.msg : 'Dữ liệu không hợp lệ.').join(' ')
  } catch { /* Non-JSON errors already contain a readable message. */ }
  return text
}

export function MediaLibraryActions({ itemId, name, trashed = false, restoreReason, projectId, disabled, beforeChange, onChanged }: Shared & { itemId: string; name: string; trashed?: boolean; restoreReason?: string | null }) {
  const menu = useRef<HTMLDetailsElement>(null)
  const [action, setAction] = useState<'rename' | 'trash' | 'purge' | null>(null)
  const [info, setInfo] = useState<ItemDetails | null>(null), [input, setInput] = useState('')
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function open(next: typeof action) {
    menu.current?.removeAttribute('open'); setAction(next); setInfo(null); setError(''); setBusy(true)
    try { await beforeChange(); const item = await fetchAPI<ItemDetails>(`${base(projectId)}/items/${itemId}`); setInfo(item); setInput(next === 'rename' ? item.name : '') }
    catch (cause) { setError(errorText(cause)) }
    finally { setBusy(false) }
  }
  async function submit(next: LibraryChange['action']) {
    setBusy(true); setError('')
    try {
      await beforeChange()
      const path = `${base(projectId)}/items/${itemId}`
      const result = await fetchAPI<LibraryChange>(next === 'trash' || next === 'restore' ? `${path}/${next}` : path, {
        method: next === 'rename' ? 'PATCH' : next === 'purge' ? 'DELETE' : 'POST',
        ...(next === 'rename' || next === 'purge' ? { body: JSON.stringify({ name: next === 'purge' ? input : input.trim() }) } : {}),
      })
      setAction(null)
      try { await onChanged(result) }
      catch (cause) { setError(`Đã lưu thay đổi, nhưng chưa nạp lại được danh sách. Nạp lại trang để cập nhật. ${errorText(cause)}`) }
    } catch (cause) { setError(errorText(cause)) }
    finally { setBusy(false) }
  }
  return <div className="ml-actions" draggable={false} onDragStart={e => { e.preventDefault(); e.stopPropagation() }} onKeyDown={e => e.stopPropagation()}>
    {trashed && <button disabled={disabled || busy || !!restoreReason} title={restoreReason || undefined} onClick={() => void submit('restore')}>Khôi phục</button>}
    <details ref={menu} className="ml-menu"><summary aria-label={`Quản lý ${name}`} aria-disabled={disabled || busy} onClick={e => { if (disabled || busy) e.preventDefault() }}><MoreHorizontal size={16} /></summary>
      <div><button disabled={disabled || busy} onClick={() => void open('rename')}>Đổi tên</button><button disabled={disabled || busy} onClick={() => void open(trashed ? 'purge' : 'trash')}>{trashed ? 'Xóa vĩnh viễn…' : 'Chuyển vào thùng rác…'}</button></div>
    </details>
    {error && !action && <p role="alert" className="ml-error">{error}</p>}
    <Dialog open={action !== null} onOpenChange={value => { if (!value && !busy) { setAction(null); setError('') } }}>
      <DialogContent className="ml-dialog" onKeyDown={e => e.stopPropagation()} onEscapeKeyDown={e => { if (busy) e.preventDefault() }} onInteractOutside={e => { if (busy) e.preventDefault() }}>
        <DialogHeader><DialogTitle>{action === 'rename' ? 'Đổi tên tư liệu' : action === 'purge' ? 'Xóa vĩnh viễn' : 'Chuyển vào thùng rác'}</DialogTitle><DialogDescription>{action === 'rename' ? 'Tên hiển thị mới sẽ giữ nguyên liên kết trên timeline.' : action === 'purge' ? 'Xóa file do FrameWeaver lưu và bản dựng đi kèm nếu đây là video. Thao tác này không thể khôi phục.' : 'Giữ file và bản dựng để khôi phục. Chưa giải phóng dung lượng.'}</DialogDescription></DialogHeader>
        {busy && !info && <p role="status">Đang kiểm tra tư liệu…</p>}
        {info && <><p className="ml-item-name">{info.name} <small>· {bytes(info.size_bytes)}</small></p>
          {action !== 'rename' && info.uses.length > 0 && <div className="ml-warning"><strong>Gỡ tư liệu khỏi các bản dựng / kịch bản này trước khi xóa:</strong><ul>{info.uses.map(use => <li key={use.id}>{use.name}{use.trashed ? ' (trong thùng rác)' : ''}</li>)}</ul></div>}
          {action !== 'trash' && <label>{action === 'purge' ? 'Nhập đúng tên để xác nhận' : 'Tên mới'}<input autoFocus maxLength={action === 'rename' ? 120 : undefined} value={input} disabled={busy} onChange={e => setInput(e.target.value)} /></label>}
          {info.kind === 'video' && action === 'trash' && <p>Video sẽ ẩn khỏi danh sách và thư viện. Bản dựng của chính video này được giữ lại.</p>}
        </>}
        {error && <p role="alert" className="ml-error">{error}</p>}
        <div className="ml-buttons"><button disabled={busy} onClick={() => setAction(null)}>Hủy</button><button className={action === 'rename' ? 'ml-primary' : 'ml-danger'} disabled={busy || !info || (action === 'rename' ? !input.trim() : info.uses.length > 0 || action === 'purge' && input !== info.name)} onClick={() => action && void submit(action)}>{busy ? 'Đang xử lý…' : action === 'rename' ? 'Lưu tên' : action === 'purge' ? 'Xóa vĩnh viễn' : 'Chuyển vào thùng rác'}</button></div>
      </DialogContent>
    </Dialog>
  </div>
}

export function MediaTrash({ projectId, disabled, beforeChange, onChanged }: Shared) {
  const [open, setOpen] = useState(false), [items, setItems] = useState<Item[]>([])
  const [loading, setLoading] = useState(false), [error, setError] = useState('')
  async function load() {
    setLoading(true); setError('')
    try { await beforeChange(); setItems(await fetchAPI<Item[]>(`${base(projectId)}/trash`)) }
    catch (cause) { setError(errorText(cause)) }
    finally { setLoading(false) }
  }
  return <><button className="ml-trash-button" disabled={disabled} onClick={() => { setOpen(true); void load() }}><Trash2 size={15} />Thùng rác</button>
    <Dialog open={open} onOpenChange={setOpen}><DialogContent className="ml-dialog ml-trash-dialog">
      <DialogHeader><DialogTitle>Thùng rác tư liệu</DialogTitle><DialogDescription>Khôi phục để dùng lại. Xóa vĩnh viễn mới giải phóng dung lượng trên máy.</DialogDescription></DialogHeader>
      {loading && <p role="status">Đang kiểm tra thùng rác…</p>}
      {error && <p role="alert" className="ml-error">{error}</p>}
      {!loading && !error && !items.length && <p>Thùng rác đang trống.</p>}
      <div className="ml-trash-list">{items.map(item => <article key={item.id}><div><strong>{item.name}</strong><small>{item.kind === 'video' ? 'Video & bản dựng' : 'Âm thanh'} · {bytes(item.size_bytes)}</small>{item.restore_reason && <small>{item.restore_reason}</small>}</div><MediaLibraryActions projectId={projectId} itemId={item.id} name={item.name} trashed disabled={disabled || loading} restoreReason={item.restore_reason} beforeChange={beforeChange} onChanged={async change => { try { await onChanged(change) } finally { await load() } }} /></article>)}</div>
    </DialogContent></Dialog>
  </>
}
