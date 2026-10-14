import { useEffect, useRef, useState } from 'react'
import { Check, Pause, Play, RefreshCw, Search } from 'lucide-react'
import { fetchAPI, resolveMediaUrl } from '../../../api/client'
import { usePolling } from '../../../api/usePolling'
import { useTranslation } from '../../../i18n/useTranslation'
import { Button } from '../../ui/button'
import { Progress } from '../../ui/progress'
import type { SpeechVoice } from './speechTypes'

interface Voice extends SpeechVoice {
  id: string; name: string; gender: string; region: string; description: string
  audio_url: string | null; duration: number | null; sample_text: string
  warning?: string | null
}
interface Status {
  available: boolean; reason: string | null; model_downloaded: boolean; model: string; state: string; device?: string; sample_rate: number
}
interface PreviewJob {
  status: string; completed: number; processed?: number; total: number; stage: string
  current_voice: string | null; errors: string[]
}
interface Props {
  selected: string; disabled: boolean
  onSelect: (id: string) => void
  onReadyChange?: (ready: boolean) => void
  engine?: 'vieneu' | 'kokoro' | 'qwen3'
  language?: string
  onVoicesChange?: (voices: SpeechVoice[]) => void
  onPlay: (audio: HTMLAudioElement) => void
}

const controlStyle = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }
const normalize = (text: string) => text.toLocaleLowerCase().normalize('NFD').replace(/\p{Diacritic}/gu, '').replace(/đ/g, 'd')

