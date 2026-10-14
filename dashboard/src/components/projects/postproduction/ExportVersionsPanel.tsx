import { useState } from 'react'
import { Download, History, Play, Save, Trash2 } from 'lucide-react'
import { fetchAPI, resolveMediaUrl } from '../../../api/client'
import { Button } from '../../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../../ui/dialog'
import type { EditingResult, EditingSettings } from './editingTypes'

interface DeletionReport {
  items: { export_id: string; name: string; size_bytes: number; uses: { id: string; name: string; trashed: boolean }[] }[]
  size_bytes: number; blocked_reason: string | null
}
interface Props {
  videoId: string; versions: EditingResult[]; disabled?: boolean
  onRefresh: () => Promise<unknown>; beforeDelete?: () => Promise<unknown>
  onApply?: (settings: EditingSettings) => void; onHandoff?: (id: string) => Promise<void>
}
const idOf = (entry: EditingResult) => entry.export_id || entry.url.split('/').pop()!
const bytes = (value: number) => value >= 1024 ** 3 ? `${(value / 1024 ** 3).toFixed(2)} GB` : `${(value / 1024 ** 2).toFixed(1)} MB`
function errorText(cause: unknown) {
  const text = String(cause)
  try { const detail: unknown = JSON.parse(text.slice(text.indexOf('{'))).detail; if (typeof detail === 'string') return detail } catch { /* Non-JSON response. */ }
  return text
}

