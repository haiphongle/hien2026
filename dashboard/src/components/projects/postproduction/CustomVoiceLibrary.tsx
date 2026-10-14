import { useRef, useState } from 'react'
import { fetchAPI } from '../../../api/client'
import { usePolling } from '../../../api/usePolling'
import { Button } from '../../ui/button'
import { speechBusy, speechError, speechStyle, type SpeechVoice } from './speechTypes'

export function CustomVoiceLibrary({ voices, onChanged, onPlay }: {
  voices: SpeechVoice[]; onChanged: (voices: SpeechVoice[]) => void; onPlay: (url: string) => void
}) {
  const [name, setName] = useState('')
  const [transcript, setTranscript] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)
  const preparing = voices.some(v => speechBusy(v.job))
  usePolling<SpeechVoice[]>(preparing || refreshing ? '/api/tts/custom-voices' : null, 1500, result => {
    onChanged(result); setRefreshing(false)
  }, cause => setError(String(cause)))

  async function upload() {
    if (!file || !name.trim()) return
    setBusy(true); setError('')
    const form = new FormData()
    form.append('name', name); form.append('transcript', transcript); form.append('file', file)
    try {
      await fetchAPI('/api/tts/custom-voices', { method: 'POST', body: form })
      setFile(null); setName(''); setTranscript('')
      if (fileInput.current) fileInput.current.value = ''
    } catch (cause) { setError(speechError(cause)) }
    finally { setRefreshing(true); setBusy(false) }
  }

  return <details className="pp-disclosure">
    <summary>Giọng riêng của tôi <span className="pp-badge">{voices.length}</span><small>Tải bản ghi của bạn để tạo giọng tham chiếu.</small></summary>
    <div className="pp-disclosure-body">
    <p className="my-3 text-xs" style={{ color: 'var(--muted)' }}>Tải bản ghi một người nói rõ, dài 3–8 giây, ít nhạc và tiếng ồn. Giọng cùng demo được lưu trên máy để dùng lại. Lần đầu có thể cần tải thêm model nhận giọng.</p>
    <div className="grid gap-2 md:grid-cols-2">
      <label className="grid gap-1 text-xs">Tên giọng<input value={name} maxLength={80} onChange={e => setName(e.target.value)} className="rounded p-2" style={speechStyle} placeholder="Phúc — giọng của tôi" /></label>
      <label className="grid gap-1 text-xs">Bản ghi (tối đa 20 MB)<input ref={fileInput} type="file" accept="audio/*,.wav,.mp3,.m4a,.flac" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
      <label className="grid gap-1 text-xs md:col-span-2">Lời trong bản ghi (ghi chú, không bắt buộc)<input value={transcript} maxLength={2000} onChange={e => setTranscript(e.target.value)} className="rounded p-2" style={speechStyle} /></label>
    </div>
    <Button className="my-3" size="sm" disabled={busy || !file || !name.trim()} onClick={() => void upload()}>{busy ? 'Đang tải bản ghi…' : 'Lưu giọng & tạo demo'}</Button>
    {error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
    <div className="grid gap-2 md:grid-cols-2">{voices.map(voice => <div key={voice.id} className="rounded p-2 text-xs" style={speechStyle}>
      <strong>{voice.name}</strong>
      <div className="mt-2 flex flex-wrap gap-2">
        {voice.reference_url && <Button size="sm" variant="outline" onClick={() => onPlay(voice.reference_url!)}>Nghe bản gốc</Button>}
        {voice.audio_url && <Button size="sm" variant="outline" onClick={() => onPlay(voice.audio_url!)}>Nghe demo giọng</Button>}
        {!speechBusy(voice.job) && voice.status !== 'READY' && <Button size="sm" onClick={async () => {
          try { await fetchAPI(`/api/tts/custom-voices/${voice.id}/prepare`, { method: 'POST' }); setRefreshing(true) }
          catch (cause) { setError(String(cause)) }
        }}>Chuẩn bị lại</Button>}
      </div>
      {speechBusy(voice.job) && <p role="status">{voice.job?.stage === 'enrolling' ? 'Đang nhận giọng…' : voice.job?.stage === 'generating' ? 'Đang tạo demo…' : 'Đang chờ hoặc nạp model…'}</p>}
      {voice.job?.error && <p role="alert">{voice.job.error}</p>}
      {Object.values(voice.job?.results ?? {}).filter(r => !r.ok).map((r, i) => <p key={i} role="alert">{r.error}</p>)}
    </div>)}</div>
    </div>
  </details>
}
