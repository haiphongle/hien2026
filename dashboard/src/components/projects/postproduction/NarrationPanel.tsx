import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { AudioLines, Check, Download, FileAudio2, FileText, Mic2, Save, Upload } from 'lucide-react'
import { fetchAPI, patchAPI, resolveMediaUrl } from '../../../api/client'
import { usePolling } from '../../../api/usePolling'
import { PresetVoiceLibrary } from './PresetVoiceLibrary'
import { SPEECH_LANGUAGES } from './speechTypes'
import { assignNarrationBlocks, splitNarrationScript } from '../../../lib/narrationScript'
import { loadNarrationPreferences, saveNarrationPreferences } from '../../../lib/narrationPreferences'
import type { Orientation, Project, Scene, Video } from '../../../types'
import { useTranslation } from '../../../i18n/useTranslation'
import { Button } from '../../ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../ui/card'
import { Progress } from '../../ui/progress'
import type { ImportReport } from './timeline/types'

interface TTSStatus { available: boolean; python: string; reason: string | null }
interface VoiceTemplate { name: string; duration: number | null; source?: 'uploaded' | 'designed' }
interface VoiceSample { id: string; name: string; gender: string; region: string; duration: number | null; source: string; audio_url: string; sample_text: string }
interface NarrationAudio { scene_id: string; audio_url: string | null; duration: number | null; voice?: { engine: string; voice_name?: string } | null }
interface NarrationResult extends NarrationAudio { status: string; error?: string | null; display_order: number }
interface NarrationJob {
  job_id: string
  video_id: string
  status: string
  total: number
  completed: number
  stage?: string
  current_scene_id?: string | null
  scenes: NarrationResult[]
  error?: string | null
}
interface VoiceTemplateJob {
  job_id: string
  status: string
  progress: number
  stage: string
  step: number
  steps: number
  segment: number
  segments: number
  template: VoiceTemplate | null
  error?: string | null
}
interface Props { project: Project; video: Video | null; active: boolean; previewPlaying: boolean; onPreviewPause: () => void; onOpenTimedSpeech: (voiceId: string) => void; onApplyTimeline?: () => Promise<ImportReport | undefined>; onEditScene?: (sceneId: string) => void }
interface ShortenJob { id: string; kind: string; status: string; progress: number; stage: string; error?: string; result?: { scene_id: string; original: string; text: string } }

const controlStyle = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }
const DEFAULT_INSTRUCT = 'male, young adult, moderate pitch'
const DEFAULT_SAMPLE = 'Ngày tháng cũ vẫn còn nằm yên trong tiếng ve và màu hoa phượng.'
const VOICE_PRESETS = [
  { id: 'male_young', instruct: 'male, young adult, moderate pitch', name: 'nam_tre_tu_nhien' },
  { id: 'male_storyteller', instruct: 'male, young adult, low pitch', name: 'nam_tram_ke_chuyen' },
  { id: 'male_mature', instruct: 'male, middle-aged, very low pitch', name: 'nam_trung_nien' },
  { id: 'female_young', instruct: 'female, young adult, moderate pitch', name: 'nu_tre_nhe_nhang' },
  { id: 'female_mature', instruct: 'female, middle-aged, low pitch', name: 'nu_truong_thanh_am' },
  { id: 'female_whisper', instruct: 'female, young adult, whisper', name: 'nu_thi_tham' },
] as const

