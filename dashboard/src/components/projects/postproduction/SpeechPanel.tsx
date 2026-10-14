import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { fetchAPI, resolveMediaUrl } from '../../../api/client'
import { usePolling } from '../../../api/usePolling'
import type { Video } from '../../../types'
import { Button } from '../../ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../ui/card'
import { Check, FileText, Plus, Save } from 'lucide-react'
import { CustomVoiceLibrary } from './CustomVoiceLibrary'
import { PresetVoiceLibrary } from './PresetVoiceLibrary'
import { MediaWorkbench } from './MediaWorkbench'
import { SpeechAnalysisPanel } from './SpeechAnalysisPanel'
import { assignNarratorVoice, renameSpeaker } from './speechSpeakers'
import { limitSpeechPreview } from './speechPlayback'
import { initialSpeechJobMonitor, speechJobMonitor } from './speechJobMonitor'
import { SpeechRepairDialog, type RepairSelection } from './SpeechRepairDialog'
import { fitSelectedSpeech, matchingAudio, speechReadiness, MAX_CAPTION_CHARS, MAX_CHARS_PER_SECOND, MAX_SPEECH_CUES } from './speechRepair'
import { SPEECH_LANGUAGES, speechBusy, speechError, speechStyle, type SpeechAudio, type SpeechCue, type SpeechDocument, type SpeechJob, type SpeechState, type SpeechVoice } from './speechTypes'

const stages: Record<string, string> = { queued: 'Đang chờ lượt', loading_model: 'Đang nạp model', model_ready: 'Model đã sẵn sàng', generating: 'Đang tạo câu thoại', retrying: 'Đang thử lại giọng bất thường', checking_audio: 'Đang đối chiếu lời đọc', done: 'Đã xử lý xong', interrupted: 'Tác vụ bị gián đoạn', cancelled: 'Đã dừng · các câu hoàn tất được giữ' }
const normalizeName = (value: string) => value.toLocaleLowerCase('vi').normalize('NFD').replace(/\p{M}/gu, '').replaceAll('đ', 'd').trim()
const percent = (job: SpeechJob | null) => job?.progress ?? (job?.total ? Math.round(job.completed / job.total * 100) : 0)

interface Props {
  video: Video; active: boolean; previewTime: number
  onSeek: (time: number) => void; onPreviewPause: () => void; previewPlaying: boolean
  onCaptionChange: (caption: string) => void
  onVideoUpdated: () => void | Promise<void>
  suggestedNarratorVoiceId?: string
  onDismissNarratorVoice?: () => void
  onApplyTimeline?: () => Promise<void>
}