export function ExportVersionsPanel({ videoId, versions, disabled, onRefresh, beforeDelete, onApply, onHandoff }: Props) {
  const [page, setPage] = useState(0), [names, setNames] = useState<Record<string, string>>({}), [error, setError] = useState(''), [pending, setPending] = useState('')
  const [selected, setSelected] = useState(''), [checked, setChecked] = useState<string[]>([])
  const [report, setReport] = useState<DeletionReport | null>(null)
  const preview = versions.find(e => e.url === selected)
  const selectedIds = checked.filter(id => versions.some(e => idOf(e) === id))
  const busy = disabled || !!pending
  const limit = 5, currentPage = Math.min(page, Math.max(0, Math.ceil(versions.length / limit) - 1))
  const visible = versions.slice(currentPage * limit, (currentPage + 1) * limit)
  const path = `/api/videos/${videoId}/editing/exports`
  async function rename(entry: EditingResult, id: string) {
    setPending(id); setError('')
    try {
      await fetchAPI(`${path}/${id}`, { method: 'PATCH', body: JSON.stringify({ name: (names[id] ?? entry.name ?? 'Bản xuất').trim() }) })
      await onRefresh()
    } catch (cause) { setError(errorText(cause)) }
    finally { setPending('') }
  }
  async function checkDelete(ids: string[]) {
    setPending('check'); setError('')
    try {
      await beforeDelete?.()
      setReport(await fetchAPI<DeletionReport>(`${path}/check-delete`, { method: 'POST', body: JSON.stringify({ export_ids: ids }) }))
    } catch (cause) { setError(errorText(cause)) }
    finally { setPending('') }
  }
  async function remove() {
    if (!report) return
    setPending('delete'); setError('')
    try {
      await beforeDelete?.()
      const result = await fetchAPI<{ deleted: string[] }>(path, { method: 'DELETE', body: JSON.stringify({ export_ids: report.items.map(e => e.export_id) }) })
      setSelected(''); setChecked(ids => ids.filter(id => !result.deleted.includes(id))); setReport(null)
      try { await onRefresh() } catch (cause) { setError(`Đã xóa, nhưng chưa nạp lại được lịch sử. Bấm nạp lại. ${errorText(cause)}`) }
    } catch (cause) { setError(errorText(cause)) }
    finally { setPending('') }
  }
  return <div className="pp-versions" onKeyDown={e => e.stopPropagation()}><h4><History size={16} />Các bản xuất đã lưu ({versions.length})</h4>
    {!versions.length && <p>Tạo bản xuất đầu tiên để xem, tải lại hoặc dùng lại thiết lập tại đây.</p>}
    {!!versions.length && <div className="pp-edit-actions pp-version-selection"><label title="Tối đa 100 bản mỗi lần xóa"><input type="checkbox" aria-label="Chọn tất cả bản xuất trong trang" disabled={busy || selectedIds.length >= 100 && visible.every(e => !selectedIds.includes(idOf(e)))} checked={visible.length > 0 && visible.every(e => selectedIds.includes(idOf(e)))} onChange={e => setChecked(e.target.checked ? [...new Set([...selectedIds, ...visible.map(idOf)])].slice(0, 100) : selectedIds.filter(id => !visible.some(v => idOf(v) === id)))} />Chọn cả trang</label>
      <Button variant="outline" disabled={busy || !selectedIds.length} onClick={() => void checkDelete(selectedIds)}><Trash2 size={14} />Xóa đã chọn ({selectedIds.length})</Button>
      <Button variant="outline" disabled={busy} onClick={() => void onRefresh().catch(cause => setError(errorText(cause)))}>Nạp lại lịch sử</Button>
    </div>}
    {visible.map((entry, i) => {
      const id = idOf(entry), name = entry.name ?? `Bản ${entry.settings?.frame.preset ?? `${entry.width} × ${entry.height}`}`
      return <article key={entry.url} className="pp-version-card">
        <div className="pp-version-title"><input type="checkbox" aria-label={`Chọn ${name}`} disabled={busy || selectedIds.length >= 100 && !selectedIds.includes(id)} checked={selectedIds.includes(id)} onChange={e => setChecked(e.target.checked ? [...selectedIds, id] : selectedIds.filter(value => value !== id))} />
          <input aria-label={`Tên bản xuất ${currentPage * limit + i + 1}`} maxLength={120} value={names[id] ?? name} disabled={busy} onChange={e => setNames({ ...names, [id]: e.target.value })} />
          <Button variant="outline" aria-label={`Lưu tên bản xuất ${currentPage * limit + i + 1}`} disabled={busy || !names[id]?.trim()} onClick={() => void rename(entry, id)}><Save size={14} /></Button></div>
        <p>{entry.width} × {entry.height} · {entry.duration?.toFixed(1)} giây{entry.range_end != null ? ' · vùng chọn' : ''}{entry.bytes ? ` · ${bytes(entry.bytes)}` : ''}{entry.created_at ? ` · ${new Date(entry.created_at * 1000).toLocaleString()}` : ''}</p>
        <div className="pp-edit-actions"><Button variant="outline" aria-pressed={selected === entry.url} onClick={() => setSelected(selected === entry.url ? '' : entry.url)}><Play size={14} />Xem bản này</Button>
          <Button asChild variant="outline"><a href={resolveMediaUrl(`${entry.url}?download=true`)} download><Download size={14} />Tải MP4</a></Button>
          {entry.settings && onApply && <Button variant="outline" onClick={() => onApply(entry.settings!)}>Dùng lại thiết lập</Button>}
          {onHandoff && !entry.range_start && entry.range_end == null && <Button variant="outline" disabled={busy} onClick={() => void onHandoff(id)}>Tạo bản phẳng cho AI</Button>}
          <Button variant="outline" disabled={busy} onClick={() => void checkDelete([id])}><Trash2 size={14} />Xóa bản này</Button></div>
      </article>
    })}
    {versions.length > limit && <div className="pp-edit-actions"><Button disabled={busy || currentPage === 0} onClick={() => setPage(currentPage - 1)} variant="outline">Trước</Button><span>Trang {currentPage + 1} / {Math.ceil(versions.length / limit)}</span><Button disabled={busy || (currentPage + 1) * limit >= versions.length} onClick={() => setPage(currentPage + 1)} variant="outline">Sau</Button></div>}
    {preview && <video key={preview.url} src={resolveMediaUrl(preview.url)} controls preload="metadata" aria-label="Xem bản xuất đã chọn" />}
    {error && !report && <p role="alert">{error}</p>}
    <Dialog open={report !== null} onOpenChange={open => { if (!open && !pending) { setReport(null); setError('') } }}>
      <DialogContent className="pp-delete-exports" showCloseButton={!pending} onKeyDown={e => e.stopPropagation()} onEscapeKeyDown={e => { if (pending) e.preventDefault() }} onInteractOutside={e => { if (pending) e.preventDefault() }}>
        <DialogHeader><DialogTitle>Xóa {report?.items.length} bản xuất?</DialogTitle><DialogDescription>Xóa vĩnh viễn các MP4 đã chọn và dữ liệu xem thử đi kèm. Không thể hoàn tác. Video nguồn và bản dựng được giữ lại.</DialogDescription></DialogHeader>
        {report && <><p>Dung lượng MP4 sẽ giải phóng: <strong>{bytes(report.size_bytes)}</strong></p><ul className="pp-export-delete-list">{report.items.map(item => <li key={item.export_id}><strong>{item.name}</strong> · {bytes(item.size_bytes)}
          {!!item.uses.length && <p role="alert">Đang dùng trong: {item.uses.map(use => `${use.name}${use.trashed ? ' (thùng rác)' : ''}`).join(', ')}. Gỡ khỏi timeline và lưu trước khi xóa.</p>}</li>)}</ul>
          {report.blocked_reason && <p role="alert">{report.blocked_reason}</p>}</>}
        {error && <p role="alert">{error}</p>}
        <div className="pp-edit-actions"><Button variant="outline" disabled={!!pending} onClick={() => { setReport(null); setError('') }}>Hủy</Button><Button variant="destructive" disabled={busy || !report || !!report.blocked_reason || report.items.some(item => item.uses.length)} onClick={() => void remove()}>{pending === 'delete' ? 'Đang xóa…' : 'Xóa vĩnh viễn'}</Button></div>
      </DialogContent>
    </Dialog>
  </div>
}
