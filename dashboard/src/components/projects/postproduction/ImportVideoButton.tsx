import { useEffect, useRef, useState } from 'react'
import { Upload } from 'lucide-react'
import { API_BASE_URL, fetchAPI } from '../../../api/client'
import { Button } from '../../ui/button'
import { speechError } from './speechTypes'

interface ImportLimits { max_bytes: number; max_duration_seconds: number; upload_timeout_seconds: number }

export function ImportVideoButton({ projectId, onImported, label = 'Nhập video từ máy' }: { projectId: string; onImported: (id: string) => Promise<void>; label?: string }) {
  const [progress, setProgress] = useState<number | null>(null)
  const [error, setError] = useState('')
  const [limits, setLimits] = useState<ImportLimits | null>(null)
  const [limitsError, setLimitsError] = useState('')
  const [limitsAttempt, setLimitsAttempt] = useState(0)
  const input = useRef<HTMLInputElement>(null)
  const request = useRef<XMLHttpRequest | null>(null)
  useEffect(() => () => request.current?.abort(), [])
  useEffect(() => {
    const controller = new AbortController()
    fetchAPI<ImportLimits>('/api/media-workbench/import-limits', { signal: controller.signal })
      .then(result => { if (!controller.signal.aborted) { setLimits(result); setLimitsError('') } })
      .catch(cause => { if (!controller.signal.aborted) setLimitsError(speechError(cause)) })
    return () => controller.abort()
  }, [limitsAttempt])
  const limitsLabel = limits ? `${limits.max_bytes / 1024 ** 3} GB / ${limits.max_duration_seconds / 60} phút` : ''
  function upload(file: File) {
    if (!limits || progress !== null) return
    setError('')
    if (file.size > limits.max_bytes) { setError(`Chọn video tối đa ${limitsLabel}.`); return }
    const xhr = new XMLHttpRequest()
    request.current = xhr
    setProgress(0)
    xhr.open('POST', `${API_BASE_URL}/api/projects/${projectId}/import-video?filename=${encodeURIComponent(file.name)}`)
    xhr.setRequestHeader('Content-Type', 'application/octet-stream')
    xhr.timeout = limits.upload_timeout_seconds * 1000
    xhr.upload.onprogress = e => { if (e.lengthComputable) setProgress(Math.round(e.loaded / e.total * 100)) }
    xhr.onerror = () => { setError('Mất kết nối khi tải video. Kiểm tra backend rồi thử lại.'); setProgress(null) }
    xhr.ontimeout = () => { setError('Tải video quá thời gian chờ. Thử video nhỏ hơn.'); setProgress(null) }
    xhr.onabort = () => setProgress(null)
    xhr.onload = async () => {
      try {
        if (xhr.status < 200 || xhr.status >= 300) throw new Error(`API ${xhr.status}: ${xhr.responseText}`)
        await onImported(JSON.parse(xhr.responseText).video.id)
      } catch (cause) { setError(speechError(cause)) }
      finally { setProgress(null) }
    }
    xhr.send(file)
  }
  return <div className="pp-import">
    <input ref={input} className="sr-only" type="file" accept=".mp4,.mov,.mkv,.webm,.m4v" aria-label="Chọn video từ máy"
      disabled={!limits || progress !== null} onChange={e => { const file = e.target.files?.[0]; if (file) upload(file); e.target.value = '' }} />
    <Button variant="outline" disabled={!limits || progress !== null} onClick={() => input.current?.click()}><Upload size={16} />{label}</Button>
    <small>{limits ? `MP4, MOV, MKV, WebM, M4V · tối đa ${limitsLabel}` : 'Đang đọc giới hạn tải lên…'}</small>
    {limitsError && <div role="alert"><p>Chưa đọc được giới hạn tải lên: {limitsError}</p><Button size="sm" variant="ghost" onClick={() => setLimitsAttempt(value => value + 1)}>Thử kết nối lại</Button></div>}
    {progress !== null && <div role="status"><p>{progress < 100 ? `Đang tải lên ${progress}%` : 'Đã tải lên · đang kiểm tra video…'}</p>
      <progress value={progress} max={100} /><Button size="sm" variant="ghost" onClick={() => request.current?.abort()}>Dừng tải</Button></div>}
    {error && <p role="alert">{error}</p>}
  </div>
}