export function NarrationPanel({ project, video, active, previewPlaying, onPreviewPause, onOpenTimedSpeech, onApplyTimeline, onEditScene }: Props) {
  const { t } = useTranslation()
  const [preferences] = useState(() => loadNarrationPreferences(video?.id ?? ''))
  const previewAudio = useRef<HTMLAudioElement | null>(null)
  useEffect(() => { if (!active || previewPlaying) previewAudio.current?.pause() }, [active, previewPlaying])
  const [status, setStatus] = useState<TTSStatus | null>(null)
  const [engine, setEngine] = useState<'omnivoice' | 'vieneu' | 'kokoro' | 'qwen3'>(preferences?.engine ?? 'vieneu')
  const [qwenLanguage, setQwenLanguage] = useState(preferences?.qwenLanguage ?? 'en')
  const [nativePresetId, setNativePresetId] = useState(preferences?.nativePresetId ?? '')
  const [nativeAvailable, setNativeAvailable] = useState(false)
  const [scenesLoading, setScenesLoading] = useState(Boolean(video))
  const [scenesError, setScenesError] = useState('')
  const [scenesVersion, setScenesVersion] = useState(0)
  const [importedSource, setImportedSource] = useState<{ status: string } | null>(null)
  const [templates, setTemplates] = useState<VoiceTemplate[]>([])
  const [voiceSamples, setVoiceSamples] = useState<VoiceSample[]>([])
  const [voiceSampleId, setVoiceSampleId] = useState(preferences?.voiceSampleId ?? '')
  const [template, setTemplate] = useState(preferences?.template ?? '')
  const [invalidatedSceneIds, setInvalidatedSceneIds] = useState<string[]>(preferences?.invalidatedSceneIds ?? [])
  const [libraryError, setLibraryError] = useState('')
  const [libraryLoading, setLibraryLoading] = useState(true)
  const [libraryVersion, setLibraryVersion] = useState(0)
  const [previewVersion, setPreviewVersion] = useState(0)
  const [voiceMode, setVoiceMode] = useState<'design' | 'upload'>('design')
  const [voiceFile, setVoiceFile] = useState<File | null>(null)
  const [referenceText, setReferenceText] = useState('')
  const [scenes, setScenes] = useState<Scene[]>([])
  const [texts, setTexts] = useState<Record<string, string>>({})
  const [audios, setAudios] = useState<Record<string, NarrationAudio>>({})
  const [selectedSceneIds, setSelectedSceneIds] = useState<string[]>([])
  const [scriptDraft, setScriptDraft] = useState(() => { try { return localStorage.getItem(`flowkit:narration-input:${video?.id}`) ?? '' } catch { return '' } })
  const [scriptBlocks, setScriptBlocks] = useState<string[]>([])
  const [instruct, setInstruct] = useState(preferences?.instruct ?? DEFAULT_INSTRUCT)
  const [speed, setSpeed] = useState(preferences?.speed ?? 1)
  const [orientation, setOrientation] = useState<Orientation>(video?.orientation ?? 'VERTICAL')
  const sourceVolume = preferences?.sourceVolume ?? .35
  const narrationVolume = preferences?.narrationVolume ?? 1
  const burnSubtitles = preferences?.burnSubtitles ?? true
  const [saving, setSaving] = useState(false)
  const [creatingTemplate, setCreatingTemplate] = useState(false)
  const [voicePreset, setVoicePreset] = useState(VOICE_PRESETS.find(item => item.instruct === preferences?.instruct)?.id ?? (preferences ? 'custom' : 'male_young'))
  const [voiceJob, setVoiceJob] = useState<VoiceTemplateJob | null>(null)
  const [generating, setGenerating] = useState(false)
  const [applying, setApplying] = useState(false)
  const [autoApply, setAutoApply] = useState(preferences?.autoApply ?? true)
  const [importReport, setImportReport] = useState<ImportReport | null>(null)
  const [shortenJob, setShortenJob] = useState<ShortenJob | null>(null)
  const [shortenChecked, setShortenChecked] = useState(false)
  const [shortening, setShortening] = useState(false)
  const pendingApply = useRef<string | null>(preferences?.autoApplyJobId ?? null)
  const mixing = false
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [templateName, setTemplateName] = useState('phuc_giong_bac')
  const [sampleText, setSampleText] = useState(DEFAULT_SAMPLE)
  const [job, setJob] = useState<NarrationJob | null>(() => preferences?.jobId && video ? {
    job_id: preferences.jobId, video_id: video.id, status: 'QUEUED', total: 0, completed: 0, scenes: [],
  } : null)
  const selectedVideoId = video?.id ?? ''
  const jobId = job?.video_id === selectedVideoId ? job.job_id : null
  const voiceJobId = voiceJob?.job_id
  const [videoOrientation] = useState<Orientation>(video?.orientation ?? 'VERTICAL')

  useEffect(() => {
    saveNarrationPreferences(selectedVideoId, {
      engine, qwenLanguage, nativePresetId,
      voiceSampleId, template, instruct, speed, sourceVolume, narrationVolume, burnSubtitles, invalidatedSceneIds,
      jobId: job && ['QUEUED', 'PROCESSING'].includes(job.status) ? job.job_id : null,
      autoApply, autoApplyJobId: pendingApply.current,
    })
  }, [selectedVideoId, engine, qwenLanguage, nativePresetId, voiceSampleId, template, instruct, speed, sourceVolume, narrationVolume, burnSubtitles, invalidatedSceneIds, job, autoApply])

  useEffect(() => () => { previewAudio.current?.pause() }, [])

  function playPreview(audio: HTMLAudioElement) {
    onPreviewPause()
    if (previewAudio.current && previewAudio.current !== audio) previewAudio.current.pause()
    previewAudio.current = audio
  }

  function invalidateAudio() {
    setAudios({})
    setInvalidatedSceneIds(scenes.map(scene => scene.id))
  }

  useEffect(() => {
    let active = true
    Promise.all([
      fetchAPI<TTSStatus>('/api/tts/status'),
      fetchAPI<VoiceTemplate[]>('/api/tts/templates'),
    ]).then(([ready, savedTemplates]) => {
      if (!active) return
      setStatus(ready)
      setTemplates(savedTemplates)
    }).catch(cause => active && setError(cause instanceof Error ? cause.message : String(cause)))
    return () => { active = false }
  }, [preferences])

  useEffect(() => {
    const controller = new AbortController()
    setLibraryLoading(true)
    setLibraryError('')
    fetchAPI<VoiceSample[]>('/api/tts/voice-samples', { signal: controller.signal })
      .then(samples => { if (!controller.signal.aborted) setVoiceSamples(samples) })
      .catch(cause => { if (!controller.signal.aborted) setLibraryError(cause instanceof Error ? cause.message : String(cause)) })
      .finally(() => { if (!controller.signal.aborted) setLibraryLoading(false) })
    return () => controller.abort()
  }, [libraryVersion])

  usePolling<VoiceTemplateJob>(
    voiceJobId && !['COMPLETED', 'FAILED'].includes(voiceJob?.status ?? '')
      ? `/api/tts/template-jobs/${voiceJobId}` : null,
    1000,
    next => {
      setVoiceJob(next)
      const saved = next.template
      if (next.status === 'COMPLETED' && saved) {
        setTemplates(current => [...current.filter(item => item.name !== saved.name), saved]
          .sort((a, b) => a.name.localeCompare(b.name)))
        setTemplate(saved.name)
        setVoiceSampleId('')
        setPreviewVersion(Date.now())
        invalidateAudio()
        setMessage(t('postProduction.voiceSaved', { name: saved.name }))
      } else if (next.status === 'FAILED') {
        setError(next.error || t('postProduction.voiceJobFailed'))
      }
    },
    cause => setError(cause instanceof Error ? cause.message : String(cause)),
  )

  useEffect(() => {
    let active = true
    setScenes([])
    setTexts({})
    setAudios({})
    setSelectedSceneIds([])
    setScenesLoading(Boolean(selectedVideoId))
    setScenesError('')
    setImportedSource(null)
    setOrientation(videoOrientation)
    setMessage('')
    setError('')
    if (!selectedVideoId) return () => { active = false }
    Promise.allSettled([
      fetchAPI<Scene[]>(`/api/scenes?video_id=${selectedVideoId}`),
      fetchAPI<NarrationAudio[]>(`/api/videos/${selectedVideoId}/narrations`),
    ]).then(async ([sceneResult, audioResult]) => {
      if (!active) return
      if (sceneResult.status === 'rejected') {
        setScenesError(String(sceneResult.reason))
        return
      }
      const ordered = [...sceneResult.value].sort((a, b) => a.display_order - b.display_order)
      // An empty Flow video has no usable timeline; only a ready upload can
      // move directly into timed narration without first creating scene clips.
      if (!ordered.length) {
        const workbench = await fetchAPI<{ source: { status: string } | null }>(`/api/videos/${selectedVideoId}/workbench`)
        if (!active) return
        setImportedSource(workbench.source)
      }
      setScenes(ordered)
      const loaded = Object.fromEntries(ordered.map(scene => [scene.id, scene.narrator_text ?? '']))
      try {
        const draft = JSON.parse(localStorage.getItem(`flowkit:narration-draft:${selectedVideoId}`) ?? 'null')
        if (draft?.texts && draft?.base) for (const id of Object.keys(loaded)) {
          if (draft.base[id] === loaded[id] && typeof draft.texts[id] === 'string') loaded[id] = draft.texts[id]
        }
      } catch { /* Ignore stale/unreadable drafts. */ }
      setTexts(loaded)
      setSelectedSceneIds(ordered.filter(scene => scene.narrator_text?.trim()).map(scene => scene.id))
      if (audioResult.status === 'fulfilled') {
        setAudios(current => ({ ...Object.fromEntries(audioResult.value.filter(item => item.audio_url && !preferences?.invalidatedSceneIds.includes(item.scene_id)).map(item => [item.scene_id, item])), ...current }))
      } else setError(String(audioResult.reason))
    })
      .catch(cause => { if (active) setScenesError(cause instanceof Error ? cause.message : String(cause)) })
      .finally(() => { if (active) setScenesLoading(false) })
    return () => { active = false }
  }, [selectedVideoId, videoOrientation, preferences, scenesVersion])

  usePolling<NarrationJob>(
    jobId && selectedVideoId && !['COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED'].includes(job?.status ?? '')
      ? `/api/tts/jobs/${jobId}` : null,
    1500,
    next => {
      if (next.video_id !== selectedVideoId) return
      setJob(next)
      setInvalidatedSceneIds(current => current.filter(id => !next.scenes.some(item => item.scene_id === id && item.status === 'COMPLETED')))
      setAudios(current => {
        const updated = { ...current }
        for (const item of next.scenes) {
          if (item.audio_url) updated[item.scene_id] = item
          else if (item.status === 'FAILED') delete updated[item.scene_id]
        }
        return updated
      })
      if (['COMPLETED', 'COMPLETED_WITH_ERRORS'].includes(next.status)) {
        const failures = next.scenes.filter(item => item.status === 'FAILED')
        setMessage(t('postProduction.narrationGenerated', {
          n: next.scenes.filter(item => item.status === 'COMPLETED').length,
          failed: failures.length,
        }))
        if (failures.length) {
          setError(failures.map(item => item.error).filter(Boolean).join('\n') || t('postProduction.narrationJobPartialFailure'))
        }
        if (pendingApply.current === next.job_id && !failures.length) {
          pendingApply.current = null
          void applyNarration()
        }
      } else if (next.status === 'FAILED') {
        pendingApply.current = null
        setError(next.error || t('postProduction.narrationJobFailed'))
      }
    },
    cause => {
      const detail = cause instanceof Error ? cause.message : String(cause)
      setError(detail)
      if (detail.startsWith('API 404:')) setJob(current => current ? { ...current, status: 'FAILED', error: detail } : null)
    },
  )

  const dirtySceneIds = useMemo(() => scenes.filter(scene =>
    (scene.narrator_text ?? '').trim() !== (texts[scene.id] ?? '').trim()
  ).map(scene => scene.id), [scenes, texts])
  const textSceneCount = scenes.filter(scene => (texts[scene.id] ?? '').trim()).length
  const jobRunning = Boolean(jobId && job && ['QUEUED', 'PROCESSING'].includes(job.status))
  const voiceJobRunning = Boolean(voiceJob && ['QUEUED', 'PROCESSING'].includes(voiceJob.status))
  const shortenRunning = Boolean(shortenJob && ['QUEUED', 'PROCESSING'].includes(shortenJob.status))
  const canGenerate = engine !== 'omnivoice' ? nativeAvailable && Boolean(nativePresetId) : Boolean(status?.available)
  const generationBusy = scenesLoading || saving || mixing || voiceJobRunning || creatingTemplate || generating || jobRunning || applying || shortening || shortenRunning
  usePolling<ShortenJob | null>(selectedVideoId && (!shortenChecked || shortenRunning) ? `/api/videos/${selectedVideoId}/timeline/job` : null, 1500,
    value => { if (value?.kind === 'narration-shorten' && (!shortenJob || value.id === shortenJob.id)) setShortenJob(value); setShortenChecked(true) }, cause => setError(String(cause)))
  const generationHint = generationBusy ? t('postProduction.narrationBusy')
    : !canGenerate ? t(engine !== 'omnivoice' && !nativePresetId ? 'postProduction.narrationChooseVoice' : 'postProduction.narrationEngineNotReady')
      : !textSceneCount ? t('postProduction.narrationAddText')
        : !selectedSceneIds.length ? t('postProduction.narrationChooseScene') : ''
  useEffect(() => {
    if (scenesLoading || !scenes.length) return
    try {
      const key = `flowkit:narration-draft:${selectedVideoId}`
      if (dirtySceneIds.length) localStorage.setItem(key, JSON.stringify({ base: Object.fromEntries(scenes.map(s => [s.id, s.narrator_text ?? ''])), texts }))
      else localStorage.removeItem(key)
    } catch { /* Server save remains available. */ }
  }, [scenesLoading, scenes, texts, dirtySceneIds, selectedVideoId])
  useEffect(() => {
    try { localStorage.setItem(`flowkit:narration-input:${selectedVideoId}`, scriptDraft) } catch { /* optional */ }
  }, [selectedVideoId, scriptDraft])

  async function saveText() {
    if (!dirtySceneIds.length) return true
    setSaving(true)
    setError('')
    setMessage('')
    try {
      await Promise.all(dirtySceneIds.map(sceneId => patchAPI(`/api/scenes/${sceneId}`, { narrator_text: texts[sceneId].trim() || null })))
      setScenes(current => current.map(scene => dirtySceneIds.includes(scene.id) ? { ...scene, narrator_text: texts[scene.id].trim() || null } : scene))
      setMessage(t('postProduction.narrationTextSaved'))
      return true
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
      return false
    } finally {
      setSaving(false)
    }
  }

  async function createVoice(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setCreatingTemplate(true)
    setError('')
    try {
      let saved: VoiceTemplate
      if (voiceMode === 'upload') {
        if (!voiceFile || !referenceText.trim()) return
        const extension = voiceFile.name.split('.').pop()?.toLowerCase() ?? ''
        const transcript = btoa(Array.from(new TextEncoder().encode(referenceText.trim()), byte => String.fromCharCode(byte)).join(''))
        saved = await fetchAPI<VoiceTemplate>('/api/tts/templates/import', {
          method: 'POST',
          body: voiceFile,
          headers: {
            'X-Voice-Name': templateName.trim(),
            'X-Voice-Extension': extension,
            'X-Voice-Transcript-Base64': transcript,
          },
        })
      } else {
        if (!status?.available || sampleText.trim().length < 3) return
        const queued = await fetchAPI<{ job_id: string; status: string }>('/api/tts/templates/jobs', {
          method: 'POST',
          body: JSON.stringify({ name: templateName.trim(), text: sampleText.trim(), instruct: instruct.trim(), speed }),
        })
        setVoiceJob({
          job_id: queued.job_id,
          status: queued.status,
          progress: 0,
          stage: 'waiting',
          step: 0,
          steps: 32,
          segment: 1,
          segments: 1,
          template: null,
        })
        setMessage('')
        return
      }
      const all = await fetchAPI<VoiceTemplate[]>('/api/tts/templates')
      setTemplates(all)
      setTemplate(saved.name)
      setVoiceSampleId('')
      setPreviewVersion(Date.now())
      invalidateAudio()
      setVoiceFile(null)
      setMessage(t('postProduction.voiceSaved', { name: saved.name }))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setCreatingTemplate(false)
    }
  }

  function applyVoicePreset(presetId: string) {
    setVoicePreset(presetId)
    setVoiceSampleId('')
    setTemplate('')
    invalidateAudio()
    const preset = VOICE_PRESETS.find(item => item.id === presetId)
    if (!preset) return
    setInstruct(preset.instruct)
    setTemplateName(preset.name)
  }

  const voiceStage = voiceJob?.stage === 'waiting' ? t('postProduction.voiceWaiting')
    : voiceJob?.stage === 'loading_model' ? t('postProduction.voiceLoadingModel')
      : voiceJob?.stage === 'model_ready' || voiceJob?.stage === 'synthesizing' ? t('postProduction.voiceSynthesizing')
        : voiceJob?.stage === 'saving_audio' ? t('postProduction.voiceSavingAudio')
          : voiceJob?.stage === 'completed' ? t('postProduction.voiceComplete') : ''

  function classifyScript() {
    const blocks = splitNarrationScript(scriptDraft)
    setScriptBlocks(blocks)
    return blocks
  }

  function applyScript() {
    const blocks = scriptBlocks.length ? scriptBlocks : classifyScript()
    if (!blocks.length) return
    const ordered = [...scenes].sort((a, b) => a.display_order - b.display_order)
    const mapped = assignNarrationBlocks(ordered.map(scene => scene.id), blocks)
    setTexts(current => ({ ...current, ...mapped }))
    setSelectedSceneIds(ordered.filter(scene => Boolean(mapped[scene.id]?.trim())).map(scene => scene.id))
    setAudios(current => {
      const updated = { ...current }
      for (const sceneId of Object.keys(mapped)) delete updated[sceneId]
      return updated
    })
    setMessage(t('postProduction.scriptAssigned', { n: Object.keys(mapped).length }))
  }

  async function generateNarrations(sceneIds: string[]) {
    if (!video || !canGenerate || !sceneIds.length || generationBusy) return
    setGenerating(true)
    setError('')
    setMessage('')
    try {
      if (!await saveText()) return
      const queued = await fetchAPI<{ job_id: string; status: string }>(`/api/videos/${video.id}/narrate/jobs`, {
        method: 'POST',
        body: JSON.stringify({
          project_id: project.id,
          engine,
          preset_id: engine !== 'omnivoice' ? nativePresetId : null,
          orientation,
          speed,
          instruct: engine === 'omnivoice' ? instruct.trim() : null,
          template: engine === 'omnivoice' && !voiceSampleId ? template || null : null,
          voice_sample_id: engine === 'omnivoice' ? voiceSampleId || null : null,
          scene_ids: sceneIds,
          mix: false,
          force: false,
        }),
      })
      setJob({ job_id: queued.job_id, video_id: video.id, status: queued.status, total: sceneIds.length, completed: 0, scenes: [] })
      pendingApply.current = autoApply && onApplyTimeline ? queued.job_id : null
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setGenerating(false)
    }
  }

  async function applyNarration() {
    if (!onApplyTimeline || applying) return
    setApplying(true); setError(''); setImportReport(null)
    try {
      const report = await onApplyTimeline()
      if (report) {
        setImportReport(report)
        setMessage(`Đã nạp giọng ${report.voices} đoạn${report.fitted ? ` · ${report.fitted} đoạn được tăng tốc nhẹ` : ''}.`)
      }
    } catch (cause) { setError(String(cause)) }
    finally { setApplying(false) }
  }

  async function shortenScene(item: ImportReport['blocked'][number]) {
    setShortening(true); setError(''); setShortenJob(null)
    try {
      if (!await saveText()) return
      setShortenJob(await fetchAPI<ShortenJob>(`/api/videos/${selectedVideoId}/narrate/shorten`, {
        method: 'POST', body: JSON.stringify({ scene_id: item.scene_id, frame: item.frame }),
      }))
    } catch (cause) { setError(String(cause)) }
    finally { setShortening(false) }
  }


  return (
    <Card className="py-4">
      <CardHeader>
        <div className="flex items-center gap-2"><Mic2 className="size-4" style={{ color: 'var(--accent)' }} /><CardTitle className="text-sm">{t('postProduction.narrationTitle')}</CardTitle></div>
        <CardDescription className="text-xs">{t('postProduction.narrationHelp')}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <details className="pp-disclosure">
          <summary>{t('postEditor.settings')}<small>{engine === 'vieneu' ? 'VieNeu v3 Turbo' : engine === 'kokoro' ? 'Kokoro · Tiếng Anh' : engine === 'qwen3' ? 'Qwen3 · đa ngôn ngữ' : 'OmniVoice'} · {template || voiceSamples.find(sample => sample.id === voiceSampleId)?.name || t('postProduction.voiceTemplate')}</small></summary>
          <div className="pp-disclosure-body">
        <label className="flex flex-col gap-1 text-xs">{t('vieneu.engine')}
          <select value={engine} disabled={generating || jobRunning || mixing || creatingTemplate || voiceJobRunning} onChange={event => { setEngine(event.target.value as 'omnivoice' | 'vieneu' | 'kokoro' | 'qwen3'); setNativePresetId(''); setNativeAvailable(false); invalidateAudio() }} className="rounded-md px-3 py-2 text-xs" style={controlStyle}>
            <option value="vieneu">{t('vieneu.engineNative')}</option>
            <option value="kokoro">Kokoro · Tiếng Anh (Mỹ / Anh)</option>
            <option value="qwen3">Qwen3 · 10 ngôn ngữ / 9 chất giọng</option>
            <option value="omnivoice">{t('vieneu.engineOmni')}</option>
          </select>
        </label>
        {engine === 'qwen3' && <label className="flex flex-col gap-1 text-xs">Ngôn ngữ Qwen3
          <select aria-label="Ngôn ngữ Qwen3" value={qwenLanguage} disabled={generating || jobRunning} onChange={event => { setQwenLanguage(event.target.value); setNativePresetId(''); setNativeAvailable(false); invalidateAudio() }} className="rounded-md px-3 py-2 text-xs" style={controlStyle}>
            {Object.entries(SPEECH_LANGUAGES).filter(([code]) => code !== 'vi').map(([code, name]) => <option key={code} value={code}>{name}</option>)}
          </select>
        </label>}
        {engine !== 'omnivoice' && <PresetVoiceLibrary key={`${engine}:${qwenLanguage}`} engine={engine} language={qwenLanguage} selected={nativePresetId} disabled={scenesLoading || generating || jobRunning || mixing || creatingTemplate || voiceJobRunning} onReadyChange={setNativeAvailable} onPlay={playPreview} onSelect={id => { if (id !== nativePresetId) { setNativePresetId(id); invalidateAudio() } }} />}
        {engine === 'omnivoice' && status && !status.available && (
          <div className="rounded-md p-3 text-xs" style={{ border: '1px solid var(--yellow)' }}>
            <div>{t('postProduction.ttsSetupRequired')}</div>
            {status?.python && <code className="mt-1 block break-all text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.ttsPython', { path: status.python })}</code>}
          </div>
        )}
        <form onSubmit={createVoice} className="flex flex-col gap-3 rounded-md p-3" style={{ background: 'var(--surface)' }}>
          <fieldset disabled={scenesLoading || generating || jobRunning || mixing || creatingTemplate || voiceJobRunning} className="contents">
          {engine === 'omnivoice' && <>
          <section className="flex flex-col gap-2 rounded-md p-3" aria-label={t('postProduction.demoVoices')} style={{ border: '1px solid var(--border)' }}>
            <div className="flex items-center justify-between gap-2 text-xs font-medium">
              <span>{t('postProduction.demoVoices')}</span>
              <span className="tabular-nums" style={{ color: 'var(--muted)' }}>{voiceSamples.length}</span>
            </div>
            <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.demoVoicesHelp')}</p>
            {libraryLoading ? <p role="status" className="m-0 text-xs">{t('postProduction.loadingDemoVoices')}</p>
              : libraryError ? <div role="alert" className="text-xs" style={{ color: 'var(--red)' }}>
                <p>{t('postProduction.demoVoicesFailed')}</p>
                <p className="break-words">{libraryError}</p>
                <Button type="button" size="sm" variant="outline" onClick={() => setLibraryVersion(value => value + 1)}>{t('postProduction.retryDemoVoices')}</Button>
              </div>
              : voiceSamples.length > 0 ? <div className="grid gap-2 sm:grid-cols-2">
              {voiceSamples.map(sample => {
                const selected = voiceSampleId === sample.id
                return <article key={sample.id} className="flex min-w-0 flex-col gap-2 rounded-md p-2.5" style={{ border: `1px solid ${selected ? 'var(--accent)' : 'var(--border)'}` }}>
                  <div className="flex items-center justify-between gap-2 text-[11px]">
                    <span className="font-medium">{sample.name}</span>
                    <span style={{ color: 'var(--muted)' }}>{sample.gender} · {sample.region}{sample.duration ? ` · ${sample.duration.toFixed(1)}s` : ''}</span>
                  </div>
                  <audio controls preload="none" src={resolveMediaUrl(sample.audio_url)} onPlay={event => playPreview(event.currentTarget)} onError={() => setLibraryError(t('postProduction.demoAudioFailed', { name: sample.name }))} aria-label={t('postProduction.previewVoice', { name: sample.name })} className="w-full" />
                  <p className="m-0 line-clamp-2 text-[10px]" style={{ color: 'var(--muted)' }}>{sample.sample_text}</p>
                  <Button type="button" size="sm" aria-pressed={selected} variant={selected ? 'default' : 'outline'} onClick={() => { if (!selected) { setVoiceSampleId(sample.id); setTemplate(''); invalidateAudio() } }}>
                    {selected ? <><Check className="size-3.5" />{t('postProduction.voiceSelected')}</> : t('postProduction.selectVoice')}
                  </Button>
                </article>
              })}
            </div> : <p className="m-0 text-[11px]" style={{ color: 'var(--muted)' }}>{t('postProduction.noDemoVoices')}</p>}
          </section>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-xs font-medium">{t('postProduction.voiceTemplate')}</div>
            <div className="flex gap-1">
              <Button type="button" size="sm" variant={voiceMode === 'design' ? 'default' : 'outline'} onClick={() => setVoiceMode('design')}>{t('postProduction.designVoice')}</Button>
              <Button type="button" size="sm" variant={voiceMode === 'upload' ? 'default' : 'outline'} onClick={() => setVoiceMode('upload')}><FileAudio2 className="size-3.5" />{t('postProduction.uploadVoice')}</Button>
            </div>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1 text-xs">{t('postProduction.voiceName')}
              <input value={templateName} onChange={event => setTemplateName(event.target.value.replace(/[^a-zA-Z0-9_-]/g, '_').slice(0, 64))} maxLength={64} className="rounded-md px-3 py-2 text-xs" style={controlStyle} />
            </label>
            {voiceMode === 'design' && <div className="flex flex-col gap-2 text-xs">
              <label className="flex flex-col gap-1">{t('postProduction.voicePreset')}
                <select value={voicePreset} onChange={event => applyVoicePreset(event.target.value)} className="rounded-md px-2.5 py-2 text-xs" style={controlStyle}>
                  {VOICE_PRESETS.map(preset => <option key={preset.id} value={preset.id}>{t(`postProduction.voicePreset.${preset.id}`)}</option>)}
                  <option value="custom">{t('postProduction.voicePreset.custom')}</option>
                </select>
              </label>
              <label className="flex flex-col gap-1">{t('postProduction.voiceStyle')}
                <input value={instruct} onChange={event => { setInstruct(event.target.value); setVoicePreset('custom'); setVoiceSampleId(''); setTemplate(''); invalidateAudio() }} maxLength={200} className="rounded-md px-3 py-2 text-xs" style={controlStyle} />
                <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.voiceStyleHelp')}</span>
              </label>
            </div>}
          </div>
          {voiceMode === 'design' ? <>
            <label className="flex flex-col gap-1 text-xs">{t('postProduction.voiceSample')}
              <input value={sampleText} onChange={event => setSampleText(event.target.value)} maxLength={5000} className="rounded-md px-3 py-2 text-xs" style={controlStyle} />
            </label>
          </> : <>
            <label className="flex flex-col gap-1 text-xs">{t('postProduction.referenceAudio')}
              <input type="file" accept="audio/wav,audio/mpeg,audio/mp4,audio/flac,audio/aac,audio/ogg,.wav,.mp3,.m4a,.flac,.aac,.ogg" onChange={event => setVoiceFile(event.target.files?.[0] ?? null)} className="rounded-md px-3 py-2 text-xs" style={controlStyle} />
            </label>
            <label className="flex flex-col gap-1 text-xs">{t('postProduction.referenceTranscript')}
              <textarea value={referenceText} onChange={event => setReferenceText(event.target.value)} rows={2} maxLength={2000} className="resize-y rounded-md px-3 py-2 text-xs" style={controlStyle} placeholder={t('postProduction.referenceTranscriptPlaceholder')} />
            </label>
            <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.referenceAudioHelp')}</p>
          </>}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" size="sm" variant="outline" disabled={creatingTemplate || voiceJobRunning || !templateName.trim() || (voiceMode === 'design' ? !status?.available || sampleText.trim().length < 3 : !voiceFile || !referenceText.trim())}>
              {voiceJobRunning || creatingTemplate ? t('postProduction.savingVoice') : voiceMode === 'upload' ? <><Upload className="size-3.5" />{t('postProduction.saveReferenceVoice')}</> : t('postProduction.createVoice')}
            </Button>
          </div>
          {(voiceJobRunning || voiceJob?.status === 'COMPLETED') && <div className="flex flex-col gap-2 rounded-md p-3" role="status" aria-live="polite" style={{ border: '1px solid var(--border)' }}>
            <div className="flex items-center justify-between gap-2 text-xs"><span>{voiceStage}</span><span className="font-semibold tabular-nums">{voiceJob?.progress ?? 0}%</span></div>
            <Progress value={voiceJob?.progress ?? 0} className="h-2" />
            {voiceJob?.stage === 'synthesizing' && <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.voiceDecodeSteps', { step: voiceJob.step, total: voiceJob.steps, segment: voiceJob.segment, segments: voiceJob.segments })}</span>}
          </div>}
          <section className="flex flex-col gap-2 rounded-md p-3" aria-label={t('postProduction.savedVoices')} style={{ border: '1px solid var(--border)' }}>
            <div className="flex items-center justify-between gap-2 text-xs font-medium"><span>{t('postProduction.savedVoices')}</span><span className="tabular-nums" style={{ color: 'var(--muted)' }}>{templates.length}</span></div>
            <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.voiceStoragePath')}</p>
            {templates.length > 0 ? <>
              <label className="flex flex-col gap-1 text-xs">
                <select value={template} onChange={event => { setTemplate(event.target.value); setVoiceSampleId(''); invalidateAudio() }} className="rounded-md px-2.5 py-2 text-xs" style={controlStyle}>
                  <option value="">{t('postProduction.noSavedVoiceSelected')}</option>
                  {templates.map(item => <option key={item.name} value={item.name}>{item.name}{item.source === 'uploaded' ? ` · ${t('postProduction.uploadedVoice')}` : ''}{item.duration ? ` · ${item.duration.toFixed(1)}s` : ''}</option>)}
                </select>
              </label>
              {template && <audio controls preload="none" src={resolveMediaUrl(`/api/tts/templates/${encodeURIComponent(template)}/audio?v=${previewVersion}`)} onPlay={event => playPreview(event.currentTarget)} className="w-full" />}
              {voiceSampleId && <p className="m-0 text-[10px]" style={{ color: 'var(--accent)' }}>{t('postProduction.usingDemoVoice', { name: voiceSamples.find(item => item.id === voiceSampleId)?.name ?? voiceSampleId })}</p>}
            </> : <p className="m-0 text-[11px]" style={{ color: 'var(--muted)' }}>{t('postProduction.noSavedVoices')}</p>}
          </section>
          </>}
          <label className="flex items-center gap-2 text-[11px]">{t('postProduction.ttsSpeed')} <input type="range" min="0.5" max="1.5" step="0.05" value={speed} onChange={event => { setSpeed(Number(event.target.value)); invalidateAudio() }} />{speed.toFixed(2)}×</label>
          </fieldset>
        </form>
          </div>
        </details>

        {!video ? <p className="m-0 text-xs" style={{ color: 'var(--muted)' }}>{t('postProduction.selectVideoForNarration')}</p>
          : scenesLoading ? <p role="status" className="m-0 text-xs">{t('postProduction.narrationLoadingScenes')}</p>
            : scenesError ? <div role="alert" className="space-y-2 text-xs">
              <p>{t('postProduction.narrationScenesFailed')} {scenesError}</p>
              <Button type="button" size="sm" variant="outline" onClick={() => setScenesVersion(value => value + 1)}>{t('postProduction.narrationRetryScenes')}</Button>
            </div>
              : !scenes.length ? <section className="space-y-3 rounded-md p-4 text-xs" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
                {!importedSource ? <>
                  <h3 className="m-0 text-sm font-medium">{t('postProduction.narrationEmptyFlow')}</h3>
                  <p className="m-0" style={{ color: 'var(--muted)' }}>{t('postProduction.narrationEmptyFlowHelp')}</p>
                  <Button asChild size="sm"><Link to={`/projects/${project.id}?tab=pipeline&video=${video.id}`}>{t('postProduction.narrationOpenPipeline')}</Link></Button>
                </> : importedSource.status !== 'READY' ? <>
                  <p role="status" className="m-0">{t('postProduction.narrationImportNotReady')}</p>
                  <Button type="button" size="sm" variant="outline" onClick={() => setScenesVersion(value => value + 1)}>{t('postProduction.narrationRetrySource')}</Button>
                </> : <>
                  <h3 className="m-0 text-sm font-medium">{t('postProduction.narrationNoScenes')}</h3>
                  <p className="m-0" style={{ color: 'var(--muted)' }}>{t('postProduction.narrationNoScenesHelp')}</p>
                  <Button type="button" size="sm" onClick={() => onOpenTimedSpeech(engine !== 'omnivoice' ? nativePresetId : '')}><AudioLines className="size-3.5" />{t('postProduction.openTimedSpeech')}</Button>
                  <p className="m-0" style={{ color: 'var(--muted)' }}>{t('postProduction.timedSpeechHelp')}</p>
                </>}
              </section> : <>
          <details className="pp-disclosure">
            <summary><FileText className="mr-2 inline size-4" />{t('postEditor.importScript')}</summary>
            <div className="pp-disclosure-body">
            <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.pasteScriptHelp')}</p>
            <textarea value={scriptDraft} onChange={event => { setScriptDraft(event.target.value); setScriptBlocks([]) }} rows={5} maxLength={30000} className="resize-y rounded-md px-3 py-2 text-xs" style={controlStyle} placeholder={t('postProduction.pasteScriptPlaceholder')} />
            <div className="flex flex-wrap items-center gap-2">
              <Button type="button" size="sm" variant="outline" disabled={!scriptDraft.trim()} onClick={() => classifyScript()}>{t('postProduction.classifyScript')}</Button>
              <Button type="button" size="sm" disabled={saving || generating || jobRunning || mixing || !scenes.length || !(scriptBlocks.length || scriptDraft.trim())} onClick={applyScript}><Check className="size-3.5" />{t('postProduction.applyScript')}</Button>
              {scriptBlocks.length > 0 && <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.scriptBlockCount', { n: scriptBlocks.length, scenes: scenes.length })}</span>}
            </div>
            {scriptBlocks.length > 0 && <ol className="m-0 flex max-h-48 list-decimal flex-col gap-1 overflow-auto pl-5 text-[10px]" style={{ color: 'var(--muted)' }}>
              {scriptBlocks.map((block, index) => <li key={`${index}-${block.slice(0, 12)}`}><span className="line-clamp-2">{block}</span></li>)}
            </ol>}
            </div>
          </details>

          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs font-medium">{t('postProduction.sceneNarration')}</span>
            <span className="text-[10px]" style={{ color: dirtySceneIds.length ? 'var(--yellow)' : 'var(--muted)' }}>
              {dirtySceneIds.length ? t('postProduction.unsavedNarration', { n: dirtySceneIds.length }) : t('postProduction.narrationScenes', { n: textSceneCount })}
            </span>
            <span className="ml-auto" />
            {dirtySceneIds.length > 0 && <Button type="button" size="sm" variant="outline" disabled={saving} onClick={() => void saveText()}><Save className="size-3.5" />{saving ? t('postProduction.savingText') : t('postProduction.saveText')}</Button>}
          </div>
          {scenes.map((scene, index) => (
            <div key={scene.id} className="flex flex-col gap-2 rounded-md p-3" style={{ border: '1px solid var(--border)' }}>
              <div className="flex items-center gap-2">
                <input aria-label={t('postProduction.selectNarrationScene', { n: index + 1 })} type="checkbox" checked={selectedSceneIds.includes(scene.id)} disabled={!(texts[scene.id] ?? '').trim()} onChange={event => setSelectedSceneIds(current => event.target.checked ? [...new Set([...current, scene.id])] : current.filter(id => id !== scene.id))} />
                <label htmlFor={'narration-' + scene.id} className="text-[11px] font-medium">{t('postProduction.sceneNumber', { n: index + 1 })}</label>
                {audios[scene.id]?.duration != null && <span className="ml-auto text-[10px]" style={{ color: 'var(--muted)' }}>{audios[scene.id].duration?.toFixed(1)}s</span>}
              </div>
              <textarea id={'narration-' + scene.id} disabled={saving || generating || jobRunning || mixing} rows={2} maxLength={1500} value={texts[scene.id] ?? ''} onChange={event => {
                const value = event.target.value
                setTexts(current => ({ ...current, [scene.id]: value }))
                setSelectedSceneIds(current => value.trim() ? [...new Set([...current, scene.id])] : current.filter(id => id !== scene.id))
                setAudios(current => { const next = { ...current }; delete next[scene.id]; return next })
              }} placeholder={t('postProduction.narrationPlaceholder')} className="resize-y rounded-md px-3 py-2 text-xs" style={controlStyle} />
              {audios[scene.id]?.audio_url && <div className="flex flex-col gap-1.5">
                {audios[scene.id].voice && <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{audios[scene.id].voice?.engine === 'vieneu' ? 'VieNeu v3 Turbo' : audios[scene.id].voice?.engine === 'kokoro' ? 'Kokoro · Tiếng Anh' : audios[scene.id].voice?.engine === 'qwen3' ? 'Qwen3' : 'OmniVoice'}{audios[scene.id].voice?.voice_name ? ` · ${audios[scene.id].voice?.voice_name}` : ''}</span>}
                <audio controls preload="none" onPlay={event => playPreview(event.currentTarget)} src={resolveMediaUrl(audios[scene.id].audio_url)} className="w-full" />
                <a href={resolveMediaUrl(audios[scene.id].audio_url)} download className="inline-flex items-center gap-1 self-start text-[10px]" style={{ color: 'var(--accent)' }}><Download className="size-3" />{t('postProduction.downloadNarration')}</a>
              </div>}
              {job?.scenes.find(item => item.scene_id === scene.id && item.status === 'FAILED')?.error && <p className="m-0 text-[10px]" style={{ color: 'var(--red)' }}>{job.scenes.find(item => item.scene_id === scene.id)?.error}</p>}
            </div>
          ))}
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" size="sm" aria-describedby={generationHint ? 'narration-generation-hint' : undefined} disabled={!canGenerate || generationBusy || selectedSceneIds.length === 0} onClick={() => void generateNarrations(selectedSceneIds)}><AudioLines className="size-3.5" />{generating ? t('postProduction.queueingNarration') : t('postProduction.generateSelected')}</Button>
            <Button type="button" size="sm" variant="outline" aria-describedby={generationHint ? 'narration-generation-hint' : undefined} disabled={!canGenerate || generationBusy || textSceneCount === 0} onClick={() => void generateNarrations(scenes.filter(scene => (texts[scene.id] ?? '').trim()).map(scene => scene.id))}>{t('postProduction.generateAll')}</Button>
          </div>
          {generationHint && <p id="narration-generation-hint" role="status" className="m-0 text-xs" style={{ color: 'var(--muted)' }}>{generationHint}</p>}
          {onApplyTimeline && <div className="pp-mix-box"><label><input type="checkbox" checked={autoApply} disabled={generationBusy} onChange={e => setAutoApply(e.target.checked)} /> Tạo xong tự căn giọng và đưa lên timeline</label><p>Giọng vượt khung ít được tăng tốc nhẹ (tối đa 15%). File gốc vẫn được giữ.</p><Button disabled={generationBusy || !textSceneCount} onClick={async () => { if (await saveText()) await applyNarration() }}>{applying ? 'Đang căn giọng và cập nhật…' : 'Đưa lời đọc lên timeline'}</Button>
            {importReport?.blocked.map(item => <div key={item.scene_id}><p role="alert">{item.name}: giọng {item.duration.toFixed(2)}s / khung {item.frame.toFixed(2)}s. Giữ giọng cũ.</p><Button disabled={generationBusy} variant="outline" onClick={() => void shortenScene(item)}>AI rút gọn {item.name.toLowerCase()}</Button>{onEditScene && <Button disabled={generationBusy} variant="ghost" onClick={() => onEditScene(item.scene_id)}>Điều chỉnh cảnh trên timeline</Button>}</div>)}
            {shortenJob && <div role="status"><p>{shortenJob.stage} · {shortenJob.progress}%</p>{shortenRunning && <><Progress value={shortenJob.progress} /><Button variant="ghost" onClick={() => void fetchAPI(`/api/videos/${selectedVideoId}/workbench/cancel`, { method: 'POST' }).catch(cause => setError(String(cause)))}>Dừng AI rút gọn</Button></>}{shortenJob.error && <p role="alert">{shortenJob.error}</p>}
              {shortenJob.status === 'COMPLETED' && shortenJob.result && <><p>Đề xuất: {shortenJob.result.text}</p><Button disabled={generationBusy || texts[shortenJob.result.scene_id]?.trim() !== shortenJob.result.original} onClick={() => {
                const result = shortenJob.result!
                setTexts(old => ({ ...old, [result.scene_id]: result.text })); setSelectedSceneIds([result.scene_id]); setShortenJob(null)
                setMessage('Đã chọn lời rút gọn. Bấm Tạo cảnh đã chọn để lưu, tạo lại giọng và tự cập nhật timeline.')
              }}>Dùng bản rút gọn</Button></>}
            </div>}
          </div>}

          {jobRunning && <div role="status" className="rounded-md p-3 text-xs" style={{ background: 'var(--surface)', color: 'var(--muted)' }}>
            {job?.stage === 'loading_model' ? t('vieneu.loadingModel') : job?.status === 'QUEUED' ? t('vieneu.queued') : t('postProduction.narrationJobProgress', { n: job?.completed ?? 0, total: job?.total ?? 0 })}
            {job?.current_scene_id && ` · ${t('postProduction.sceneNumber', { n: (scenes.find(scene => scene.id === job.current_scene_id)?.display_order ?? 0) + 1 })}`}
            <span className="ml-2 font-semibold">{job?.total ? Math.floor(job.completed / job.total * 100) : 0}%</span>
            <Progress value={job?.total ? job.completed / job.total * 100 : 0} className="mt-2 h-2" />
            <p className="mb-0 text-[10px]">{t('vieneu.narrationProgressHelp')}</p>
          </div>}
          <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.narrationLocalNote')}</p>
        </>}
        {message && <p role="status" className="m-0 text-xs" style={{ color: 'var(--green)' }}>{message}</p>}
        {error && <p role="alert" className="m-0 whitespace-pre-wrap text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
      </CardContent>
    </Card>
  )
}
