import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { AudioLines, Download, KeyRound, Music2, Sparkles } from 'lucide-react'
import { fetchAPI, resolveMediaUrl } from '../../../api/client'
import { usePolling } from '../../../api/usePolling'
import type { Video } from '../../../types'
import { useTranslation } from '../../../i18n/useTranslation'
import { Button } from '../../ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../ui/card'

interface TemplateIndex {
  categories: { category: string; templates: { id: string; genre: string; subgenre: string; description: string }[] }[]
}
interface FullTemplate { suno_tags?: string; example_lyrics?: string }
interface MusicStatus { configured: boolean; model: string }
interface MusicTrack { filename: string; url: string; title?: string; duration?: number; clip_id?: string }
interface MusicTask {
  status?: string
  response?: { sunoData?: Record<string, unknown>[]; data?: Record<string, unknown>[]; clips?: Record<string, unknown>[] }
  sunoData?: Record<string, unknown>[]
  clips?: Record<string, unknown>[]
}
interface DownloadResult { downloaded: MusicTrack[] }
interface Props { projectId: string; video: Video | null; onAddTimeline?: (filename: string) => Promise<void> }

const controlStyle = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

function taskClips(task: MusicTask): { clip_id: string; title: string; url: string; duration?: number }[] {
  const rawItems = task.response?.sunoData ?? task.response?.data ?? task.response?.clips ?? task.sunoData ?? task.clips ?? []
  const items = Array.isArray(rawItems) ? rawItems : []
  return items.flatMap((clip, index) => {
    const url = String(clip.audioUrl ?? clip.audio_url ?? clip.streamAudioUrl ?? '')
    if (!url) return []
    return [{
      clip_id: String(clip.id ?? clip.audioId ?? index),
      title: String(clip.title ?? ('Track ' + (index + 1))),
      url,
      duration: typeof clip.duration === 'number' ? clip.duration : undefined,
    }]
  })
}

