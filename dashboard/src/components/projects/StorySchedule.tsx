import { useState } from 'react'
import { fetchAPI } from '../../api/client'
import { usePolling } from '../../api/usePolling'
import { Button } from '../ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card'

export interface SavedStory {
  id: string; title: string; status: string; run_at: string | null; video_id: string | null
  error?: string; progress?: number; stage?: string; source: string; settings: Record<string, unknown>
}
type StorySummary = Omit<SavedStory, 'source' | 'settings'>
const labels: Record<string, string> = { DRAFT: 'Bản nháp', SCHEDULED: 'Đã hẹn giờ', WAITING: 'Chờ kết nối Flow', STARTING: 'Đang bắt đầu', RUNNING: 'Đang tạo', REVIEW: 'Chờ duyệt ảnh mẫu', COMPLETED: 'Hoàn tất', FAILED: 'Cần kiểm tra', CANCELLED: 'Đã dừng' }
const control = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

export function StorySchedule({ projectId, source, request, ready, disabled, onLoad }: {
  projectId: string; source: string; request: string; ready: boolean; disabled: boolean; onLoad: (story: SavedStory) => void
}) {
  const [items, setItems] = useState<StorySummary[]>([]), [date, setDate] = useState('')
  const [error, setError] = useState(''), [working, setWorking] = useState(false)
  const [pollError, setPollError] = useState('')
  const url = `/api/projects/${projectId}/stickman/schedule`
  usePolling<StorySummary[]>(url, 15000, items => { setItems(items); setPollError('') }, cause => setPollError(String(cause)))
  async function act(run: () => Promise<void>) {
    if (working) return
    setWorking(true); setError('')
    try { await run(); setItems(await fetchAPI<StorySummary[]>(url)) }
    catch (cause) { setError(String(cause)) }
    finally { setWorking(false) }
  }
  function runAt() {
    const time = new Date(date)
    if (!date || !Number.isFinite(time.getTime()) || time.getTime() <= Date.now()) throw new Error('Chọn ngày giờ trong tương lai.')
    return time.toISOString()
  }
  function save(scheduled: boolean) {
    void act(async () => {
      await fetchAPI(url, { method: 'POST', body: JSON.stringify({ source, settings: JSON.parse(request), run_at: scheduled ? runAt() : null }) })
    })
  }
  return <Card>
    <CardHeader><CardTitle>6. Kịch bản đã lưu và lịch tạo</CardTitle><CardDescription>Lưu nhiều kịch bản cùng phong cách, nhân vật, giọng và nhạc để tạo sau.</CardDescription></CardHeader>
    <CardContent className="space-y-4">
      <label className="block text-xs">Giờ tạo · theo giờ trên máy bạn<input aria-label="Giờ hẹn tạo video" type="datetime-local" disabled={working || disabled} value={date} onChange={e => setDate(e.target.value)} className="mt-1 block rounded-lg p-2" style={control} /></label>
      <div className="flex flex-wrap gap-2"><Button type="button" variant="outline" disabled={!ready || disabled || working} onClick={() => save(false)}>Lưu kịch bản hiện tại</Button>
        <Button type="button" disabled={!ready || !date || disabled || working} onClick={() => save(true)}>Lưu và hẹn tạo video</Button></div>
      {!ready && <p className="text-xs" style={{ color: 'var(--muted)' }}>Phân tích nội dung, duyệt cảnh và chọn giọng trước khi lưu.</p>}
      <p className="text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>Để máy, FrameWeaver và Google Flow mở, extension kết nối. Nếu lỡ giờ, lịch chạy khi mở lại và kết nối đủ. Mỗi lần tạo một video; nếu bật duyệt ảnh mẫu, hàng chờ dừng để bạn duyệt. Lượt tạo dùng tín dụng Flow và không tự chạy lại khi lỗi.</p>
      <div className="space-y-2" aria-label="Danh sách kịch bản đã lưu">{items.length ? items.map(item => <section key={item.id} className="space-y-2 rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
        <div className="flex flex-wrap items-center justify-between gap-2"><strong className="text-sm">{item.title}</strong><span className="text-xs">{labels[item.status] || item.status}</span></div>
        {item.run_at && <p className="text-xs" style={{ color: 'var(--muted)' }}>{new Date(item.run_at).toLocaleString('vi-VN')}</p>}
        {item.status === 'RUNNING' && <><p className="text-xs">{item.stage || 'Đang tạo'} · {item.progress || 0}%</p><progress max={100} value={item.progress || 0} className="w-full" /></>}
        {item.error && <p className="text-xs" style={{ color: 'var(--red)' }}>{item.error}</p>}
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" variant="outline" disabled={disabled || working} onClick={() => void act(async () => onLoad(await fetchAPI<SavedStory>(`${url}/${item.id}`)))}>{item.video_id ? 'Mở kịch bản / video' : 'Nạp kịch bản'}</Button>
          {!item.video_id && ['DRAFT', 'FAILED'].includes(item.status) && <Button type="button" size="sm" disabled={!date || disabled || working} onClick={() => void act(async () => { await fetchAPI(`${url}/${item.id}`, { method: 'PATCH', body: JSON.stringify({ run_at: runAt() }) }) })}>Hẹn theo giờ đã chọn</Button>}
          {['SCHEDULED', 'WAITING'].includes(item.status) && <Button type="button" size="sm" variant="outline" disabled={working} onClick={() => void act(async () => { await fetchAPI(`${url}/${item.id}`, { method: 'PATCH', body: JSON.stringify({ run_at: null }) }) })}>Hủy lịch · giữ bản nháp</Button>}
          {['DRAFT', 'FAILED'].includes(item.status) && !item.video_id && <Button type="button" size="sm" variant="outline" disabled={working} onClick={() => { if (window.confirm(`Xóa bản nháp “${item.title}”?`)) void act(async () => { await fetchAPI(`${url}/${item.id}`, { method: 'DELETE' }) }) }}>Xóa bản nháp</Button>}
        </div>
      </section>) : <p className="text-sm" style={{ color: 'var(--muted)' }}>Chưa có kịch bản đã lưu.</p>}</div>
      {working && <p role="status" className="text-xs">Đang lưu / nạp kịch bản…</p>}
      {(error || pollError) && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{error || pollError}</p>}
    </CardContent>
  </Card>
}
