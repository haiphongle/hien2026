import { useEffect, useState } from 'react'
import { AudioLines, ShieldCheck, Users } from 'lucide-react'
import { fetchAPI } from '../../../api/client'
import { usePolling } from '../../../api/usePolling'
import { Button } from '../../ui/button'
import { SpeakerSuggestions, type SpeakerReport } from './SpeakerSuggestions'
import { speechError, type SpeechAudio, type SpeechDocument, type SpeechVoice } from './speechTypes'

interface Job { id: string; kind: string; status: string; progress: number; stage: string; error: string | null }
interface BaseReport { created_at: number; revision: number; stale: boolean; skipped: string[]; fingerprint: string }
interface SignalReport extends BaseReport {
  export_url?: string
  items: { id: string; text: string; start: number; duration: number; limited: boolean; warnings: { kind: string; start: number; end: number }[] }[]
}
interface WordsReport extends BaseReport {
  cues: { id: string; start: number; end: number; expected: string; recognized: string; needs_review: boolean; uncertain: boolean }[]
}
interface State {
  capabilities: { signal_ready: boolean; speakers_ready: boolean; whisper_available: boolean; models: { id: string; name: string; ready: boolean }[] }
  has_export: boolean; job: Job | null
  reports: { signal_speech?: SignalReport; signal_export?: SignalReport; words?: WordsReport; speakers?: SpeakerReport & BaseReport }
}
interface Props {
  videoId: string; document: SpeechDocument | null; dirty: boolean; stale: boolean; busy: boolean
  audio: SpeechAudio[]; voices: SpeechVoice[]; refreshKey: string
  onBusy: (value: boolean) => void; onSeek: (time: number) => void
  onPlay: (url: string, offset?: number, end?: number) => void; onDraft: (document: SpeechDocument) => void
}
const active = (job?: Job | null) => job?.status === 'QUEUED' || job?.status === 'PROCESSING'
const labels = { signal_speech: 'Tín hiệu lời đọc', signal_export: 'Tín hiệu video đã ghép', words: 'Nội dung lời đọc', speakers: 'Người nói' }
const warnings: Record<string, string> = { empty: 'File không có âm thanh', silence: 'Khoảng im lặng', quiet: 'Âm quá nhỏ', loud: 'Âm lượng tăng đột ngột', clipping: 'Có dấu hiệu clipping / rè', invalid: 'Mẫu âm thanh lỗi', unreadable: 'Không đọc được file' }