export function MusicPanel({ projectId, video, onAddTimeline }: Props) {
  const { t } = useTranslation()
  const [restored] = useState(() => { try { return JSON.parse(localStorage.getItem(`flowkit:suno-draft:${projectId}`) ?? 'null') as {taskId?: string; prompt?: string; style?: string; title?: string; templateId?: string; mode?: string; instrumental?: boolean; selectedFilename?: string} | null } catch { return null } })
  const [status, setStatus] = useState<MusicStatus | null>(null)
  const [apiKey, setApiKey] = useState('')
  const [savingKey, setSavingKey] = useState(false)
  const [keySaved, setKeySaved] = useState(false)
  const [keyError, setKeyError] = useState('')
  const [templateIndex, setTemplateIndex] = useState<TemplateIndex | null>(null)
  const [templateId, setTemplateId] = useState(restored?.templateId ?? '')
  const [mode, setMode] = useState<'custom' | 'description'>(restored?.mode === 'description' ? 'description' : 'custom')
  const [prompt, setPrompt] = useState(restored?.prompt ?? '')
  const [style, setStyle] = useState(restored?.style ?? '')
  const [title, setTitle] = useState(restored?.title ?? '')
  const [instrumental, setInstrumental] = useState(restored?.instrumental ?? true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [taskId, setTaskId] = useState(restored?.taskId ?? '')
  const [taskStatus, setTaskStatus] = useState(restored?.taskId ? 'PROCESSING' : '')
  const [generatedClips, setGeneratedClips] = useState<{ clip_id: string; title: string; url: string; duration?: number }[]>([])
  const [savedTracks, setSavedTracks] = useState<MusicTrack[]>([])
  const [selectedFilename, setSelectedFilename] = useState(restored?.selectedFilename ?? '')
  const selectedVideoId = video?.id ?? ''

  useEffect(() => {
    let active = true
    Promise.all([
      fetchAPI<MusicStatus>('/api/music/status'),
      fetchAPI<TemplateIndex>('/api/music/templates'),
    ]).then(([ready, templates]) => {
      if (!active) return
      setStatus(ready)
      setTemplateIndex(templates)
    }).catch(cause => active && setError(cause instanceof Error ? cause.message : String(cause)))
    return () => { active = false }
  }, [])

  useEffect(() => {
    try { localStorage.setItem(`flowkit:suno-draft:${projectId}`, JSON.stringify({ taskId, prompt, style, title, templateId, mode, instrumental, selectedFilename })) } catch { /* optional */ }
  }, [projectId, taskId, prompt, style, title, templateId, mode, instrumental, selectedFilename])

  const taskFailed = /FAILED|ERROR/.test(taskStatus.toUpperCase())
  const taskRunning = Boolean(taskId && taskStatus !== 'SUCCESS' && !taskFailed)
  usePolling<MusicTask>(
    taskRunning ? `/api/music/tasks/${encodeURIComponent(taskId)}` : null,
    6000,
    task => {
      const nextStatus = String(task.status ?? 'PROCESSING').toUpperCase()
      setTaskStatus(nextStatus)
      const clips = taskClips(task)
      if (clips.length) setGeneratedClips(clips)
      if (/FAILED|ERROR/.test(nextStatus)) setError(t('postProduction.musicTaskFailed'))
    },
    cause => setError(cause instanceof Error ? cause.message : String(cause)),
  )

  useEffect(() => {
    let active = true
    Promise.all([
      fetchAPI<MusicTrack[]>(`/api/music/files/${projectId}`),
    ]).then(([tracks]) => {
      if (!active) return
      setSavedTracks(tracks)
      setSelectedFilename(current => tracks.some(track => track.filename === current) ? current : tracks[0]?.filename ?? '')
    }).catch(cause => active && setError(cause instanceof Error ? cause.message : String(cause)))
    return () => { active = false }
  }, [selectedVideoId, projectId])

  const templateOptions = useMemo(() => templateIndex?.categories.flatMap(category =>
    category.templates.map(item => ({ ...item, category: category.category }))) ?? [], [templateIndex])
  const selectedTrack = savedTracks.find(track => track.filename === selectedFilename)
  async function selectTemplate(id: string) {
    setTemplateId(id)
    if (!id) return
    try {
      const template = await fetchAPI<FullTemplate>(`/api/music/templates/${id}`)
      setStyle(template.suno_tags ?? '')
      if (mode === 'custom') setPrompt(template.example_lyrics ?? '')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    }
  }

  async function saveApiKey(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const key = apiKey.trim()
    setKeyError('')
    setKeySaved(false)
    if (!/^[!-~]{1,4096}$/.test(key)) {
      setKeyError(t('postProduction.musicKeyInvalid'))
      return
    }
    setSavingKey(true)
    try {
      const ready = await fetchAPI<MusicStatus>('/api/music/settings', {
        method: 'PUT', body: JSON.stringify({ api_key: key }),
      })
      setStatus(ready)
      setApiKey('')
      setKeySaved(true)
    } catch {
      setKeyError(t('postProduction.musicKeySaveFailed'))
    } finally {
      setSavingKey(false)
    }
  }

  async function generate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!status?.configured || busy || taskRunning || prompt.trim().length < 3) return
    setBusy(true)
    setError('')
    setGeneratedClips([])
    setTaskId('')
    setTaskStatus('PENDING')
    try {
      const result = await fetchAPI<{ task_id: string }>('/api/music/generate', {
        method: 'POST',
        body: JSON.stringify({
          prompt: prompt.trim(), style: style.trim(), title: title.trim(), instrumental,
          custom_mode: mode === 'custom', template_id: templateId || null,
        }),
      })
      setTaskId(result.task_id)
      setTaskStatus('PROCESSING')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      setTaskStatus('FAILED')
    } finally {
      setBusy(false)
    }
  }

  async function saveGeneratedTracks() {
    if (!taskId) return
    setBusy(true)
    setError('')
    try {
      const response = await fetchAPI<DownloadResult>(`/api/music/tasks/${encodeURIComponent(taskId)}/download?project_id=${projectId}`, { method: 'POST' })
      const tracks = response.downloaded ?? []
      setSavedTracks(current => [...tracks.filter(item => !current.some(old => old.filename === item.filename)), ...current])
      if (tracks[0]) setSelectedFilename(tracks[0].filename)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }


  return (
    <Card className="py-4">
      <CardHeader>
        <div className="flex items-center gap-2"><Music2 className="size-4" style={{ color: 'var(--accent)' }} /><CardTitle className="text-sm">{t('postProduction.musicTitle')}</CardTitle></div>
        <CardDescription className="text-xs">Tạo nhạc, nghe thử rồi thêm vào lớp Nhạc nền của bản dựng.</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        {video && <div className="flex flex-col gap-4">
          <div className="text-xs font-medium">{t('postProduction.musicApply')}</div>
          <p className="text-xs">Tải nhạc có sẵn tại Thư viện của bàn dựng. Mục này dùng để tạo nhạc Suno và nghe kết quả.</p>
          {savedTracks.length === 0 ? <p className="m-0 text-xs" style={{ color: 'var(--muted)' }}>{t('postProduction.noSavedMusic')}</p> : savedTracks.map(track => (
            <label key={track.filename} className="flex flex-col gap-1 rounded-md p-2.5" style={{ border: '1px solid var(--border)' }}>
              <span className="flex items-center gap-2 text-xs"><input type="radio" name={'music-' + projectId} checked={selectedFilename === track.filename} onChange={() => setSelectedFilename(track.filename)} />{track.title || track.filename}</span>
              <audio controls preload="none" src={resolveMediaUrl(track.url)} className="w-full" />
              <a href={resolveMediaUrl(track.url)} download={track.filename} className="inline-flex items-center gap-1 self-start text-[10px]" style={{ color: 'var(--accent)' }}><Download className="size-3" />{t('postProduction.downloadMusic')}</a>
            </label>
          ))}
          {selectedTrack && onAddTimeline && <Button disabled={busy || taskRunning} onClick={async () => { setBusy(true);try { await onAddTimeline(selectedTrack.filename) } catch(cause) { setError(String(cause)) } finally { setBusy(false) } }}>Thêm nhạc đã chọn vào timeline</Button>}
        </div>}
        <details className="pp-disclosure" open>
          <summary>{t('postEditor.suno')}<small>{t('postEditor.sunoHint')}</small></summary>
          <div className="pp-disclosure-body">
        <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.sunoCreditNote')}</p>
        <details className="pp-disclosure">
          <summary>{t('postEditor.keySettings')}<small>{t(status?.configured ? 'postProduction.musicKeyConfigured' : 'postProduction.musicKeyRequired')}</small></summary>
        <form onSubmit={saveApiKey} className="flex flex-col gap-2 rounded-md p-3" style={{ border: '1px solid var(--border)' }}>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
            <span className="inline-flex items-center gap-1.5 font-medium"><KeyRound className="size-3.5" />{t('postProduction.musicKeyLabel')}</span>
            <span style={{ color: status?.configured ? 'var(--green)' : 'var(--muted)' }}>{t(status?.configured ? 'postProduction.musicKeyConfigured' : 'postProduction.musicKeyRequired')}</span>
          </div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <input type="password" aria-label={t('postProduction.musicKeyLabel')} value={apiKey}
              onChange={event => { setApiKey(event.target.value); setKeySaved(false); setKeyError('') }}
              autoComplete="off" autoCapitalize="none" spellCheck={false} maxLength={4096} disabled={savingKey}
              className="min-w-0 flex-1 rounded-md px-3 py-2 text-xs" style={controlStyle}
              placeholder={t(status?.configured ? 'postProduction.musicKeyReplace' : 'postProduction.musicKeyPlaceholder')} />
            <Button type="submit" size="sm" disabled={savingKey || !apiKey.trim()}>{t(savingKey ? 'postProduction.musicKeySaving' : 'postProduction.musicKeySave')}</Button>
          </div>
          <p className="m-0 text-[11px]" style={{ color: 'var(--muted)' }}>{t('postProduction.musicKeyHelp')} <a href="https://sunoapi.org/api-key" target="_blank" rel="noreferrer" className="underline" style={{ color: 'var(--accent)' }}>{t('postProduction.musicKeyGet')}</a></p>
          {keySaved && <p role="status" className="m-0 text-xs" style={{ color: 'var(--green)' }}>{t('postProduction.musicKeySaved')}</p>}
          {keyError && <p role="alert" className="m-0 text-xs" style={{ color: 'var(--red)' }}>{keyError}</p>}
        </form>
        </details>
        {status?.configured && <div className="text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.musicModel', { model: status.model })}</div>}
        <form onSubmit={generate} className="flex flex-col gap-3">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-xs">{t('postProduction.musicMode')}
              <select value={mode} onChange={event => setMode(event.target.value as 'custom' | 'description')} className="rounded-md px-2.5 py-2 text-xs" style={controlStyle}>
                <option value="custom">{t('postProduction.musicCustom')}</option>
                <option value="description">{t('postProduction.musicDescription')}</option>
              </select>
            </label>
            <label className="flex flex-col gap-1 text-xs">{t('postProduction.musicTemplate')}
              <select value={templateId} onChange={event => void selectTemplate(event.target.value)} className="rounded-md px-2.5 py-2 text-xs" style={controlStyle}>
                <option value="">{t('postProduction.noTemplate')}</option>
                {templateOptions.map(item => <option key={item.id} value={item.id}>{item.category} · {item.subgenre}</option>)}
              </select>
            </label>
          </div>
          <label className="flex flex-col gap-1 text-xs">{mode === 'custom' ? t('postProduction.musicLyrics') : t('postProduction.musicIdea')}
            <textarea value={prompt} onChange={event => setPrompt(event.target.value)} rows={4} maxLength={5000} className="rounded-md px-3 py-2 text-xs resize-y" style={controlStyle} placeholder={t('postProduction.musicPromptPlaceholder')} />
          </label>
          {mode === 'custom' && <label className="flex flex-col gap-1 text-xs">{t('postProduction.musicStyle')}
            <input value={style} onChange={event => setStyle(event.target.value)} maxLength={500} className="rounded-md px-3 py-2 text-xs" style={controlStyle} placeholder={t('postProduction.musicStylePlaceholder')} />
          </label>}
          <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <label className="flex flex-col gap-1 text-xs">{t('postProduction.musicTitleField')}
              <input value={title} onChange={event => setTitle(event.target.value)} maxLength={100} className="rounded-md px-3 py-2 text-xs" style={controlStyle} />
            </label>
            <label className="flex items-center gap-2 pb-2 text-xs"><input type="checkbox" checked={instrumental} onChange={event => setInstrumental(event.target.checked)} />{t('postProduction.instrumental')}</label>
            <Button type="submit" size="sm" disabled={!status?.configured || savingKey || busy || taskRunning || prompt.trim().length < 3}><Sparkles className="size-3.5" />{t('postProduction.generateMusic')}</Button>
          </div>
        </form>

        {(taskId || taskStatus) && <div className="flex flex-wrap items-center gap-2 rounded-md p-2.5 text-xs" style={{ background: 'var(--surface)' }}>
          <AudioLines className="size-3.5" style={{ color: 'var(--accent)' }} />
          <span>{t('postProduction.musicTaskStatus', { status: taskStatus || '—', id: taskId.slice(0, 8) })}</span>
          {taskStatus.toUpperCase() === 'SUCCESS' && <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => void saveGeneratedTracks()}><Download className="size-3.5" />{t('postProduction.saveMusic')}</Button>}
        </div>}
        {generatedClips.map(clip => <div key={clip.clip_id} className="flex flex-col gap-1.5 rounded-md p-2.5" style={{ border: '1px solid var(--border)' }}>
          <span className="text-xs">{clip.title}</span><audio controls preload="none" src={clip.url} className="w-full" />
        </div>)}

          </div>
        </details>
        {!video && <p className="m-0 text-xs" style={{ color: 'var(--muted)' }}>{t('postProduction.selectVideoForMusic')}</p>}
        {error && <p role="alert" className="m-0 whitespace-pre-wrap text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
      </CardContent>
    </Card>
  )
}
