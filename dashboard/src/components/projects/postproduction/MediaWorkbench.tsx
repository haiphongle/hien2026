import { useEffect, useMemo, useRef, useState } from 'react'
import { Languages, ScanText } from 'lucide-react'
import { fetchAPI } from '../../../api/client'
import { usePolling } from '../../../api/usePolling'
import { Button } from '../../ui/button'
import { speechError, type SpeechDocument } from './speechTypes'
import { applySpeechCandidate, candidateRange, type SpeechCandidate } from './transcriptionDraft'
import { SpeechShorteningReview } from './SpeechShorteningReview'
import type { ShorteningCandidate } from './speechRepair'

interface Job { id: string; kind: string; status: string; progress: number; stage: string; error: string | null }
interface TranslationProgress {
  completed: number; total: number; compatible: boolean
  language: string; provider_selector: string; synopsis: string; glossary: string
  selection?: Record<string, number>
}
interface TranscriptionProgress {
  completed: number; total: number; model: string; language: string; start: number; end: number; timeline: string
  compatible?: boolean
}
interface State {
  source: { status: string; filename: string; has_audio: boolean; width: number; height: number; duration: number } | null
  audio_source?: { kind: 'timeline' | 'source'; duration: number; has_audio: boolean; edit_signature?: string } | null
  job: Job | null; whisper_available: boolean
  models: { id: string; name: string; ready: boolean }[]
  providers: { id: string; name: string; available: boolean }[]
  languages: Record<string, string>; transcript: SpeechCandidate | null; translation: SpeechCandidate | null
  translation_progress?: TranslationProgress | null
  transcription_progress?: TranscriptionProgress | null
  shortening: ShorteningCandidate | null; shortening_progress?: TranslationProgress | null
}
interface Props {
  videoId: string; document: SpeechDocument | null; timeline?: string; dirty: boolean; busy: boolean
  onDraft: (doc: SpeechDocument) => void; onBusy: (value: boolean) => void; onImported: () => void
  onSeek: (time: number) => void
  selectedIds: string[]
}
const active = (job: Job | null | undefined) => job?.status === 'PROCESSING' || job?.status === 'QUEUED'
const secondsValue = (time: string) => /^\d{1,2}:[0-5]\d:[0-5]\d$/.test(time) ? time.split(':').reduce((total, part) => total * 60 + Number(part), 0) : NaN
function preferences(videoId: string) {
  try { return JSON.parse(localStorage.getItem(`flowkit:translation:${videoId}`) ?? '{}') as Record<string, string> }
  catch { return {} }
}