export function PresetVoiceLibrary({ selected, disabled, onSelect, onReadyChange, onPlay, onVoicesChange, engine = 'vieneu', language = 'en' }: Props) {
  const { t } = useTranslation()
  const base = `/api/tts/${engine}`
  const languageQuery = engine === 'qwen3' ? `?language=${encodeURIComponent(language)}` : ''
  const title = engine === 'qwen3' ? 'Giọng đa ngôn ngữ · Qwen3' : engine === 'kokoro' ? 'Giọng tiếng Anh · Kokoro' : t('vieneu.library')
  const [voices, setVoices] = useState<Voice[]>([])
  const [status, setStatus] = useState<Status | null>(null)
  const [job, setJob] = useState<PreviewJob | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const [query, setQuery] = useState('')
  const [region, setRegion] = useState('')
  const [gender, setGender] = useState('')
  const previewAudio = useRef<HTMLAudioElement | null>(null)
  const playbackRequest = useRef(0)
  const [previewId, setPreviewId] = useState<string | null>(null)
  const [playing, setPlaying] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const running = Boolean(job && ['QUEUED', 'PROCESSING'].includes(job.status))
  const readyCount = voices.filter(voice => voice.audio_url).length

  useEffect(() => () => { previewAudio.current?.pause() }, [])

  async function play(voice: Voice) {
    if (!voice.audio_url) return
    const request = ++playbackRequest.current
    if (previewId === voice.id && playing) { previewAudio.current?.pause(); return }
    if (!previewAudio.current) {
      previewAudio.current = new Audio()
      previewAudio.current.onpause = () => setPlaying(false)
      previewAudio.current.onended = () => setPlaying(false)
    }
    const audio = previewAudio.current
    audio.pause()
    audio.src = resolveMediaUrl(voice.audio_url)!
    audio.onplay = () => { setPlaying(true); onPlay(audio) }
    setPreviewId(voice.id)
    setPreviewError('')
    try { await audio.play() }
    catch {
      if (request === playbackRequest.current) { setPlaying(false); setPreviewError(t('postProduction.demoAudioFailed', { name: voice.name })) }
    }
  }

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchAPI<Voice[]>(`${base}/voices${languageQuery}`, { signal: controller.signal }),
      fetchAPI<Status>(`${base}/status`, { signal: controller.signal }),
      fetchAPI<PreviewJob>(`${base}/previews/job${languageQuery}`, { signal: controller.signal }),
    ]).then(([library, ready, current]) => {
      if (controller.signal.aborted) return
      setVoices(library)
      onVoicesChange?.(library)
      setStatus(ready)
      setJob(current)
      onReadyChange?.(ready.available && (!selected || library.some(voice => voice.id === selected)))
      setError('')
    }).catch(cause => {
      if (!controller.signal.aborted) { setError(String(cause)); onReadyChange?.(false) }
    }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [revision, selected, onReadyChange, base, languageQuery, onVoicesChange])

  usePolling<PreviewJob>(running ? `${base}/previews/job${languageQuery}` : null, 1500, next => {
    if (next.completed !== job?.completed || next.status !== job?.status) setRevision(value => value + 1)
    setJob(next)
  }, cause => setError(String(cause)))

  async function prepare(presetId?: string) {
    setSubmitting(true)
    setError('')
    const query = new URLSearchParams(engine === 'qwen3' ? { language } : {})
    if (presetId) query.set('preset_id', presetId)
    try {
      setJob(await fetchAPI<PreviewJob>(`${base}/previews?${query}`, { method: 'POST' }))
      setRevision(value => value + 1)
    }
    catch (cause) { setError(String(cause)) }
    finally { setSubmitting(false) }
  }

  const visible = voices.filter(voice => (!region || voice.region === region) && (!gender || voice.gender === gender)
    && normalize(`${voice.name} ${voice.description}`).includes(normalize(query)))
  const chosen = voices.find(voice => voice.id === selected)
  const percent = job?.total ? Math.floor((job.processed ?? job.completed) / job.total * 100) : 0

  return <section className="flex flex-col gap-3 rounded-md p-3" aria-label={title} style={{ border: '1px solid var(--border)' }}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <span className="text-xs font-semibold">{title}</span>
      {!loading && <span className="text-[11px] tabular-nums" style={{ color: 'var(--muted)' }}>{t('vieneu.readyCount', { ready: readyCount, total: voices.length })}</span>}
    </div>
    <p className="m-0 text-[11px]" style={{ color: 'var(--muted)' }}>{engine === 'qwen3' ? 'Nghe 9 chất giọng theo ngôn ngữ đang chọn. Ưu tiên Ryan/Aiden cho tiếng Anh, Ono Anna cho tiếng Nhật, Sohee cho tiếng Hàn. Qwen3 chưa hỗ trợ tiếng Việt.' : engine === 'kokoro' ? 'Nghe thử giọng Anh Mỹ / Anh Anh, rồi chọn giọng cho người nói.' : t('vieneu.help')}</p>
    {status && <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>
      {engine === 'qwen3' ? 'Qwen3 0.6B' : engine === 'kokoro' ? 'Kokoro 82M' : 'VieNeu v3 Turbo'} · {status.device ?? 'CPU'} · {status.sample_rate / 1000} kHz · {status.model_downloaded ? t('vieneu.downloaded') : t('vieneu.firstDownload')}
    </p>}
    {status && !status.available && <div className="space-y-2">
      <p role="alert" className="m-0 text-xs" style={{ color: 'var(--yellow)' }}>{status.reason}</p>
      <Button type="button" size="sm" variant="outline" disabled={loading} onClick={() => setRevision(value => value + 1)}>{t('postProduction.retryDemoVoices')}</Button>
    </div>}
    {!loading && readyCount < voices.length && <Button type="button" size="sm" variant="outline" className="self-start" disabled={disabled || running || submitting || !status?.available} onClick={() => void prepare()}>
      <RefreshCw className={`size-3.5 ${running ? 'animate-spin' : ''}`} />{running || submitting ? t('vieneu.preparing') : t('vieneu.prepare')}
    </Button>}
    {running && <div role="status" aria-live="polite" className="flex flex-col gap-2 text-xs">
      <span>{job?.stage === 'checking_audio' ? `Đang đối chiếu lời đọc ${job.current_voice ?? ''}` : job?.stage === 'retrying' ? `Đang thử lại ${job.current_voice ?? ''} để tránh tiếng bất thường` : job?.stage === 'loading_model' ? t('vieneu.loadingModel') : job?.stage === 'queued' ? t('vieneu.queued') : t('vieneu.preparingVoice', { name: job?.current_voice ?? '' })} · {percent}%</span>
      <Progress value={percent} className="h-2" />
      <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{t('vieneu.progressHelp')}</span>
    </div>}
    {job?.errors.length ? <p role="alert" className="m-0 whitespace-pre-wrap text-xs" style={{ color: 'var(--red)' }}>{job.errors.join('\n')}</p> : null}
    <div className="flex flex-wrap gap-2">
      <label className="flex min-w-40 flex-1 items-center gap-2 rounded-md px-2" style={controlStyle}>
        <Search className="size-3.5 shrink-0" /><input aria-label={t('vieneu.search')} placeholder={t('vieneu.search')} value={query} onChange={event => setQuery(event.target.value)} className="w-full bg-transparent py-2 text-xs outline-none" />
      </label>
      <select aria-label={t('vieneu.region')} value={region} onChange={event => setRegion(event.target.value)} className="rounded-md p-2 text-xs" style={controlStyle}>
        <option value="">{t('vieneu.allRegions')}</option>{[...new Set(voices.map(voice => voice.region))].map(value => <option key={value} value={value}>{value}</option>)}
      </select>
      <select aria-label={t('vieneu.gender')} value={gender} onChange={event => setGender(event.target.value)} className="rounded-md p-2 text-xs" style={controlStyle}>
        <option value="">{t('vieneu.allGenders')}</option><option value="male">{t('vieneu.male')}</option><option value="female">{t('vieneu.female')}</option>
      </select>
    </div>
    {chosen && <p className="m-0 text-xs font-medium" style={{ color: 'var(--accent)' }}>{t('vieneu.selected', { name: chosen.name })}</p>}
    {previewError && <p role="alert" className="m-0 text-xs" style={{ color: 'var(--red)' }}>{previewError}</p>}
    {selected && !chosen && !loading && !error && <p role="alert" className="m-0 text-xs" style={{ color: 'var(--yellow)' }}>{t('vieneu.missingSelection')}</p>}
    {loading ? <p role="status" className="m-0 text-xs">{t('postProduction.loadingDemoVoices')}</p> : <div className="grid max-h-[480px] gap-2 overflow-y-auto pr-1 sm:grid-cols-2">
      {visible.map(voice => <article key={voice.id} className="flex min-w-0 flex-col gap-2 rounded-md p-2.5" style={{ border: `1px solid ${voice.id === selected ? 'var(--accent)' : 'var(--border)'}` }}>
        <div className="text-xs font-medium">{voice.name}</div>
        <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>{voice.description}</p>
        {voice.warning && <p role="alert" className="m-0 text-[11px]" style={{ color: 'var(--yellow)' }}>{voice.warning}</p>}
        {voice.audio_url ? <Button type="button" size="sm" variant="outline" aria-label={t('postProduction.previewVoice', { name: voice.name })} aria-pressed={previewId === voice.id && playing} onClick={() => void play(voice)}>
          {previewId === voice.id && playing ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
          {previewId === voice.id && playing ? t('vieneu.pause') : t('vieneu.listen')}{voice.duration ? ` · ${voice.duration.toFixed(1)}s` : ''}
        </Button>
          : <Button type="button" size="sm" variant="outline" aria-label={`Tạo demo giọng ${voice.name}`} disabled={disabled || running || submitting || !status?.available} onClick={() => void prepare(voice.id)}>
            <RefreshCw className={`size-3.5 ${running && job?.current_voice === voice.name ? 'animate-spin' : ''}`} />
            {running && job?.current_voice === voice.name ? t('vieneu.preparing') : 'Tạo demo'}
          </Button>}
        <Button type="button" size="sm" disabled={disabled || !status?.available} variant={voice.id === selected ? 'default' : 'outline'} aria-pressed={voice.id === selected} onClick={() => onSelect(voice.id)}>
          {voice.id === selected ? <><Check className="size-3.5" />{t('postProduction.voiceSelected')}</> : t('postProduction.selectVoice')}
        </Button>
      </article>)}
      {!visible.length && !error && <p className="text-xs">{t('vieneu.noMatches')}</p>}
    </div>}
    <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>{t('vieneu.savedDemos')}</p>
    {error && <div role="alert" className="text-xs" style={{ color: 'var(--red)' }}>
      <p className="break-words">{error}</p><Button type="button" variant="outline" size="sm" onClick={() => setRevision(value => value + 1)}>{t('postProduction.retryDemoVoices')}</Button>
    </div>}
  </section>
}