export function SpeechAnalysisPanel({ videoId, document, dirty, stale, busy, audio, voices, refreshKey, onBusy, onSeek, onPlay, onDraft }: Props) {
  const base = `/api/videos/${videoId}/speech-analysis`
  const [state, setState] = useState<State | null>(null)
  const [pending, setPending] = useState(false)
  const [check, setCheck] = useState(false)
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const [model, setModel] = useState('small')
  const [speakerCount, setSpeakerCount] = useState(0)
  const [view, setView] = useState<keyof State['reports']>('signal_speech')
  const [page, setPage] = useState(0)
  const running = active(state?.job)
  const ownJob = state?.job?.kind.startsWith('analysis_') ? state.job : null
  const locked = busy || pending || running || !document
  const speechLocked = locked || dirty || stale || !document?.cues.length
  const hasAudio = audio.some(item => item.audio_url)
  const readyModel = state?.capabilities.models.some(m => m.id === model && m.ready)
  const report = state?.reports[view]
  const reportStale = report?.stale || (view !== 'signal_export' && (dirty || stale || report?.revision !== document?.revision))

  useEffect(() => {
    const controller = new AbortController()
    fetchAPI<State>(base, { signal: controller.signal }).then(result => { if (!controller.signal.aborted) { setState(result); setPollError('') } })
      .catch(cause => { if (!controller.signal.aborted) setPollError(speechError(cause)) })
    return () => controller.abort()
  }, [base, document?.revision, refreshKey])
  useEffect(() => { onBusy(pending || running); return () => onBusy(false) }, [pending, running, onBusy])
  usePolling<State>(running || check || Boolean(pollError) ? base : null, 1800, result => {
    setState(result); setCheck(false); setPollError('')
  }, cause => setPollError(speechError(cause)))

  async function start(kind: 'signal' | 'words' | 'speakers', target = 'speech') {
    if (!document) return
    setPending(true); setError(''); setPage(0)
    setView(kind === 'signal' ? target === 'speech' ? 'signal_speech' : 'signal_export' : kind)
    try {
      const job = await fetchAPI<Job>(base, { method: 'POST', body: JSON.stringify({ kind, target, revision: document.revision, model, language: 'vi', num_speakers: speakerCount }) })
      setState(current => current && ({ ...current, job })); setCheck(true)
    } catch (cause) { setError(speechError(cause)) }
    finally { setPending(false) }
  }
  const signal = view.startsWith('signal_') ? report as SignalReport | undefined : undefined
  const issues = signal?.items.flatMap(item => item.warnings.map(warning => ({ ...warning, item }))) ?? []
  const words = view === 'words' ? report as WordsReport | undefined : undefined
  const wordIssues = words?.cues.filter(cue => cue.needs_review) ?? []
  const rowCount = signal ? issues.length : wordIssues.length
  const currentPage = Math.min(page, Math.max(0, Math.ceil(rowCount / 20) - 1))
  function playCue(id: string, offset = 0, end?: number) {
    const url = audio.find(item => item.id === id)?.audio_url
    if (url) onPlay(url, offset, end)
  }
  return <section className="pp-analysis">
    <div className="pp-workbench-heading"><ShieldCheck size={21} /><div><h3>Kiểm tra chất lượng & người nói</h3><p>Nghe lại các đoạn cần chú ý và duyệt người nói trước khi gán giọng.</p></div></div>
    <div className="pp-analysis-tools">
      <div><h4><AudioLines size={16} /> Kiểm tra âm thanh</h4><p>Phát hiện im lặng, âm quá nhỏ hoặc lớn và dấu hiệu rè.</p>
        <Button size="sm" variant="outline" disabled={speechLocked || !hasAudio || !state?.capabilities.signal_ready} onClick={() => void start('signal')}>Kiểm tra lời đọc</Button>
        <Button size="sm" variant="outline" disabled={locked || !state?.has_export || !state?.capabilities.signal_ready} onClick={() => void start('signal', 'export')}>Kiểm tra video đã ghép</Button>
        {!hasAudio && <small>Tạo lời đọc để kiểm tra từng câu.</small>}
      </div>
      <div><h4><ShieldCheck size={16} /> Đối chiếu lời đọc</h4><p>Whisper nghe lại lời tiếng Việt và đánh dấu câu có thể đọc thiếu hoặc sai.</p>
        <select aria-label="Model kiểm tra lời đọc" value={model} disabled={locked} onChange={e => setModel(e.target.value)}>{(state?.capabilities.models ?? []).map(m => <option key={m.id} value={m.id} disabled={!m.ready}>{m.name}{!m.ready ? ' · chưa tải' : ''}</option>)}</select>
        <Button size="sm" variant="outline" disabled={speechLocked || !hasAudio || !readyModel || !state?.capabilities.whisper_available} onClick={() => void start('words')}>Nghe lại & đối chiếu</Button>
      </div>
      <div><h4><Users size={16} /> Nhận diện người nói</h4><p>Phân nhóm giọng từ âm thanh video gốc, sau đó sửa tên và chọn giọng đọc.</p>
        <select aria-label="Số người nói dự kiến" value={speakerCount} disabled={locked} onChange={e => setSpeakerCount(Number(e.target.value))}><option value={0}>Tự xác định số người nói</option>{Array.from({ length: 10 }, (_, i) => <option key={i + 1} value={i + 1}>{i + 1} người nói</option>)}</select>
        <Button size="sm" variant="outline" disabled={speechLocked || !state?.capabilities.speakers_ready} onClick={() => void start('speakers')}>Phân nhóm người nói</Button>
        {state && !state.capabilities.speakers_ready && <small>Chưa cài bộ nhận diện người nói. Bạn vẫn có thể gán người nói trong trình sửa.</small>}
      </div>
    </div>
    {(dirty || stale) && <p className="pp-workbench-notice">Lưu kịch bản và xác nhận mốc cảnh trước khi kiểm tra lời đọc hoặc phân người nói.</p>}
    {error && <p role="alert">{error}</p>}{pollError && <p role="alert">Không tải được kết quả: {pollError}. Đang kết nối lại…</p>}
    {ownJob && <div className="pp-workbench-progress" role="status"><strong>{ownJob.stage} · {ownJob.progress}%</strong><progress max={100} value={ownJob.progress} />
      {active(ownJob) && <><small>Tiến trình theo công đoạn; nạp model có thể giữ nguyên phần trăm.</small><Button size="sm" variant="outline" onClick={async () => {
        try { await fetchAPI(`${base}/cancel`, { method: 'POST' }); setCheck(true) } catch (cause) { setError(speechError(cause)) }
      }}>Dừng kiểm tra</Button></>}
      {ownJob.status === 'CANCELLED' && <p>Đã dừng. Có thể chạy lại khi cần.</p>}{ownJob.error && <p role="alert">{ownJob.error}</p>}
    </div>}
    <div className="pp-analysis-tabs" aria-label="Báo cáo đã lưu">{Object.entries(labels).map(([key, label]) => <Button key={key} size="sm" variant={view === key ? 'default' : 'outline'} disabled={!state?.reports[key as keyof State['reports']]} onClick={() => { setView(key as typeof view); setPage(0) }}>{label}</Button>)}</div>
    {report ? <>
      <p className="pp-analysis-meta">Đã lưu {new Date(report.created_at * 1000).toLocaleString('vi-VN')}{reportStale ? ' · Nội dung hoặc âm thanh đã thay đổi, cần kiểm tra lại.' : ' · Kết quả của bản đã lưu hiện tại.'}</p>
      {!!report.skipped.length && <p className="pp-analysis-warning">Chưa kiểm tra {report.skipped.length} câu chưa có lời đọc hợp lệ.</p>}
      {signal && <><p>Đã kiểm tra {signal.items.length} file · {issues.length} cảnh báo. Các dấu hiệu này cần nghe lại; khoảng lặng có thể là chủ ý.</p>
        {signal.items.some(item => item.limited) && <p className="pp-analysis-warning">Đã đạt giới hạn thời lượng hoặc số cảnh báo của một file; báo cáo chưa bao phủ toàn bộ.</p>}
        <div className="pp-analysis-issues">{issues.slice(currentPage * 20, currentPage * 20 + 20).map((issue, i) => <div key={`${issue.item.id}:${i}`}>
          <strong>{warnings[issue.kind] ?? issue.kind}</strong><span>{(issue.item.start + issue.start).toFixed(1)}–{(issue.item.start + issue.end).toFixed(1)}s</span><p>{issue.item.text}</p>
          <Button size="sm" variant="outline" disabled={Boolean(reportStale)} onClick={() => issue.item.id === 'export' ? onPlay(`${signal?.export_url ?? `/api/videos/${videoId}/file`}?v=${report.created_at}`, issue.start, issue.end) : playCue(issue.item.id, issue.start, issue.end)}>Nghe đoạn này</Button>
        </div>)}</div>{!issues.length && <p className="pp-analysis-ok">Không phát hiện các lỗi tín hiệu trong phạm vi đã quét.</p>}</>}
      {words && <><p>Đã đối chiếu {words.cues.length} câu · {wordIssues.length} câu cần nghe lại. Whisper có thể nhận sai tên riêng hoặc giọng vùng miền.</p>
        <div className="pp-analysis-issues">{wordIssues.slice(currentPage * 20, currentPage * 20 + 20).map(cue => <div key={cue.id}><strong>{cue.start.toFixed(1)}–{cue.end.toFixed(1)}s{cue.uncertain ? ' · Nhận dạng chưa chắc chắn' : ' · Nội dung có khác biệt'}</strong>
          <p><b>Cần đọc:</b> {cue.expected}</p><p><b>Whisper nghe:</b> {cue.recognized || '(Không nhận được lời)'}</p>
          <Button size="sm" variant="outline" disabled={Boolean(reportStale)} onClick={() => playCue(cue.id)}>Nghe lời đã tạo</Button><Button size="sm" variant="outline" onClick={() => onSeek(cue.start)}>Đến mốc trong video</Button>
        </div>)}</div>{!wordIssues.length && <p className="pp-analysis-ok">Không thấy khác biệt rõ trong nội dung lời đọc đã kiểm tra.</p>}</>}
      {rowCount > 20 && <div className="pp-candidate-toolbar"><Button size="sm" variant="outline" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Trước</Button><span>{currentPage + 1}/{Math.ceil(rowCount / 20)}</span><Button size="sm" variant="outline" disabled={(currentPage + 1) * 20 >= rowCount} onClick={() => setPage(currentPage + 1)}>Sau</Button></div>}
      {view === 'speakers' && state?.reports.speakers && document && <SpeakerSuggestions key={state.reports.speakers.created_at} report={state.reports.speakers} document={document} voices={voices} disabled={locked || Boolean(reportStale)} onSeek={onSeek} onPlay={onPlay} onApply={async candidate => {
        const fresh = await fetchAPI<State>(base)
        setState(fresh)
        if (fresh.reports.speakers?.fingerprint !== report.fingerprint || fresh.reports.speakers?.stale || active(fresh.job)) throw new Error('Kịch bản hoặc kết quả đã đổi. Tải lại và kiểm tra trước khi áp dụng.')
        onDraft(candidate)
      }} />}
    </> : <p className="pp-analysis-meta">Báo cáo sẽ được lưu tại đây sau khi kiểm tra xong.</p>}
  </section>
}