export function MediaWorkbench({ videoId, document, timeline, dirty, busy, onDraft, onBusy, onImported, onSeek, selectedIds }: Props) {
  const base = `/api/videos/${videoId}/workbench`
  const [state, setState] = useState<State | null>(null)
  const [error, setError] = useState('')
  const [pollError, setPollError] = useState('')
  const [pending, setPending] = useState(false)
  const [check, setCheck] = useState(true)
  const [savedPreferences] = useState(() => preferences(videoId))
  const [model, setModel] = useState('small')
  const [language, setLanguage] = useState('auto')
  const [range, setRange] = useState(false)
  const [rangeStart, setRangeStart] = useState('00:00:00')
  const [rangeEnd, setRangeEnd] = useState('00:05:00')
  const [target, setTarget] = useState(savedPreferences.target ?? 'vi')
  const [provider, setProvider] = useState(savedPreferences.provider ?? 'configured')
  const [synopsis, setSynopsis] = useState(savedPreferences.synopsis ?? '')
  const [glossary, setGlossary] = useState(savedPreferences.glossary ?? '')
  const [candidateKind, setCandidateKind] = useState<'transcript' | 'translation' | 'shortening'>('transcript')
  const [page, setPage] = useState(0)
  const notified = useRef('')
  const job = state?.job
  const running = active(job)
  const checking = check || Boolean(pollError)
  const locked = busy || pending || running || checking || !document
  const candidate = candidateKind === 'shortening' ? null : state?.[candidateKind]
  // Invalid legacy metadata is reported when applying, without breaking render.
  let draftRange: { start: number; end: number } | null = null
  try { if (candidate) draftRange = candidateRange(candidate) } catch { /* Handled by apply below. */ }
  const savedTranslation = state?.translation_progress
  const savedTranscription = state?.transcription_progress
  const audioSource = state?.audio_source ?? state?.source
  const asrEnd = range ? secondsValue(rangeEnd) : audioSource?.duration
  const asrStart = range ? secondsValue(rangeStart) : 0
  const asrResumable = savedTranscription && savedTranscription.compatible !== false && savedTranscription.timeline === timeline && savedTranscription.model === model &&
    savedTranscription.language === language && savedTranscription.start === asrStart &&
    (asrEnd === undefined || Math.abs(savedTranscription.end - asrEnd) < .001)
  const invalidRange = range && (!Number.isFinite(asrStart) || !Number.isFinite(asrEnd) || asrStart >= (asrEnd ?? 0) || (asrEnd ?? 0) > (audioSource?.duration ?? Infinity))
  const resumable = Boolean(savedTranslation?.compatible && savedTranslation.language === target &&
    savedTranslation.provider_selector === provider && savedTranslation.synopsis === synopsis && savedTranslation.glossary === glossary)
  const translateLocked = locked || dirty || !document?.cues.length || !document.revision || !state?.providers.find(p => p.id === provider)?.available
  const shorteningProgress = state?.shortening_progress
  const shortenResumable = Boolean(shorteningProgress?.compatible && shorteningProgress.provider_selector === provider &&
    shorteningProgress.synopsis === synopsis && shorteningProgress.glossary === glossary &&
    Object.keys(shorteningProgress.selection ?? {}).length === selectedIds.length && selectedIds.every(id => Object.hasOwn(shorteningProgress.selection ?? {}, id)))
  const currentCues = useMemo(() => new Map(document?.cues.map(cue => [cue.id, cue])), [document?.cues])
  const candidateApplied = Boolean(candidate && document && candidate.document.timeline === timeline &&
    (draftRange || candidate.document.cues.length === document.cues.length) && candidate.document.cues.every(cue => {
      const current = currentCues.get(cue.id)
      return current?.text === cue.text && current.start === cue.start && current.end === cue.end
    }))
  const canApply = Boolean(candidate && !locked && !dirty && document &&
    candidate.document.revision === document.revision && candidate.document.timeline === timeline &&
    (!candidate.document.edit_signature || candidate.document.edit_signature === state?.audio_source?.edit_signature))
  const pageCount = Math.max(1, Math.ceil((candidate?.document.cues.length ?? 0) / 10))
  const currentPage = Math.min(page, pageCount - 1)

  useEffect(() => { onBusy(pending || running || checking); return () => onBusy(false) }, [pending, running, checking, onBusy])
  useEffect(() => { setCheck(true) }, [document?.revision, timeline])
  useEffect(() => {
    try { localStorage.setItem(`flowkit:translation:${videoId}`, JSON.stringify({ target, provider, synopsis, glossary })) }
    catch { /* Preferences are optional. */ }
  }, [videoId, target, provider, synopsis, glossary])
  usePolling<State>(check || running || Boolean(pollError) ? base : null, 1500, loaded => {
    setState(loaded); setCheck(false); setPollError('')
    if (loaded.job?.status === 'COMPLETED' && loaded.job.id !== notified.current) {
      notified.current = loaded.job.id
      if (loaded.job.kind === 'import') onImported()
      if (loaded.job.kind === 'transcribe' || loaded.job.kind === 'translate' || loaded.job.kind === 'shorten') {
        setCandidateKind(loaded.job.kind === 'transcribe' ? 'transcript' : loaded.job.kind === 'shorten' ? 'shortening' : 'translation'); setPage(0)
      }
    }
  }, cause => setPollError(speechError(cause)))

  async function start(kind: 'transcribe' | 'translate' | 'shorten', restart = false) {
    if (!document) return
    setPending(true); setError('')
    try {
      const created = await fetchAPI<Job>(`${base}/${kind}`, { method: 'POST', body: JSON.stringify(kind === 'transcribe'
        ? { revision: document.revision, model, language, start: asrStart, end: range ? asrEnd : null, restart }
        : { revision: document.revision, provider, language: target, synopsis, glossary, restart,
            ...(kind === 'shorten' ? { cue_ids: selectedIds } : {}) }) })
      setState(current => current && ({ ...current, job: created }))
    } catch (cause) { setError(speechError(cause)) }
    finally { setPending(false); setCheck(true) }
  }

  return <section className="pp-workbench">
    <div className="pp-workbench-heading"><ScanText size={21} /><div><h3>Tạo phụ đề & dịch video</h3>
      <p>Nhận dạng → kiểm tra lời và thời gian → dịch → chọn giọng hoặc xuất phụ đề.</p></div></div>
    {state?.source && <p className="pp-source-info">Video nhập: <strong>{state.source.filename}</strong> · {state.source.width} × {state.source.height}
      {state.source.status === 'READY' ? ' · Đã lưu bản nguồn' : ' · Chưa sẵn sàng'}
      {!audioSource?.has_audio && ' · Chưa có âm thanh đang bật; hãy thêm lời đọc hoặc nhập SRT.'}</p>}
    <div className="pp-workbench-grid">
      <div><h4>1. Nhận dạng lời nói</h4>
        {state?.audio_source?.kind === 'timeline' && <p>Whisper nghe âm thanh bản dựng đã lưu: giọng đọc, nhạc và âm gốc theo các lớp đang bật. Không cần xuất MP4 trước.</p>}
        <label>Ngôn ngữ gốc<select value={language} onChange={e => setLanguage(e.target.value)} disabled={locked}>
          {Object.entries(state?.languages ?? { auto: 'Tự nhận diện' }).map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </select></label>
        <label>Model trên máy<select value={model} onChange={e => setModel(e.target.value)} disabled={locked}>
          {(state?.models ?? []).map(m => <option key={m.id} value={m.id} disabled={!m.ready}>{m.name}{!m.ready && ' · chưa tải'}</option>)}
        </select></label>
        <label>Phạm vi nhận dạng<select value={range ? 'range' : 'all'} disabled={locked} onChange={e => setRange(e.target.value === 'range')}>
          <option value="all">Toàn bộ video</option><option value="range">Chỉ một đoạn · thử trước</option>
        </select></label>
        {range && <div className="pp-workbench-grid">
          <label>Từ giờ:phút:giây<input type="text" inputMode="numeric" placeholder="00:00:00" maxLength={8} value={rangeStart} disabled={locked} onChange={e => setRangeStart(e.target.value)} /></label>
          <label>Đến giờ:phút:giây<input type="text" inputMode="numeric" placeholder="00:05:00" maxLength={8} value={rangeEnd} disabled={locked} onChange={e => setRangeEnd(e.target.value)} /></label>
        </div>}
        {invalidRange && <p role="alert">Nhập giờ:phút:giây (ví dụ 01:30:00). Điểm cuối phải sau điểm đầu và nằm trong video.</p>}
        <Button disabled={locked || dirty || invalidRange || !state?.whisper_available || !state.models.find(m => m.id === model)?.ready || audioSource?.has_audio === false}
          onClick={() => void start('transcribe')}><ScanText size={15} />{asrResumable && savedTranscription && savedTranscription.completed < savedTranscription.total ? 'Tiếp tục nhận dạng' : 'Nhận dạng bằng Whisper'}</Button>
        {asrResumable && Boolean(savedTranscription?.completed) && <Button size="sm" variant="outline" disabled={locked || dirty || invalidRange} onClick={() => void start('transcribe', true)}>Nhận dạng lại từ đầu</Button>}
        {asrResumable && savedTranscription && <p>Đã lưu {savedTranscription.completed}/{savedTranscription.total} phần. Phần hoàn tất được giữ lại khi dừng hoặc khởi động lại máy chủ.</p>}
        <p>Chạy tại máy, chia từng phần 10 phút. Bản nháp chỉ thay đổi phụ đề sau khi bạn chọn áp dụng. Sau khi lưu, có thể phân nhóm người nói hoặc gán thủ công.</p>
      </div>
      <div><h4>2. Dịch phụ đề đã lưu</h4>
        <label>Dịch sang<select value={target} onChange={e => setTarget(e.target.value)} disabled={locked}>
          {Object.entries(state?.languages ?? { vi: 'Tiếng Việt' }).filter(([id]) => id !== 'auto').map(([id, name]) => <option key={id} value={id}>{name}</option>)}
        </select></label>
        <label>Dịch bằng<select value={provider} onChange={e => setProvider(e.target.value)} disabled={locked}>
          {(state?.providers ?? []).map(p => <option key={p.id} value={p.id} disabled={!p.available}>{p.name}{!p.available && ' · chưa cài'}</option>)}
        </select></label>
        <a href="/settings" className="text-xs underline">Chọn model AI trong Cài đặt</a>
        <Button disabled={translateLocked}
          onClick={() => void start('translate')}><Languages size={15} />{resumable && savedTranslation && savedTranslation.completed < savedTranslation.total ? 'Tiếp tục dịch' : 'Dịch bản đã lưu'}</Button>
        {resumable && Boolean(savedTranslation?.completed) && <Button size="sm" variant="outline" disabled={translateLocked} onClick={() => void start('translate', true)}>Dịch lại từ đầu</Button>}
        {savedTranslation && <p className="pp-workbench-notice">Đã lưu {savedTranslation.completed}/{savedTranslation.total} câu dịch.
          {resumable ? ' Phần đã lưu sẽ được dùng lại; chỉ dịch các câu còn thiếu.' : ' Kịch bản hoặc thiết lập đã thay đổi; lần dịch mới sẽ bắt đầu lại.'}
          {' '}Chỉ đưa bản dịch vào trình sửa khi đủ tất cả câu.</p>}
        <p>Nội dung phụ đề và ngữ cảnh được gửi đến tài khoản AI đã đăng nhập; dùng hạn mức của tài khoản đó. Giọng VieNeu hiện hỗ trợ tiếng Việt.</p>
      </div>
    </div>
    <div className="pp-candidate-toolbar">
      <Button disabled={translateLocked || !selectedIds.length} onClick={() => void start('shorten')}>
        {shortenResumable && shorteningProgress && shorteningProgress.completed < shorteningProgress.total ? 'Tiếp tục AI rút gọn' : 'AI rút gọn câu đã chọn'} · {selectedIds.length} câu
      </Button>
      {shortenResumable && Boolean(shorteningProgress?.completed) && <Button size="sm" variant="outline" disabled={translateLocked || !selectedIds.length} onClick={() => void start('shorten', true)}>Rút gọn lại bằng AI</Button>}
      <small>Chọn câu ở “Biên tập lời đọc”, lưu kịch bản rồi rút gọn. Dùng AI, model và ngữ cảnh đã chọn ở trên. Giữ nguyên ngôn ngữ; chỉ áp dụng sau khi kiểm tra.</small>
    </div>
    {shorteningProgress && <p className="pp-workbench-notice">Đã lưu {shorteningProgress.completed}/{shorteningProgress.total} câu rút gọn.
      {shortenResumable ? shorteningProgress.completed < shorteningProgress.total ? ' Có thể tiếp tục phần còn thiếu.' : ' Bản nháp đã hoàn tất; chọn “Bản rút gọn AI” để kiểm tra.' : ' Chọn lại nhóm câu hoặc chạy từ bản kịch bản hiện tại.'}</p>}
    <details className="pp-disclosure"><summary>Ngữ cảnh & từ điển cho bản dịch</summary><div className="pp-workbench-grid pp-disclosure-body">
      <label>Nội dung phim / cách xưng hô<textarea rows={3} maxLength={4000} value={synopsis} onChange={e => setSynopsis(e.target.value)} disabled={locked} placeholder="Phim hài gia đình. Hai anh em xưng hô anh–em…" /></label>
      <label>Tên riêng & thuật ngữ<textarea rows={3} maxLength={4000} value={glossary} onChange={e => setGlossary(e.target.value)} disabled={locked} placeholder="Tom = Tôm\nJelly = Jelly\nGiữ nguyên tên nhân vật…" /></label>
    </div></details>
    {dirty && <p className="pp-workbench-notice">Có nội dung chưa lưu. Bấm “Lưu kịch bản & giọng” bên dưới trước khi nhận dạng hoặc dịch.</p>}
    {error && <p role="alert">{error}</p>}
    {check && !pollError && <p role="status">Đang kiểm tra trạng thái tác vụ…</p>}
    {pollError && <p role="alert">Không đọc được tiến trình: {pollError}. Đang kết nối lại…</p>}
    {job && !job.kind.startsWith('analysis_') && <div className="pp-workbench-progress" role="status"><strong>{job.status === 'FAILED' ? 'Tác vụ đã dừng do lỗi' : job.stage} · {job.progress}%</strong><progress value={job.progress} max={100} />
      {job.status === 'FAILED' && <small>{job.stage}</small>}
      {running && <><small>Tiến trình theo các bước xử lý; lúc nạp model hoặc chờ AI có thể giữ nguyên.</small><Button size="sm" variant="outline" onClick={async () => {
        try { await fetchAPI(`${base}/cancel`, { method: 'POST' }); setCheck(true) } catch (cause) { setError(speechError(cause)) }
      }}>Dừng tác vụ</Button></>}
      {job.error && <p role="alert">{job.error}</p>}
      {job.status === 'CANCELLED' && <p>Đã dừng. Kịch bản đã lưu vẫn được giữ.</p>}
    </div>}
    {(state?.transcript || state?.translation || state?.shortening) && <div className="pp-candidate">
      <div className="pp-candidate-toolbar"><h4>Bản nháp để kiểm tra</h4><select aria-label="Chọn bản nháp" value={candidateKind} onChange={e => { setCandidateKind(e.target.value as typeof candidateKind); setPage(0) }}>
        <option value="transcript" disabled={!state.transcript}>Lời nhận dạng gốc</option><option value="translation" disabled={!state.translation}>Bản dịch</option>
        <option value="shortening" disabled={!state.shortening}>Bản rút gọn AI</option>
      </select></div>
      {candidateKind === 'shortening' && state.shortening && document && <SpeechShorteningReview key={`${videoId}:${state.shortening.id ?? state.shortening.original.revision}`} videoId={videoId} candidate={state.shortening} document={document}
        disabled={locked || state.shortening.original.timeline !== timeline} onDraft={onDraft} onSeek={onSeek} />}
      {candidateKind === 'shortening' && state.shortening && !locked && state.shortening.original.timeline !== timeline && <p role="alert">Mốc video đã đổi từ lúc rút gọn. Lưu kịch bản theo mốc hiện tại rồi chạy lại AI.</p>}
      {candidate && <><p>{candidate.document.cues.length} câu · Đã lưu bản nháp trên máy · {candidateApplied ? 'Nội dung đã có trong trình sửa' : 'Chưa áp dụng vào trình sửa'}</p>
        {draftRange && <p>Chỉ thay phụ đề từ {draftRange.start.toFixed(1)}s đến {draftRange.end.toFixed(1)}s; giữ các câu ngoài đoạn này. Nếu mốc cắt ngang câu cũ, kiểm tra lại lời ở hai mép đoạn. Có thể hoàn tác sau khi áp dụng.</p>}
        <div className="pp-translation-table"><table><thead><tr><th>Mốc</th>{candidate.original && <th>Bản gốc</th>}<th>{candidate.original ? 'Bản dịch' : 'Lời nhận dạng'}</th></tr></thead>
          <tbody>{candidate.document.cues.slice(currentPage * 10, currentPage * 10 + 10).map((cue, i) => <tr key={cue.id}>
            <td><button type="button" onClick={() => onSeek(cue.start)}>{cue.start.toFixed(1)}–{cue.end.toFixed(1)}s</button></td>
            {candidate.original && <td>{candidate.original.cues[currentPage * 10 + i]?.text}</td>}<td>{cue.text}</td>
          </tr>)}</tbody></table></div>
        <div className="pp-candidate-toolbar"><Button size="sm" variant="outline" disabled={currentPage === 0} onClick={() => setPage(currentPage - 1)}>Trước</Button><span>{currentPage + 1}/{pageCount}</span><Button size="sm" variant="outline" disabled={currentPage + 1 === pageCount} onClick={() => setPage(currentPage + 1)}>Sau</Button>
          <Button disabled={!canApply} onClick={() => {
            if (!document) return
            try { onDraft(applySpeechCandidate(document, candidate)); setError('') }
            catch (cause) { setError(speechError(cause)) }
          }}>{draftRange ? 'Áp dụng phụ đề trong đoạn này' : 'Đưa vào trình sửa bên dưới'}</Button></div>
        {!canApply && !candidateApplied && !dirty && !locked && <p>Bản nháp thuộc phiên bản kịch bản hoặc mốc cảnh cũ. Chạy lại nhận dạng / dịch để tránh ghi đè nội dung mới.</p>}
      </>}
    </div>}
  </section>
}
