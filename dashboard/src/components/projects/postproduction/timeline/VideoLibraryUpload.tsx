import { useEffect, useRef, useState } from 'react'
import { fetchAPI } from '../../../../api/client'
import { usePolling } from '../../../../api/usePolling'
import { ImportVideoButton } from '../ImportVideoButton'
import type { Job, TimelineState } from './types'

/** Use the existing streamed import/normalization pipeline, keeping this edit open. */
export function VideoLibraryUpload({ projectId, refresh }: { projectId: string; refresh: () => Promise<TimelineState | undefined> }) {
  const key = `flowkit:video-library-upload:${projectId}`
  const [pending, setPending] = useState<string | null>(()=>{try{return localStorage.getItem(key)}catch{return null}})
  const [message,setMessage] = useState(''), [error,setError] = useState(''), [job,setJob] = useState<Job | null>(null)
  const finished = useRef(false), alive = useRef(true)
  useEffect(()=>{alive.current=true;return()=>{alive.current=false}},[])
  function remember(id: string | null) {setPending(id);try{if(id)localStorage.setItem(key,id);else localStorage.removeItem(key)}catch{/* Recovery notice is optional. */}}
  usePolling<{source?:{status:string};job:Job|null}>(pending ? `/api/videos/${pending}/workbench` : null, 2000, data=>{
    setJob(data.job);setError('')
    if(data.source?.status==='READY'&&!finished.current){
      finished.current=true
      void refresh().then(()=>{if(alive.current){remember(null);setMessage('Video đã lưu trong thư viện. Bấm “Chèn khung” dưới video để đặt vào vạch phát.')}}).catch(cause=>{finished.current=false;if(alive.current)setError(String(cause))})
    }else if(data.job&&['FAILED','CANCELLED'].includes(data.job.status)){
      setError(data.job.error||'Chuẩn bị video đã dừng. Tải lại file để thử lại.');remember(null)
    }
  },cause=>setError(String(cause)))
  return <div className="tl-video-upload">
    {!pending&&<ImportVideoButton projectId={projectId} label="Tải video chèn" onImported={async id=>{finished.current=false;setMessage('');setError('');remember(id)}}/>}
    {pending&&<div role="status"><span>{job?.stage||'Đang chuẩn bị video chèn…'} · {job?.progress??0}%</span><progress value={job?.progress??0} max={100}/><button onClick={()=>void fetchAPI(`/api/videos/${pending}/workbench/cancel`,{method:'POST'}).catch(cause=>setError(String(cause)))}>Hủy chuẩn bị</button></div>}
    {message&&<p className="tl-help" role="status">{message}</p>}{error&&<p className="tl-error" role="alert">{error}</p>}
  </div>
}