export function SpeechPanel({ video, active, previewTime, onSeek, onPreviewPause, previewPlaying, onCaptionChange, onVideoUpdated, suggestedNarratorVoiceId, onDismissNarratorVoice, onApplyTimeline }: Props) {
  const base = `/api/videos/${video.id}/speech`
  const [state, setState] = useState<SpeechState | null>(null)
  const [doc, setDoc] = useState<SpeechDocument | null>(null)
  const [voices, setVoices] = useState<SpeechVoice[]>([])
  const [custom, setCustom] = useState<SpeechVoice[]>([])
  const [jobMonitor, updateJobMonitor] = useReducer(speechJobMonitor, initialSpeechJobMonitor)
  const job = jobMonitor.job
  const [dirty, setDirty] = useState(false)
  const [inputSnapshot] = useState((): { text: string; mode: 'dialogue' | 'srt' } => {
    try {
      return { text: localStorage.getItem(`flowkit:speech-input:${video.id}`) ?? '',
        mode: localStorage.getItem(`flowkit:speech-input-mode:${video.id}`) === 'srt' ? 'srt' : 'dialogue' }
    } catch { return { text: '', mode: 'dialogue' } }
  })
  const [mode, setMode] = useState(inputSnapshot.mode)
  const [draft, setDraft] = useState(inputSnapshot.text)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [mediaBusy, setMediaBusy] = useState(false)
  const [analysisBusy, setAnalysisBusy] = useState(false)
  const [page, setPage] = useState(0)
  const [reviewPage, setReviewPage] = useState(0)
  const [demoSpeaker, setDemoSpeaker] = useState('')
  const [voiceLanguage, setVoiceLanguage] = useState(() => {
    try { const saved = localStorage.getItem(`flowkit:speech-language:${video.id}`); return saved && saved in SPEECH_LANGUAGES ? saved : 'vi' } catch { return 'vi' }
  })
  const [selected, setSelected] = useState<Set<string>>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(`flowkit:speech-selection:${video.id}`) ?? '[]')
      return new Set(Array.isArray(saved) ? saved.filter((id): id is string => typeof id === 'string') : [])
    } catch { return new Set() }
  })
  const [repair, setRepair] = useState<RepairSelection | null>(null)
  const [undoRepair, setUndoRepair] = useState<{ before: SpeechDocument; after: SpeechDocument; wasDirty: boolean } | null>(null)
  const [playing, setPlaying] = useState(false)
  const audio = useRef<HTMLAudioElement | null>(null)
  const running = speechBusy(job)
  const busy = pending || running || jobMonitor.checking || mediaBusy || analysisBusy
  const currentPage = Math.min(page, Math.max(0, Math.ceil((doc?.cues.length ?? 0) / 30) - 1))
  const allVoices = [...voices, ...custom.filter(v => v.status === 'READY')]
  const refreshNativeVoices = useCallback((library: SpeechVoice[]) => {
    const updates = new Map(library.map(voice => [voice.id, voice]))
    setVoices(current => current.map(voice => updates.get(voice.id) ?? voice))
  }, [])
  const languageVoices = allVoices.filter(voice => (voice.languages ?? ['vi']).includes(voiceLanguage))
  const suggestedNarrator = allVoices.find(voice => voice.id === suggestedNarratorVoiceId)
  const narratorApplied = Boolean(doc?.cues.length && doc.cues.every(cue => cue.voice_id === suggestedNarratorVoiceId))
  const speakers = [...new Set(doc?.cues.map(c => c.speaker) ?? [])]
  const demoTarget = speakers.includes(demoSpeaker) ? demoSpeaker : speakers[0] ?? ''
  const demoCues = doc?.cues.filter(cue => cue.speaker === demoTarget) ?? []
  const demoVoice = new Set(demoCues.map(cue => cue.voice_id)).size === 1 ? demoCues[0].voice_id : ''
  const audios = new Map<string, SpeechAudio>(state?.audio.map(a => [a.id, a]) ?? [])
  const stale = Boolean(doc?.timeline && state && (doc.timeline !== state.timeline ||
    doc.edit_signature && doc.edit_signature !== state.edit_signature))
  const currentCue = doc?.cues.find(c => previewTime >= c.start && previewTime < c.end)
  const readiness = useMemo(() => doc ? speechReadiness(doc, state) : { ready: [], long: [], missing: [] }, [doc, state])
  const missingIds = new Set(readiness.missing), longIds = new Set(readiness.long)
  const overlongCount = readiness.long.length
  const selectedIds = doc?.cues.filter(cue => selected.has(cue.id)).map(cue => cue.id) ?? []
  const originalCues = new Map(state?.automatic?.original.cues.map(cue => [cue.id, cue]) ?? [])
  const reviewChanges = doc?.cues.filter(cue => {
    const before = originalCues.get(cue.id)
    return missingIds.has(cue.id) || longIds.has(cue.id) || Boolean(before &&
      (before.text !== cue.text || before.start !== cue.start || before.end !== cue.end || before.speed !== cue.speed))
  }) ?? []
  const reviewCues = reviewChanges.length ? reviewChanges : doc?.cues.slice(0, 3) ?? []
  const reviewPages = Math.max(1, Math.ceil(reviewCues.length / 10))
  const currentReviewPage = Math.min(reviewPage, reviewPages - 1)

  useEffect(() => {
    if (!active || previewPlaying) audio.current?.pause()
  }, [active, previewPlaying])
  useEffect(() => {
    onCaptionChange(currentCue ? `${currentCue.speaker}: ${currentCue.text}` : '')
  }, [currentCue, onCaptionChange])

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchAPI<SpeechState>(base, { signal: controller.signal }),
      fetchAPI<SpeechVoice[]>('/api/tts/voices', { signal: controller.signal }),
      fetchAPI<SpeechVoice[]>('/api/tts/custom-voices', { signal: controller.signal }),
    ]).then(([loaded, presets, saved]) => {
      if (controller.signal.aborted) return
      setState(loaded); setDoc(loaded.document); setMode(inputSnapshot.text.trim() ? inputSnapshot.mode : loaded.document.mode)
      updateJobMonitor({ type: 'checked', job: loaded.job }); setVoices(presets); setCustom(saved)
      try {
        const restored = JSON.parse(localStorage.getItem(`flowkit:speech-draft:${video.id}`) ?? 'null') as SpeechDocument | null
        if (restored && Array.isArray(restored.cues) && restored.revision === loaded.document.revision && (!restored.timeline || restored.timeline === loaded.timeline)) {
          setDoc(restored); setDirty(true); setMessage('Đã khôi phục bản nháp chưa lưu từ trình duyệt. Kiểm tra rồi lưu kịch bản.')
        }
      } catch { /* Ignore an unreadable or obsolete browser draft. */ }
    }).catch(cause => { if (!controller.signal.aborted) setError(String(cause)) })
    return () => { controller.abort(); audio.current?.pause() }
  }, [base, video.id, inputSnapshot])

  useEffect(() => {
    try {
      localStorage.setItem(`flowkit:speech-input:${video.id}`, draft)
      localStorage.setItem(`flowkit:speech-input-mode:${video.id}`, mode)
    } catch { /* optional */ }
  }, [video.id, draft, mode])

  useEffect(() => {
    if (!doc) return
    try {
      const key = `flowkit:speech-draft:${video.id}`
      if (dirty) localStorage.setItem(key, JSON.stringify(doc))
      else localStorage.removeItem(key)
    } catch { /* Saving on the server remains available. */ }
  }, [doc, dirty, video.id])

  useEffect(() => {
    try { localStorage.setItem(`flowkit:speech-selection:${video.id}`, JSON.stringify([...selected])) }
    catch { /* Selection is optional; saved AI progress remains on the server. */ }
  }, [selected, video.id])

  usePolling<SpeechState>(running || jobMonitor.checking ? base : null, 1200, loaded => {
    setState(loaded); updateJobMonitor({ type: 'checked', job: loaded.job })
    if (loaded.job?.mode === 'automatic') { setDoc(loaded.document); setDirty(false) }
  }, cause => updateJobMonitor({ type: 'check-failed', error: speechError(cause) }))

  function edit(id: string, changes: Partial<SpeechCue>) {
    setDoc(current => current && ({ ...current, cues: current.cues.map(c => c.id === id ? { ...c, ...changes } : c) }))
    setDirty(true); setMessage('')
  }
  function assign(speaker: string, voice_id: string) {
    setDoc(current => current && ({ ...current, cues: current.cues.map(c => c.speaker === speaker ? { ...c, voice_id } : c) }))
    setDirty(true)
  }
  async function play(url: string, offset = 0, end?: number) {
    audio.current?.pause(); onPreviewPause()
    const next = new Audio(resolveMediaUrl(url))
    limitSpeechPreview(next, offset, end)
    audio.current = next
    next.onpause = () => { if (audio.current === next) setPlaying(false) }
    next.onended = () => { if (audio.current === next) setPlaying(false) }
    next.onerror = () => { if (audio.current === next) { setPlaying(false); setError('Không phát được audio. Thử tải lại thư viện.') } }
    try { await next.play(); if (audio.current === next) setPlaying(true) }
    catch (cause) { if (audio.current === next) setError(String(cause)) }
  }
  async function save(acknowledge = false, candidate = doc) {
    if (!candidate) throw new Error('Chưa tải được kịch bản.')
    const saved = await fetchAPI<SpeechDocument>(`${base}?acknowledge_timeline=${acknowledge}`, { method: 'PUT', body: JSON.stringify(candidate) })
    setDoc(saved); setDirty(false)
    setState(await fetchAPI<SpeechState>(base))
    return saved
  }
  async function action(task: () => Promise<void>) {
    setPending(true); setError(''); setMessage('')
    try { await task() } catch (cause) { setError(speechError(cause)) }
    finally { setPending(false) }
  }
  async function generate(cueIds?: string[], force = false, candidate?: SpeechDocument, automatic = false) {
    await action(async () => {
      const saved = candidate ? await save(false, candidate) : dirty ? await save() : doc!
      try {
        let settings: Record<string, string> = {}
        if (automatic) {
          try {
            const value = JSON.parse(localStorage.getItem(`flowkit:translation:${video.id}`) ?? '{}')
            if (value && typeof value === 'object') settings = value
            localStorage.setItem(`flowkit:speech-language:${video.id}`, voiceLanguage)
          } catch { /* Use configured AI; storing preferences is optional. */ }
          setReviewPage(0)
        }
        const created = await fetchAPI<SpeechJob>(`${base}/jobs`, { method: 'POST', body: JSON.stringify({ revision: saved.revision, cue_ids: cueIds, force,
          ...(automatic ? { automatic: true, language: voiceLanguage,
            provider: ['configured', 'codex', 'claude', 'agy'].includes(settings.provider) ? settings.provider : 'configured',
            synopsis: settings.synopsis ?? '', glossary: settings.glossary ?? '' } : {}) }) })
        updateJobMonitor({ type: 'queued', job: created })
      } catch (cause) {
        updateJobMonitor({ type: 'submission-uncertain', error: speechError(cause) })
      }
    })
  }
  function applyRepair(candidate: SpeechDocument, generateIds?: string[]) {
    if (!doc) return
    setUndoRepair({ before: doc, after: candidate, wasDirty: dirty })
    setDoc(candidate); setDirty(true); setRepair(null)
    setMessage('Đã áp dụng vào bản nháp. Kiểm tra mốc và lưu kịch bản; có thể hoàn tác ngay bên dưới.')
    if (generateIds?.length) void generate(generateIds, true, candidate)
  }
  function voiceOptions(filtered = false) {
    return <><option value="">Chọn giọng…</option>{(filtered ? languageVoices : allVoices).map(v => <option key={v.id} value={v.id}>{v.name}{v.id.startsWith('qwen3_') && ` · Qwen3 · ${SPEECH_LANGUAGES[v.languages?.[0] as keyof typeof SPEECH_LANGUAGES] ?? ''}`}{v.region ? ` · ${v.gender === 'male' ? 'nam' : 'nữ'} · ${v.region}` : ' · Giọng riêng'}</option>)}</>
  }
  async function applyTimeline() {
    await action(async () => {
      const saved = dirty ? await save() : doc!
      const checked = await fetchAPI<SpeechState>(base)
      setState(checked)
      if (checked.document.revision !== saved.revision || checked.document.timeline !== checked.timeline ||
          speechBusy(checked.job) || checked.audio.length !== saved.cues.length || checked.audio.some(a => a.status !== 'READY')) {
        throw new Error('Kịch bản hoặc giọng đã đổi. Kiểm tra các câu còn thiếu / quá dài trước khi đưa lên timeline.')
      }
      await onApplyTimeline?.()
      setMessage('Đã đưa đủ phụ đề và giọng lên timeline. Có thể hoàn tác trong bản dựng timeline.')
    })
  }

  return <Card>
    <CardHeader>
      <CardTitle>Lời đọc & phụ đề theo thời gian</CardTitle>
      <CardDescription>Dùng một giọng thuyết minh hoặc nhiều giọng cho nhân vật. Mỗi câu đọc theo mốc thời gian trong video; có thể nhập kịch bản hoặc file SRT để bắt đầu.</CardDescription>
    </CardHeader>
    <CardContent className="space-y-4">
      {jobMonitor.checking && <div role="status" className="rounded-md p-3 text-xs" style={speechStyle}>
        {jobMonitor.pollError ? 'Chưa kết nối lại được với máy chủ. Đang tự kiểm tra lại trạng thái tạo lời đọc…' : 'Đang kiểm tra trạng thái tạo lời đọc trên máy chủ…'}
        <p>Tạm khóa thao tác tạo và ghép cho đến khi xác nhận được tác vụ hiện tại.</p>
        {jobMonitor.pollError && <p>{jobMonitor.pollError}</p>}
      </div>}
      {jobMonitor.submissionError && !jobMonitor.checking && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>
        Không xác nhận được tác vụ tạo lời đọc mới. Máy chủ hiện không có tác vụ lời đọc đang chạy; bạn có thể thử lại. {jobMonitor.submissionError}
      </p>}
      {jobMonitor.recovered && <p role="status" className="text-xs">Đã kết nối lại và khôi phục trạng thái tác vụ tạo lời đọc.</p>}
      {suggestedNarratorVoiceId && <section aria-label="Giọng thuyết minh đã chọn" className="space-y-2 rounded-md p-4 text-xs" style={speechStyle}>
        <strong>Giọng thuyết minh đã chọn: {suggestedNarrator?.name ?? 'Đang tải giọng…'}</strong>
        <p>Mỗi câu giữ nội dung và mốc phụ đề. Bạn có thể dùng cùng một giọng cho toàn bộ lời đọc.</p>
        {doc && <p>{doc.cues.length} câu · {narratorApplied ? 'Tất cả câu đang dùng giọng này.' : 'Áp dụng giọng đã chọn vào bản nháp trước khi tạo lời đọc.'}</p>}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy || !doc?.cues.length || !suggestedNarrator || narratorApplied} onClick={() => {
            if (!doc || !suggestedNarrator) return
            setVoiceLanguage(suggestedNarrator.languages?.[0] ?? 'vi')
            applyRepair(assignNarratorVoice(doc, suggestedNarrator.id))
            setMessage(`Đã chọn ${suggestedNarrator.name} cho ${doc.cues.length} câu. Có thể hoàn tác hoặc tạo lời đọc để lưu và bắt đầu.`)
          }}>Dùng giọng này cho tất cả câu</Button>
          <Button size="sm" disabled={busy || stale || !narratorApplied || !suggestedNarrator} onClick={() => void generate()}>Tạo lời đọc · {doc?.cues.length ?? 0} câu</Button>
          <Button size="sm" variant="ghost" onClick={onDismissNarratorVoice}>Đóng gợi ý</Button>
        </div>
        {!doc?.cues.length && <p>Thêm câu hoặc đưa phụ đề vào trình sửa bên dưới trước khi tạo lời đọc.</p>}
      </section>}
      <details className="pp-disclosure" open={Boolean(doc && !doc.cues.length)}><summary>Chuẩn bị kịch bản<small>Nhận dạng, dịch, nhập SRT hoặc phân nhóm người nói.</small></summary><div className="pp-disclosure-body">
      <details className="pp-disclosure" open={Boolean(doc && !doc.cues.length)}><summary>Tạo phụ đề & dịch video<small>Nhận dạng hoặc dịch trước khi chọn giọng.</small></summary><div className="pp-disclosure-body">
      <MediaWorkbench videoId={video.id} document={doc} timeline={state?.timeline} dirty={dirty}
        selectedIds={selectedIds}
        busy={pending || running || jobMonitor.checking || analysisBusy} onBusy={setMediaBusy} onSeek={onSeek}
        onImported={() => { void onVideoUpdated() }} onDraft={candidate => {
          applyRepair(candidate); setMode('srt'); setPage(0)
          setMessage('Đã đưa bản nháp vào trình sửa. Kiểm tra rồi bấm tạo câu còn thiếu / đã sửa để lưu và tạo giọng; có thể hoàn tác.')
        }} />
      </div></details>
      <details className="pp-disclosure"><summary>Phân nhóm người nói & kiểm tra chất lượng</summary><div className="pp-disclosure-body">
      <SpeechAnalysisPanel videoId={video.id} document={doc} dirty={dirty} stale={stale}
        busy={pending || running || jobMonitor.checking || mediaBusy} audio={state?.audio ?? []} voices={allVoices}
        refreshKey={JSON.stringify([state?.audio.map(a => a.audio_url), false])}
        onBusy={setAnalysisBusy} onSeek={onSeek} onPlay={(url, offset, end) => { void play(url, offset, end) }}
        onDraft={candidate => { applyRepair(candidate); setPage(0); setMessage('Đã đưa người nói và giọng vào bản nháp. Kiểm tra rồi lưu kịch bản; có thể hoàn tác.') }} />
      </div></details>
      <details className="pp-disclosure" open={Boolean(doc && !doc.cues.length)}>
        <summary><FileText className="mr-2 inline size-4" />Nhập kịch bản hoặc file SRT<small>Dán nội dung từ GPT, Gemini hoặc mở file phụ đề của bạn.</small></summary>
        <div className="pp-disclosure-body">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label>Nhập nội dung <select aria-label="Loại kịch bản" value={mode} disabled={busy} onChange={e => setMode(e.target.value as 'dialogue' | 'srt')} className="rounded p-2" style={speechStyle}><option value="dialogue">Hội thoại theo nhân vật</option><option value="srt">Phụ đề SRT có thời gian</option></select></label>
        <label className="cursor-pointer underline">Mở file SRT<input className="sr-only" type="file" accept=".srt,text/plain,application/x-subrip" disabled={busy} onChange={async e => {
          const file = e.target.files?.[0]
          if (!file) return
          if (file.size > 1000000) { setError('File SRT tối đa 1 MB.'); return }
          try { setDraft(await file.text()); setMode('srt') } catch (cause) { setError(String(cause)) }
          e.target.value = ''
        }} /></label>
      </div>
      <textarea aria-label="Kịch bản hội thoại hoặc SRT" rows={5} value={draft} onChange={e => setDraft(e.target.value)} disabled={busy} className="w-full rounded p-3 text-sm" style={speechStyle} placeholder={mode === 'dialogue' ? 'Cảnh 1\nPHÚC: Xin chào, hôm nay em thế nào?\nLINH: Em rất vui khi được gặp anh.\n\nCảnh 2\nPHÚC: Mình đi dạo nhé.' : '1\n00:00:01,000 --> 00:00:04,000\nPHÚC: Xin chào bạn.\n\n2\n00:00:05,000 --> 00:00:08,000\nLINH: Hẹn gặp lại!'} />
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" disabled={busy || !draft.trim() || !doc} onClick={() => void action(async () => {
          const parsed = await fetchAPI<SpeechDocument>(`${base}/parse`, { method: 'POST', body: JSON.stringify({ mode, text: draft }) })
          setDoc(parsed); setDirty(true); setMessage('Đã phân loại. Kiểm tra giọng và mốc thời gian rồi lưu kịch bản.')
        })}>Phân loại thành câu thoại</Button>
        <span className="text-xs" style={{ color: 'var(--muted)' }}>Hội thoại dùng dạng TÊN: lời nói. Thời gian gợi ý có thể sửa. SRT giữ nguyên các mốc bạn nhập.</span>
      </div>
        </div>
      </details>
      </div></details>
      {error && <p role="alert" className="whitespace-pre-wrap text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
      {message && <p role="status" className="text-xs">{message}</p>}
      {!doc && !error && <p>Đang tải kịch bản đã lưu…</p>}
      {doc && <>
        <div className="pp-section-label"><span>1</span>Giọng của nhân vật</div>
        <label className="block text-xs">Ngôn ngữ lời đọc<select aria-label="Ngôn ngữ lời đọc" className="ml-2 rounded p-2" style={speechStyle} value={voiceLanguage} disabled={busy} onChange={e => { setVoiceLanguage(e.target.value); try { localStorage.setItem(`flowkit:speech-language:${video.id}`, e.target.value) } catch { /* optional */ } }}>{Object.entries(SPEECH_LANGUAGES).map(([code, name]) => <option key={code} value={code}>{name}</option>)}</select></label>
        {!languageVoices.length && <p role="alert" className="text-xs">Chưa có giọng hỗ trợ ngôn ngữ này. Thử tải lại thư viện.</p>}
        {Boolean(speakers.length) && <div className="pp-speakers">{speakers.map(speaker => {
          const speakerCues = doc.cues.filter(c => c.speaker === speaker)
          const chosen = new Set(speakerCues.map(c => c.voice_id))
          const voiceId = chosen.size === 1 ? speakerCues[0].voice_id : ''
          const voice = languageVoices.find(v => v.id === voiceId)
          const linked = state?.characters.find(c => normalizeName(c.name) === normalizeName(speaker))
          return <div key={speaker} className="pp-speaker">
            <div className="pp-speaker-heading"><span className="pp-avatar">{speaker.slice(0, 1).toUpperCase()}</span><div><strong>{speaker}</strong><small>{speakerCues.length} câu · {linked ? 'Nhân vật trong dự án' : 'Người nói trong kịch bản'}</small></div></div>
            <details><summary className="cursor-pointer text-xs">Đổi tên / gộp người nói</summary><form className="pp-speaker-rename" onSubmit={event => {
              event.preventDefault()
              const name = String(new FormData(event.currentTarget).get('name') ?? '')
              try { applyRepair(renameSpeaker(doc, speaker, name)); setMessage('Đã đổi tên / gộp người nói trong bản nháp. Kiểm tra giọng từng câu rồi lưu.') }
              catch (cause) { setError(speechError(cause)) }
            }}><input name="name" aria-label={`Đổi tên ${speaker}`} defaultValue={speaker} maxLength={80} required disabled={busy} /><Button size="sm" variant="outline" disabled={busy}>Đổi tên / gộp</Button></form></details>
            <select aria-label={`Giọng của ${speaker}`} value={languageVoices.some(v => v.id === voiceId) ? voiceId : ''} disabled={busy || !languageVoices.length} onChange={e => assign(speaker, e.target.value)} className="w-full rounded p-2" style={speechStyle}>{voiceOptions(true)}</select>
            {chosen.size > 1 && <p>Đang có giọng khác nhau theo câu.</p>}
            {voice?.audio_url && <Button size="sm" variant="outline" onClick={() => void play(voice.audio_url!)}>Nghe giọng {voice.name}</Button>}
          </div>
        })}</div>}
        {voiceLanguage === 'en' && <div className="space-y-2">
          {speakers.length > 1 && <label className="block text-xs">Chọn giọng demo cho<select aria-label="Người nói nhận giọng demo" value={demoTarget} disabled={busy} onChange={e => setDemoSpeaker(e.target.value)} className="ml-2 rounded p-2" style={speechStyle}>{speakers.map(speaker => <option key={speaker}>{speaker}</option>)}</select></label>}
          <PresetVoiceLibrary engine="kokoro" selected={demoVoice.startsWith('kokoro_') ? demoVoice : ''}
            disabled={busy || !speakers.length} onVoicesChange={refreshNativeVoices}
            onSelect={id => assign(demoTarget, id)}
            onPlay={next => { if (audio.current !== next) audio.current?.pause(); audio.current = next; onPreviewPause() }} />
        </div>}
        {voiceLanguage !== 'vi' && <div className="space-y-2">
          {speakers.length > 1 && <label className="block text-xs">Chọn giọng Qwen3 cho<select aria-label="Người nói nhận giọng Qwen3" value={demoTarget} disabled={busy} onChange={e => setDemoSpeaker(e.target.value)} className="ml-2 rounded p-2" style={speechStyle}>{speakers.map(speaker => <option key={speaker}>{speaker}</option>)}</select></label>}
          <PresetVoiceLibrary key={voiceLanguage} engine="qwen3" language={voiceLanguage} selected={demoVoice.startsWith('qwen3_') ? demoVoice : ''}
            disabled={busy || !speakers.length} onVoicesChange={refreshNativeVoices}
            onSelect={id => assign(demoTarget, id)}
            onPlay={next => { if (audio.current !== next) audio.current?.pause(); audio.current = next; onPreviewPause() }} />
        </div>}
        <details className="pp-disclosure"><summary>Thư viện giọng riêng</summary><div className="pp-disclosure-body"><CustomVoiceLibrary voices={custom} onChanged={setCustom} onPlay={url => void play(url)} /></div></details>
        <section aria-label="Tự xử lý lồng tiếng" className="space-y-3 rounded p-4 text-xs" style={speechStyle}>
          <div className="pp-section-label"><span>2</span>Tự xử lý lồng tiếng</div>
          <strong>Giọng vừa khung {readiness.ready.length}/{doc.cues.length}</strong>
          <p>{readiness.missing.length} câu thiếu giọng · {readiness.long.length} câu giọng quá dài. Tự lưu, dùng thời gian trống giữa các câu, tăng tốc nhẹ và AI rút gọn nếu vẫn cần; tối đa hai vòng sửa.</p>
          <div className="flex flex-wrap gap-2">
            <Button disabled={busy || stale || !doc.cues.length || doc.cues.some(c => !languageVoices.some(v => v.id === c.voice_id)) || readiness.ready.length === doc.cues.length}
              onClick={() => void generate(undefined, false, undefined, true)}>{state?.automatic && ['CANCELLED', 'FAILED'].includes(job?.status ?? '') ? 'Tiếp tục tự xử lý' : 'Tự xử lý lồng tiếng'}</Button>
            {state?.automatic && !dirty && <Button variant="outline" disabled={busy || stale} onClick={() => void action(async () => {
              await save(false, { ...state.automatic!.original, revision: doc.revision })
              setMessage('Đã khôi phục lời và mốc trước khi tự xử lý. Bản dựng timeline dùng hoàn tác riêng.')
            })}>Hoàn tác tự xử lý</Button>}
          </div>
          {languageVoices.length > 0 && doc.cues.some(c => !languageVoices.some(v => v.id === c.voice_id)) && <p>Chọn giọng hợp lệ cho từng người nói trước khi bắt đầu.</p>}
          {stale && <p role="alert">Mốc video đã đổi. Mở Chỉnh nâng cao để kiểm tra và xác nhận mốc mới.</p>}
          {job?.mode === 'automatic' && !running && <p>{readiness.ready.length === doc.cues.length ? 'Đã sẵn sàng. Nghe thử rồi áp dụng vào timeline.' : 'Phần hoàn tất đã lưu. Kiểm tra các câu còn thiếu / quá dài bên dưới; không cắt mất lời đọc.'}</p>}
          {Boolean(reviewCues.length) && <details open={Boolean(state?.automatic && !running && readiness.ready.length !== doc.cues.length)}><summary className="cursor-pointer">{reviewChanges.length ? `Xem ${reviewChanges.length} câu đã tự căn hoặc cần sửa` : 'Nghe thử lời đọc'}</summary>
            <div className="pp-cue-list">{reviewCues.slice(currentReviewPage * 10, currentReviewPage * 10 + 10).map(cue => {
              const before = originalCues.get(cue.id)
              const info = matchingAudio(cue, state)
              return <div key={cue.id} className="rounded p-3" style={speechStyle}>
                <button type="button" className="underline" onClick={() => onSeek(cue.start)}>{cue.start.toFixed(2)}–{cue.end.toFixed(2)}s · {cue.speaker}</button>
                {before && before.text !== cue.text && <p className="opacity-60">Trước: {before.text}</p>}
                {before && (before.start !== cue.start || before.end !== cue.end) && <p className="opacity-60">Mốc trước: {before.start.toFixed(2)}–{before.end.toFixed(2)}s</p>}
                <p>{cue.text}</p><small>{info ? `Giọng ${info.duration!.toFixed(2)}s / khung ${(cue.end - cue.start).toFixed(2)}s · ${longIds.has(cue.id) ? 'Còn quá dài' : 'Vừa khung'} · ${cue.speed}×` : 'Cần tạo giọng'}</small>
                {job?.results[cue.id]?.error && <p role="alert">{job.results[cue.id].error}</p>}
                {info?.audio_url && <Button size="sm" variant="outline" disabled={running} onClick={() => void play(info.audio_url!)}>Nghe thử câu</Button>}
              </div>
            })}</div>
            {reviewPages > 1 && <div className="flex gap-2"><Button size="sm" variant="outline" disabled={!currentReviewPage} onClick={() => setReviewPage(currentReviewPage - 1)}>Trước</Button><span>{currentReviewPage + 1}/{reviewPages}</span><Button size="sm" variant="outline" disabled={currentReviewPage + 1 >= reviewPages} onClick={() => setReviewPage(currentReviewPage + 1)}>Sau</Button></div>}
          </details>}
        </section>
        {job && (running || jobMonitor.checking || job.mode === 'automatic' || !dirty) && <div role="status" className="space-y-1 rounded p-3 text-xs" style={speechStyle}>
          <p>{job.status === 'FAILED' ? job.mode === 'automatic' ? `Tự xử lý chưa hoàn tất · ${stages[job.stage] ?? job.stage}` : 'Tạo lời đọc bị lỗi' : stages[job.stage] ?? job.stage}{job.mode !== 'automatic' && ` · ${job.completed}/${job.total} câu`} · {job.mode === 'automatic' && 'Toàn quy trình '}{percent(job)}%{running && job.current && ` · Câu ${doc.cues.findIndex(c => c.id === job.current) + 1}`}</p>
          <progress max={100} value={percent(job)} className="w-full" />
          <p>{job.mode === 'automatic' ? 'Tiến trình gồm tạo giọng, căn thời gian và AI rút gọn. Phần trăm tính cho toàn quy trình; số câu chỉ tính lượt tạo giọng hiện tại. Khi đang tạo một câu hoặc chờ AI, phần trăm có thể giữ nguyên.' : 'Phần trăm tính theo số câu đã xử lý. Model đang đọc một câu thì tiến trình có thể giữ nguyên.'}</p>
          {running && <Button size="sm" variant="outline" disabled={pending || job.cancel_requested} onClick={() => void action(async () => {
            const cancelled = await fetchAPI<SpeechJob>(`${base}/jobs/cancel`, { method: 'POST' })
            updateJobMonitor({ type: 'checked', job: cancelled })
            setMessage('Sẽ dừng sau câu đang tạo. Các câu hoàn tất được giữ; tạo câu còn thiếu để tiếp tục.')
          })}>{job.cancel_requested ? 'Đang dừng sau phần hiện tại…' : 'Dừng xử lý'}</Button>}
          {job.error && <p role="alert" style={{ color: 'var(--red)' }}>{job.error}</p>}
        </div>}
        {onApplyTimeline && <div className="pp-mix-box"><div className="pp-section-label"><span>3</span>Áp dụng vào bản dựng</div>
          <p>{readiness.ready.length === doc.cues.length && doc.cues.length ? 'Giọng đã vừa khung. Cảnh báo phụ đề đọc nhanh chỉ là gợi ý, không ngăn áp dụng.' : `Chưa thể áp dụng: ${readiness.missing.length} câu thiếu giọng, ${readiness.long.length} câu giọng quá dài.`}</p>
          <Button disabled={busy || stale || !doc.cues.length || readiness.ready.length !== doc.cues.length} onClick={() => void applyTimeline()}>Áp dụng vào timeline</Button></div>}
        <details className="pp-disclosure"><summary>Chỉnh nâng cao<small>Sửa từng câu, chọn nhóm, rút gọn thủ công hoặc căn mốc riêng.</small></summary><div className="pp-disclosure-body">
        <div className="pp-section-label">Biên tập lời đọc</div>
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
          <span>{doc.cues.length} câu · Video {state?.duration.toFixed(2)} giây · {dirty ? 'Có thay đổi chưa lưu' : `Đã lưu phiên bản ${doc.revision}`}</span>
          {playing && <Button size="sm" variant="outline" onClick={() => { audio.current?.pause(); setPlaying(false) }}>Dừng nghe</Button>}
        </div>
        <div className="pp-repair-toolbar">
          <Button size="sm" variant="outline" disabled={busy || stale || !state || !overlongCount} onClick={() => setRepair({ mode: 'fit' })}>Tự căn thời lượng · {overlongCount} câu dài</Button>
          {dirty && undoRepair?.after === doc && <Button size="sm" variant="outline" disabled={busy} onClick={() => {
            setDoc(undoRepair.before); setDirty(undoRepair.wasDirty); setUndoRepair(null); setMessage('Đã hoàn tác thao tác vừa áp dụng. Nội dung trước đó được giữ.')
          }}>Hoàn tác lần sửa</Button>}
          <span>Đổi mốc sẽ giữ âm thanh. Sửa lời, tốc độ hoặc tách câu cần tạo lại giọng.</span>
        </div>
        <section aria-label="Xử lý lời đọc hàng loạt" className="space-y-2 rounded p-3 text-xs" style={speechStyle}>
          <strong>Đã chọn {selectedIds.length}/{doc.cues.length} câu · Giọng vừa khung {readiness.ready.length}/{doc.cues.length}</strong>
          <p>{readiness.missing.length} câu cần tạo / chọn giọng · {readiness.long.length} câu có giọng quá dài. Các nút dưới đây dành cho sửa nhóm câu thủ công.</p>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setSelected(new Set(doc.cues.map(c => c.id)))}>Chọn tất cả</Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setSelected(new Set(doc.cues.filter(c => longIds.has(c.id) || c.text.length > MAX_CAPTION_CHARS || c.text.length / Math.max(.1, c.end - c.start) > MAX_CHARS_PER_SECOND).map(c => c.id)))}>Chọn câu dài / đọc nhanh</Button>
            <Button size="sm" variant="outline" disabled={busy} onClick={() => setSelected(new Set(readiness.missing))}>Chọn câu thiếu giọng</Button>
            <Button size="sm" variant="ghost" disabled={busy} onClick={() => setSelected(new Set())}>Bỏ chọn</Button>
            <Button size="sm" disabled={busy || stale || !selectedIds.length || doc.cues.some(c => selected.has(c.id) && !c.voice_id)} onClick={() => void generate(selectedIds)}>Tạo giọng câu đã chọn</Button>
            <Button size="sm" variant="outline" disabled={busy || stale || !state || !selectedIds.some(id => longIds.has(id))} onClick={() => {
              if (!state) return
              try {
                const fitted = fitSelectedSpeech(doc, state, new Set(selectedIds))
                applyRepair(fitted.document)
                setMessage(`Đã căn vào khoảng trống; ${fitted.regenerate.length} câu đổi tốc độ cần tạo lại. ${fitted.unresolved.length} câu vẫn quá dài, chọn AI rút gọn. Không dời các câu khác; tốc độ tối đa 1,25×.`)
              } catch (cause) { setError(speechError(cause)) }
            }}>Căn nhóm đã chọn · tối đa 1,25×</Button>
          </div>
          <p>AI rút gọn nằm trong “Tạo phụ đề & dịch video” ở trên. Giữ câu đã chọn khi đổi trang; bản giọng hợp lệ được dùng lại.</p>
        </section>
        {stale && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>Cảnh đã thay đổi. Kiểm tra lại mọi mốc thời gian, rồi bấm “Xác nhận cảnh mới & lưu”.</p>}
        {doc.cues.length > 30 && <div className="pp-candidate-toolbar"><Button size="sm" variant="outline" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>30 câu trước</Button><span>Trang {currentPage + 1}/{Math.ceil(doc.cues.length / 30)}</span><Button size="sm" variant="outline" disabled={(currentPage + 1) * 30 >= doc.cues.length} onClick={() => setPage(currentPage + 1)}>30 câu sau</Button></div>}
        <div className="pp-cue-list">{doc.cues.slice(currentPage * 30, currentPage * 30 + 30).map((cue, localIndex) => {
          const i = currentPage * 30 + localIndex
          const info = audios.get(cue.id)
          const validAudio = matchingAudio(cue, state)
          const tooLong = validAudio?.duration != null && validAudio.duration > cue.end - cue.start + .04
          const captionWarning = cue.text.length / Math.max(.1, cue.end - cue.start) > MAX_CHARS_PER_SECOND || cue.text.length > MAX_CAPTION_CHARS
          const failed = job?.results[cue.id]?.error
          return <div key={cue.id} className="pp-cue" data-current={currentCue?.id === cue.id}>
            <div className="pp-cue-heading">
              <input type="checkbox" aria-label={`Chọn câu ${i + 1}`} checked={selected.has(cue.id)} disabled={busy} onChange={e => {
                const checked = e.target.checked
                setSelected(current => { const next = new Set(current); if (checked) next.add(cue.id); else next.delete(cue.id); return next })
              }} />
              <strong>Câu {String(i + 1).padStart(2, '0')} · {cue.speaker}</strong>
              {validAudio && !tooLong && <span className="pp-cue-status"><Check size={11} />Đã tạo · {validAudio.duration?.toFixed(2)}s</span>}
              <Button size="sm" variant="ghost" disabled={busy} onClick={() => { setDoc({ ...doc, cues: doc.cues.filter(c => c.id !== cue.id) }); setDirty(true) }}>Bỏ câu</Button>
            </div>
            <textarea aria-label={`Nội dung câu ${i + 1}`} value={cue.text} maxLength={1500} disabled={busy} rows={2} onChange={e => edit(cue.id, { text: e.target.value })} className="w-full rounded p-2 text-sm" style={speechStyle} />
            <div className="pp-cue-timing">
              <label>Từ <input aria-label={`Bắt đầu câu ${i + 1}`} type="number" min={0} step={0.01} value={cue.start} disabled={busy} onChange={e => edit(cue.id, { start: Number(e.target.value) })} className="w-20" />s</label>
              <label>Đến <input aria-label={`Kết thúc câu ${i + 1}`} type="number" min={0} step={0.01} value={cue.end} disabled={busy} onChange={e => edit(cue.id, { end: Number(e.target.value) })} className="w-20" />s</label>
              <label>Tốc độ <input aria-label={`Tốc độ câu ${i + 1}`} type="number" min={0.5} max={2} step={0.05} value={cue.speed} disabled={busy} onChange={e => edit(cue.id, { speed: Number(e.target.value) })} className="w-20" />×</label>
            </div>
            <details className="mb-3 text-xs"><summary className="cursor-pointer py-1" style={{ color: 'var(--pp-muted)' }}>Người nói & giọng riêng của câu</summary>
              <div className="mt-2 flex flex-wrap gap-2">
                <input aria-label={`Người nói câu ${i + 1}`} value={cue.speaker} maxLength={80} disabled={busy} onChange={e => edit(cue.id, { speaker: e.target.value })} className="w-32" />
                <select aria-label={`Giọng câu ${i + 1}`} value={cue.voice_id} disabled={busy} onChange={e => edit(cue.id, { voice_id: e.target.value })} className="max-w-64 rounded p-1.5" style={speechStyle}>{voiceOptions()}</select>
              </div>
            </details>
            <div className="pp-cue-actions">
              {captionWarning && <span className="pp-workbench-notice">Phụ đề dài hoặc đọc nhanh · {cue.text.length} ký tự · {Math.round(cue.text.length / Math.max(.1, cue.end - cue.start))} ký tự/giây.</span>}
              <Button size="sm" variant="outline" disabled={busy || !cue.voice_id || stale} onClick={() => void generate([cue.id], true)}>Tạo lại câu {i + 1}</Button>
              {validAudio?.audio_url && <Button size="sm" variant="outline" onClick={() => void play(validAudio.audio_url!)}>Nghe câu · {validAudio.duration?.toFixed(2)}s</Button>}
              <Button size="sm" variant="ghost" disabled={!video.vertical_url && !video.horizontal_url} onClick={() => onSeek(cue.start)}>Tới mốc này</Button>
              {!cue.voice_id && <span style={{ color: 'var(--red)' }}>Chưa chọn giọng</span>}
              {tooLong && <span role="alert" style={{ color: 'var(--red)' }}>Lời đọc dài {validAudio?.duration?.toFixed(2)}s, khung chỉ {(cue.end - cue.start).toFixed(2)}s. Cần thêm {((validAudio?.duration ?? 0) - (cue.end - cue.start)).toFixed(2)}s.</span>}
              {!dirty && info?.error && <span role="alert">{info.error}</span>}
              {failed && <span role="alert" style={{ color: 'var(--red)' }}>{failed}</span>}
            </div>
            <div className="pp-repair-toolbar">
              {tooLong && <Button size="sm" variant="outline" disabled={busy || stale} onClick={() => setRepair({ mode: 'fit', id: cue.id })}>Căn thời lượng câu này</Button>}
              <Button size="sm" variant="outline" disabled={busy || stale} onClick={() => setRepair({ mode: 'shorten', id: cue.id })}>Rút gọn lời</Button>
              <Button size="sm" variant="outline" disabled={busy || stale || doc.cues.length >= MAX_SPEECH_CUES} onClick={() => setRepair({ mode: 'split', id: cue.id })}>Tách câu</Button>
            </div>
          </div>
        })}</div>
        <div className="pp-action-bar">
          <Button size="sm" variant="outline" disabled={busy || doc.cues.length >= MAX_SPEECH_CUES} onClick={() => {
            const start = doc.cues.at(-1)?.end ?? 0
            setDoc({ ...doc, cues: [...doc.cues, { id: crypto.randomUUID(), start, end: start + 3, speaker: speakers[0] ?? 'Người kể', text: '', voice_id: '', speed: 1 }] }); setDirty(true)
            setPage(Math.floor(doc.cues.length / 30))
          }}><Plus size={14} />Thêm câu</Button>
          <Button size="sm" variant="outline" disabled={busy || doc.cues.length < 2} onClick={() => { setDoc({ ...doc, cues: [...doc.cues].sort((a, b) => a.start - b.start) }); setDirty(true) }}>Sắp xếp theo mốc</Button>
          <Button size="sm" disabled={busy} onClick={() => void action(async () => { await save(stale); setMessage('Đã lưu kịch bản và giọng. Có thể mở lại hoặc dùng giọng cho video khác trong dự án.') })}><Save size={14} />{stale ? 'Xác nhận cảnh mới & lưu' : 'Lưu kịch bản & giọng'}</Button>
          {dirty && <Button size="sm" variant="ghost" disabled={busy} onClick={() => { if (state) { setDoc(state.document); setDirty(false); setMessage('Đã trở về kịch bản đã lưu.') } }}>Bỏ bản nháp</Button>}
          <Button size="sm" disabled={busy || !doc.cues.length || stale || doc.cues.some(c => !c.voice_id)} onClick={() => void generate()}>Tạo tất cả câu</Button>
          <Button size="sm" variant="outline" disabled={busy || stale || !doc.cues.some(c => c.voice_id && missingIds.has(c.id))} onClick={() => void generate(doc.cues.filter(c => c.voice_id && missingIds.has(c.id)).map(c => c.id))}>Tạo câu còn thiếu / đã sửa</Button>
          {!dirty && doc.cues.length > 0 && <a className="self-center text-xs underline" href={resolveMediaUrl(`${base}/subtitles.srt`)}>Tải phụ đề .SRT</a>}
        </div>
        </div></details>
      </>}
      {repair && doc && state && <SpeechRepairDialog key={`${repair.mode}:${repair.id ?? 'all'}`} selection={repair} document={doc} state={state}
        busy={busy || stale} onClose={() => setRepair(null)} onApply={applyRepair} />}
    </CardContent>
  </Card>
}
