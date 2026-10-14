import { useEffect, useRef, useState } from 'react'
import { API_BASE_URL } from '../../../../api/client'
import type { TimelineState } from './types'

export type UploadKind = 'music' | 'sfx'
export function useLibraryUpload(videoId: string, projectId: string, refresh: () => Promise<TimelineState | undefined>, onError: (message: string) => void) {
  const [uploading, setUploading] = useState(false), [progress, setProgress] = useState(0), [message, setMessage] = useState('')
  const [canCancel, setCanCancel] = useState(false)
  const request = useRef<XMLHttpRequest | null>(null), alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false; request.current?.abort() } }, [])

  async function upload(file: File, kind: UploadKind) {
    if (request.current) return
    const suffix = file.name.toLowerCase().split('.').pop() || ''
    const formats = kind === 'music' ? ['mp3', 'wav'] : ['mp3', 'wav', 'm4a', 'ogg', 'flac', 'aac']
    const limit = (kind === 'music' ? 100 : 16) * 1024 * 1024
    if (!formats.includes(suffix)) { onError(`Chọn file ${formats.map(f => f.toUpperCase()).join(', ')}.`); return }
    if (!file.size || file.size > limit) { onError(`File cần có dữ liệu và tối đa ${kind === 'music' ? '100' : '16'} MB.`); return }
    setUploading(true); setCanCancel(true); setProgress(0); setMessage(`Đang tải ${file.name}`); onError('')
    try {
      const result = await new Promise<{ filename?: string; id?: string }>((resolve, reject) => {
        const xhr = new XMLHttpRequest(); request.current = xhr
        const url = kind === 'music'
          ? `/api/music/files/${projectId}/upload?filename=${encodeURIComponent(file.name)}`
          : `/api/videos/${videoId}/pacing/assets`
        xhr.open('POST', `${API_BASE_URL}${url}`); xhr.timeout = 180_000
        xhr.upload.onprogress = event => { if (alive.current && event.lengthComputable) setProgress(Math.min(99, Math.round(event.loaded / event.total * 100))) }
        xhr.onload = () => {
          let body
          try { body = JSON.parse(xhr.responseText) } catch { reject(new Error(`API ${xhr.status}: không đọc được phản hồi tải lên.`)); return }
          if (xhr.status < 200 || xhr.status >= 300) { reject(new Error(`API ${xhr.status}: ${body.detail || xhr.statusText}`)); return }
          resolve(body)
        }
        xhr.onerror = () => reject(new Error('Mất kết nối khi tải lên. Kiểm tra backend rồi thử lại.'))
        xhr.ontimeout = () => reject(new Error('Tải lên quá thời gian chờ. Thử lại khi backend sẵn sàng.'))
        xhr.onabort = () => reject(new DOMException('Đã hủy tải lên.', 'AbortError'))
        if (kind === 'music') xhr.send(file)
        else { const body = new FormData(); body.append('file', file); xhr.send(body) }
      })
      if (!alive.current) return
      setCanCancel(false)
      setMessage('Đang nạp vào thư viện…')
      const state = await refresh()
      if (!alive.current) return
      const ref = kind === 'music' ? `music:${result.filename}` : result.id
      if (!state?.assets.some(a => a.ref === ref && a.category === kind)) throw new Error('File đã tải lên nhưng chưa đọc được âm thanh. Kiểm tra định dạng hoặc bấm Nạp lại thư viện.')
      setProgress(100); setMessage(`Đã lưu ${file.name}. Bấm nghe thử hoặc kéo vào timeline.`)
    } catch (cause) {
      if (!alive.current) return
      if (cause instanceof DOMException && cause.name === 'AbortError') setMessage('Đã hủy tải lên.')
      else { setMessage(''); onError(cause instanceof Error ? cause.message : String(cause)) }
    } finally {
      request.current = null
      if (alive.current) { setUploading(false); setCanCancel(false) }
    }
  }
  return { uploading, progress, message, canCancel, upload, cancel: () => request.current?.abort() }
}
