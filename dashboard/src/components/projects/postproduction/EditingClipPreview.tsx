import { useState } from 'react'
import { Loader2, Play, Square } from 'lucide-react'
import { fetchAPI, resolveMediaUrl } from '../../../api/client'
import { usePolling } from '../../../api/usePolling'
import { Button } from '../../ui/button'
import type { EditingSettings } from './editingTypes'

interface Job { status: string; progress: number; stage: string; error: string | null; result?: { url: string } }
export function EditingClipPreview({ videoId, settings, time, blocked }: { videoId: string; settings: EditingSettings; time: number; blocked: boolean }) {
  const [job, setJob] = useState<Job | null>(null), [error, setError] = useState(''), [pending, setPending] = useState(false)
  const [checked, setChecked] = useState(false)
  const running = job?.status === 'QUEUED' || job?.status === 'PROCESSING'
  usePolling<Job | null>(!checked || running ? `/api/videos/${videoId}/editing/preview-job` : null, 1000,
    data => { setChecked(true); setJob(data); setError('') }, cause => setError(String(cause)))
  async function start() {
    setPending(true); setError('')
    try { setJob(await fetchAPI<Job>(`/api/videos/${videoId}/editing/preview-clips`, { method: 'POST', body: JSON.stringify({ settings, start: time, duration: 3 }) })) }
    catch (cause) { setError(String(cause)); setChecked(false) }
    finally { setPending(false) }
  }
  return <div className="pp-clip-preview"><Button variant="outline" disabled={blocked || running || pending || !checked} onClick={() => void start()}>{pending || running ? <Loader2 size={15} className="animate-spin" /> : <Play size={15} />}Dựng đoạn xem thử 3 giây tại {time.toFixed(1)}s</Button>
    <p>Kiểm tra màu, vùng che và Delogo bằng FFmpeg trước khi xuất cả video. Đoạn thử dùng âm gốc.</p>
    {running && <><p role="status">{job.stage} · {job.progress}%</p><progress value={job.progress} max={100} /><Button variant="outline" onClick={() => void fetchAPI<Job>(`/api/videos/${videoId}/editing/preview-job/cancel`, { method: 'POST' }).then(setJob).catch(cause => setError(String(cause)))}><Square size={12} />Dừng đoạn thử</Button></>}
    {(error || job?.error) && <p role="alert">{error || job?.error}</p>}
    {job?.status === 'COMPLETED' && job.result && <video src={resolveMediaUrl(job.result.url)} controls preload="metadata" aria-label="Đoạn xem thử màu và vùng che" />}
    {job?.status === 'CANCELLED' && <p>Đã dừng đoạn thử.</p>}
  </div>
}
