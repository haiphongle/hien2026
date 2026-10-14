import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchAPI, resolveMediaUrl } from '../../api/client'
import { usePolling } from '../../api/usePolling'
import type { Character, Orientation, Project } from '../../types'
import { Button } from '../ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card'
import { PresetVoiceLibrary } from './postproduction/PresetVoiceLibrary'
import type { SpeechVoice } from './postproduction/speechTypes'
import { MAX_STORY_SCENES, emotions, gestures, rememberDirection, defaultVisualLocks, normalizeVisualLocks, emptyAnalysis, normalizeAnalysis, type StoryAsset, type CharacterRole, type StoryAnalysis, type VisualLocks, type ShotDirection, type Motion, type StickmanScene as Scene, type StoryCharacter } from './stickmanOptions'
import { StoryStylePicker, type StoryPreset } from './StoryStylePicker'
import { StickmanModelRecovery, type ModelRecovery, type VideoModelOption } from './StickmanModelRecovery'
import { StoryReferences, StoryWorldReferences } from './StoryReferences'
import { StorySchedule, type SavedStory } from './StorySchedule'
import { StoryVisualSettings } from './StoryVisualSettings'
import { StoryPromptBoard } from './StoryPromptBoard'
import { StickmanSceneCard } from './StickmanSceneCard'
import { StickmanPreview } from './StickmanPreview'
import { styleChange } from './storyStyleLibrary'

interface Music { filename: string; url: string }
interface Job { id: string; status: string; stage: string; progress: number; error?: string; result?: { url?: string; preview_url?: string }; video_model?: string; model_notice?: string; model_recovery?: ModelRecovery }
interface Draft {
  source: string; title: string; scenes: Scene[]; sceneCount: number | null; style: string; orientation: Orientation
  language: 'vi' | 'en'; voice: string; character: string; music: string; musicGain: number; videoId: string
  motion: Motion; dialogue: boolean; secondVoice: string; secondCharacter: string; speed: number; speechCheck: boolean
  videoModel: string
  styleProfile: string; previewFirst: boolean; designs: StoryCharacter[]
  sceneVersions: ShotDirection[][]
  visualLocks: VisualLocks
  analysis: StoryAnalysis; plannedSource: string
  storyAssets: StoryAsset[]
}
interface Template { id: string; name: string; settings: Record<string, unknown> }
interface Estimate { reference_images: number; scene_images: number; flow_clips: number; local_clips: number; reused_scenes: number }
interface Quality { scenes: { scene: number; warning?: string; voice_seconds: number; picture_seconds: number; caption_cps: number }[]; mouth_alignment: string; character_check: string; vision?: { scene: number; score?: number; warning?: string }[]; words?: { scene: number; recognized: string; needs_review: boolean }[] }
const control = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }
const blank: Draft = { source: '', title: '', scenes: [], sceneCount: null, style: 'stickman_chalk', orientation: 'VERTICAL', language: 'vi', voice: '', character: '', music: '', musicGain: .25, videoId: '', motion: 'ai', dialogue: false, secondVoice: '', secondCharacter: '', speed: 1, speechCheck: false, videoModel: '', styleProfile: '', previewFirst: true, designs: [], storyAssets: [], sceneVersions: [], visualLocks: defaultVisualLocks, analysis: emptyAnalysis, plannedSource: '' }

function productionPayload(draft: Draft) {
  return JSON.stringify({ title: draft.title.trim(), scenes: draft.scenes, characters: draft.designs, story_assets: draft.storyAssets, analysis: draft.analysis, style: draft.style, orientation: draft.orientation,
    language: draft.language, engine: draft.language === 'vi' ? 'vieneu' : 'kokoro', preset_id: draft.voice, character_id: draft.character || null,
    music_filename: draft.music || null, music_gain: draft.musicGain, motion: draft.motion, dialogue: draft.dialogue,
    second_preset_id: draft.dialogue ? draft.secondVoice || null : null, second_character_id: draft.secondCharacter || null,
    speed: draft.speed, speech_check: draft.speechCheck, video_model: draft.motion === 'ai' ? draft.videoModel || null : null,
    style_profile_id: draft.styleProfile || null, preview_first: draft.previewFirst, preview_all: true, visual_locks: draft.visualLocks })
}

function load(key: string): Draft {
  try {
    const value = JSON.parse(localStorage.getItem(key) || 'null') as Draft | null
    if (value && ['source', 'title', 'style', 'orientation', 'language', 'voice', 'character', 'music', 'videoId'].every(k => typeof value[k as keyof Draft] === 'string')
      && Array.isArray(value.scenes) && value.scenes.length <= MAX_STORY_SCENES && value.scenes.every(s => s && typeof s.action === 'string' && typeof s.narration === 'string')) return {
        ...blank, ...value, speed: Number.isFinite(value.speed) ? Math.max(.8, Math.min(1.3, value.speed)) : 1,
        sceneCount: typeof value.sceneCount === 'number' && Number.isFinite(value.sceneCount) ? Math.max(1, Math.min(MAX_STORY_SCENES, Math.round(value.sceneCount))) : null,
        musicGain: Number.isFinite(value.musicGain) ? Math.max(0, Math.min(1, value.musicGain)) : .25,
        motion: ['zoom', 'pan', 'still', 'ai', 'talk'].includes(value.motion) ? value.motion : 'zoom',
        dialogue: Boolean(value.dialogue), speechCheck: Boolean(value.speechCheck),
        videoModel: typeof value.videoModel === 'string' ? value.videoModel : '',
        visualLocks: normalizeVisualLocks(value.visualLocks),
        storyAssets: Array.isArray(value.storyAssets) ? value.storyAssets : [],
        analysis: normalizeAnalysis(value.analysis), plannedSource: typeof value.plannedSource === 'string' ? value.plannedSource : value.source,
        styleProfile: typeof value.styleProfile === 'string' ? value.styleProfile : '', previewFirst: value.previewFirst === undefined ? true : Boolean(value.previewFirst), designs: Array.isArray(value.designs) ? value.designs : [],
        sceneVersions: value.scenes.map((_, i) => Array.isArray(value.sceneVersions?.[i]) ? value.sceneVersions[i].filter(v => v && typeof v.action === 'string' && v.action.length <= 1000 && (!v.motion_beats || Array.isArray(v.motion_beats) && v.motion_beats.length === 3 && v.motion_beats.every(s => typeof s === 'string')) && (!v.camera || typeof v.camera === 'string')).slice(-5) : []),
        secondVoice: typeof value.secondVoice === 'string' ? value.secondVoice : '', secondCharacter: typeof value.secondCharacter === 'string' ? value.secondCharacter : '',
        scenes: value.scenes.map(s => ({ ...s, speaker: value.dialogue && s.speaker === 'B' ? 'B' : 'A', emotion: s.emotion in emotions ? s.emotion : 'thinking', gesture: s.gesture in gestures ? s.gesture : 'explain' })),
      }
  } catch { /* Optional browser draft; the server keeps created stories. */ }
  return blank
}

export default function StickmanStudio({ project, characters, onOpen }: {
  project: Project; characters: Character[]; onOpen: (videoId: string, target?: 'pipeline' | 'postproduction') => void
}) {
  const key = `flowkit.stickman.${project.id}`
  const [draft, setDraft] = useState(() => load(key))
  const [presets, setPresets] = useState<StoryPreset[]>([])
  const [uploadedCharacters, setUploadedCharacters] = useState<Character[]>([])
  const referenceCharacters = [...characters, ...uploadedCharacters.filter(c => !characters.some(existing => existing.id === c.id))].filter(c => c.media_id && c.entity_type === 'character')
  const [videoModels, setVideoModels] = useState<VideoModelOption[]>([])
  const [defaultModels, setDefaultModels] = useState<Partial<Record<Orientation, string>>>({})
  const [music, setMusic] = useState<Music[]>([])
  const [job, setJob] = useState<Job | null>(null)
  const [planning, setPlanning] = useState(false)
  const [improving, setImproving] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [referenceBusy, setReferenceBusy] = useState(false)
  const [styleRevision, setStyleRevision] = useState(0)
  const [voiceReady, setVoiceReady] = useState(false)
  const [error, setError] = useState('')
  const [templates, setTemplates] = useState<Template[]>([]), [templateName, setTemplateName] = useState(''), [savingTemplate, setSavingTemplate] = useState(false)
  const [voiceActor, setVoiceActor] = useState<'A' | 'B'>('A'), [updating, setUpdating] = useState(false)
  const [estimateState, setEstimate] = useState<{ key: string; value?: Estimate; error?: string } | null>(null), [quality, setQuality] = useState<Quality | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const upload = useRef<HTMLInputElement>(null)
  const planRequest = useRef<AbortController | null>(null)
  const suggestionRequest = useRef<AbortController | null>(null)
  const running = Boolean(draft.videoId && (!job || ['QUEUED', 'PROCESSING'].includes(job.status)))
  const busy = running || planning || improving || submitting || referenceBusy
  const staleStory = draft.plannedSource !== draft.source
  const engine = draft.language === 'vi' ? 'vieneu' : 'kokoro'
  const patch = (changes: Partial<Draft>) => setDraft(value => ({ ...value, ...changes }))
  function selectReference(role: CharacterRole, id: string) {
    setDraft(value => ({ ...value, ...(role === 'A' ? { character: id } : role === 'B' ? { secondCharacter: id } : {}),
      designs: value.designs.map(c => c.role === role ? { ...c, reference_id: id || null, reference_status: id ? 'unknown' : 'unselected', reference_note: '' } : c) }))
  }

  useEffect(() => {
    try { localStorage.setItem(key, JSON.stringify(draft)) } catch { /* Optional draft. */ }
  }, [key, draft])
  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchAPI<{ presets: StoryPreset[]; video_models: VideoModelOption[]; default_video_models: Record<Orientation, string> }>(`/api/projects/${project.id}/stickman/options`, { signal: controller.signal }),
      fetchAPI<Music[]>(`/api/music/files/${project.id}`, { signal: controller.signal }),
      fetchAPI<Template[]>(`/api/projects/${project.id}/stickman/templates`, { signal: controller.signal }),
    ]).then(([options, tracks, saved]) => { setPresets(options.presets); setVideoModels(options.video_models || []); setDefaultModels(options.default_video_models || {}); setMusic(tracks); setTemplates(saved) }).catch(cause => { if (!controller.signal.aborted) setError(String(cause)) })
    return () => { controller.abort(); planRequest.current?.abort(); suggestionRequest.current?.abort(); audio.current?.pause() }
  }, [project.id])
  usePolling<Job | null>(running ? `/api/videos/${draft.videoId}/stickman/job` : null, 2500, result => {
    setJob(result); setError('')
  }, cause => {
    const message = String(cause)
    setError(message)
    if (/API (404|409):/.test(message)) setJob({ id: '', status: 'MISSING', stage: 'Mở Hậu kỳ để kiểm tra video', progress: 0 })
  })

  const chooseVoice = useCallback((voices: SpeechVoice[]) => {
    setDraft(value => ({ ...value, voice: voices.some(v => v.id === value.voice) ? value.voice : (voices.find(v => v.name === 'Thiện Minh' || v.name === 'Heart')?.id || voices[0]?.id || ''), secondVoice: voices.some(v => v.id === value.secondVoice) ? value.secondVoice : voices.find(v => v.id !== value.voice)?.id || voices[0]?.id || '' }))
  }, [])
  const play = useCallback((next: HTMLAudioElement) => {
    if (audio.current !== next) audio.current?.pause()
    audio.current = next
  }, [])

  async function storyboard(automatic = false) {
    setPlanning(true); setError('')
    const controller = new AbortController()
    planRequest.current = controller
    try {
      const result = await fetchAPI<{ title: string; scenes: Scene[]; characters: StoryCharacter[]; story_assets: StoryAsset[]; analysis: StoryAnalysis }>(`/api/projects/${project.id}/stickman/plan`, {
        method: 'POST', signal: controller.signal,
        body: JSON.stringify({ source: draft.source, scene_count: draft.sceneCount, language: draft.language, dialogue: draft.dialogue,
          style: draft.style, orientation: draft.orientation, style_profile_id: draft.styleProfile || null,
          character_id: draft.character || null, second_character_id: draft.secondCharacter || null, characters: staleStory ? [] : draft.designs,
          story_assets: staleStory ? [] : draft.storyAssets,
          visual_locks: draft.visualLocks, auto_timing: draft.motion === 'ai' }),
      })
      if (controller.signal.aborted) return
      const changes = { title: result.title, scenes: result.scenes, designs: result.characters, storyAssets: result.story_assets || [], analysis: normalizeAnalysis(result.analysis), plannedSource: draft.source, sceneVersions: [] }
      const next = { ...draft, ...changes }
      patch(changes)
      if (automatic && !controller.signal.aborted) await create(next, true)
    } catch (cause) { if (!controller.signal.aborted) setError(String(cause)) }
    finally { setPlanning(false) }
  }
  async function create(next = draft, newVideo = false) {
    setSubmitting(true); setError(''); audio.current?.pause()
    try {
      const body = productionPayload(next)
      if (updating && draft.videoId && !newVideo) {
        setJob(await fetchAPI<Job>(`/api/videos/${draft.videoId}/stickman/plan`, { method: 'PATCH', body }))
      } else {
        const result = await fetchAPI<{ video_id: string; job: Job }>(`/api/projects/${project.id}/stickman/videos`, { method: 'POST', body })
        patch({ videoId: result.video_id }); setJob(result.job); setUpdating(false)
      }
      setQuality(null)
    } catch (cause) { setError(String(cause)) }
    finally { setSubmitting(false) }
  }
  async function jobAction(action: 'retry' | 'cancel', videoModel?: string, preview?: { regenerate_preview?: boolean; preview_media_id?: string; preview_scene_id?: string }) {
    setSubmitting(true); setError('')
    try {
      setJob(await fetchAPI<Job>(`/api/videos/${draft.videoId}/stickman/${action}${action === 'cancel' ? `/${job!.id}` : ''}`, {
        method: 'POST', ...(action === 'retry' ? { body: JSON.stringify({ job_id: job?.id, approve_preview: Boolean(job?.result?.preview_url) && !preview?.regenerate_preview, ...preview, ...(videoModel ? { video_model: videoModel } : {}) }) } : {}),
      }))
    } catch (cause) { setError(String(cause)) }
    finally { setSubmitting(false) }
  }
  async function uploadMusic(file?: File) {
    if (!file) return
    setUploading(true); setError('')
    try {
      const item = await fetchAPI<Music>(`/api/music/files/${project.id}/upload?filename=${encodeURIComponent(file.name)}`, { method: 'POST', body: file })
      setMusic(tracks => [item, ...tracks]); patch({ music: item.filename })
    } catch (cause) { setError(String(cause)) }
    finally { setUploading(false); if (upload.current) upload.current.value = '' }
  }
  function editScene(index: number, value: Partial<Scene>, remember = false) {
    setDraft(current => ({ ...current, scenes: current.scenes.map((s, i) => i === index ? { ...s, ...value } : s),
      sceneVersions: remember ? current.scenes.map((s, i) => i === index ? rememberDirection(current.sceneVersions[i] || [], s) : current.sceneVersions[i] || []) : current.sceneVersions }))
  }
  async function suggestScene(index: number): Promise<Scene> {
    if (suggestionRequest.current) throw new Error('AI đang cải thiện một cảnh. Đợi hoàn tất rồi thử lại.')
    const controller = new AbortController()
    suggestionRequest.current = controller
    setImproving(true)
    try {
      const result = await fetchAPI<{ scene: Scene }>(`/api/projects/${project.id}/stickman/suggest-scene`, { method: 'POST', signal: controller.signal,
        body: JSON.stringify({ source: draft.scenes[index].narration, scene: draft.scenes[index], style: draft.style, orientation: draft.orientation,
          language: draft.language, dialogue: draft.dialogue, style_profile_id: draft.styleProfile || null,
          character_id: draft.character || null, second_character_id: draft.secondCharacter || null,
          characters: draft.designs, story_assets: draft.storyAssets, analysis: draft.analysis, visual_locks: draft.visualLocks }) })
      return result.scene
    } finally { suggestionRequest.current = null; setImproving(false) }
  }
  const suggestionContext = JSON.stringify([project.id, draft.style, draft.styleProfile, draft.orientation, draft.character, draft.secondCharacter, draft.dialogue, draft.language, draft.designs, draft.storyAssets, draft.visualLocks, draft.analysis])
  const promptRequest = JSON.stringify({ source: draft.source.trim() || draft.scenes[0]?.narration || 'Kịch bản đã duyệt', scenes: draft.scenes,
    characters: draft.designs, story_assets: draft.storyAssets, style: draft.style, orientation: draft.orientation, language: draft.language, dialogue: draft.dialogue,
    character_id: draft.character || null, second_character_id: draft.secondCharacter || null, style_profile_id: draft.styleProfile || null,
    analysis: draft.analysis, visual_locks: draft.visualLocks, motion: draft.motion })
  const hasVisualLocks = JSON.stringify(draft.visualLocks) !== JSON.stringify(defaultVisualLocks)
  const selectedStyle = presets.find(p => p.id === draft.style)
  const selectedModel = videoModels.find(m => m.id === (draft.videoModel || defaultModels[draft.orientation]))
  const modelReady = draft.motion !== 'ai' || Boolean(selectedModel && (!selectedModel.orientations || selectedModel.orientations.includes(draft.orientation)))
  const compatibleMotion = Boolean(selectedStyle && (draft.motion !== 'talk' || selectedStyle.local_actor && !draft.styleProfile && !hasVisualLocks && !draft.designs.some(c => !['A', 'B'].includes(c.role)) && !draft.scenes.some(s => s.extras || s.assets?.length || s.cast?.length === 0)))
  const invalidScene = draft.scenes.findIndex(s => s.narration.trim().length < 3 || s.action.trim().length < 3 || s.motion_beats?.length && (s.motion_beats.length !== 3 || s.motion_beats.some(b => b.trim().length < 3)))
  const invalidDesign = draft.designs.find(c => !c.name.trim() || c.description.trim().length < 3)
  const ready = Boolean(!staleStory && compatibleMotion && modelReady && voiceReady && draft.voice && (!draft.dialogue || draft.secondVoice) && draft.title.trim().length >= 3 && draft.scenes.length && invalidScene === -1 && !invalidDesign)
  const payload = productionPayload(draft)
  const resourceKey = JSON.stringify([styleRevision, referenceCharacters.map(c => [c.id, c.name, c.media_id])])
  const estimateKey = payload+String(updating)+draft.videoId+resourceKey
  const estimate = estimateState?.key === estimateKey ? estimateState.value : null
  const estimateError = estimateState?.key === estimateKey ? estimateState.error : ''
  useEffect(() => {
    if (!ready || busy) return
    const controller = new AbortController()
    const timer = setTimeout(() => {
      fetchAPI<Estimate>(`/api/projects/${project.id}/stickman/estimate${updating && draft.videoId ? `?video_id=${draft.videoId}` : ''}`, { method: 'POST', signal: controller.signal, body: payload })
        .then(value => setEstimate({ key: estimateKey, value })).catch(cause => { if (!controller.signal.aborted) setEstimate({ key: estimateKey, error: String(cause) }) })
    }, 250)
    return () => { clearTimeout(timer); controller.abort() }
  }, [ready, busy, payload, estimateKey, project.id, updating, draft.videoId])
  useEffect(() => {
    if (job?.status !== 'COMPLETED' || !draft.videoId) return
    const controller = new AbortController()
    fetchAPI<Quality | null>(`/api/videos/${draft.videoId}/stickman/quality`, { signal: controller.signal }).then(setQuality).catch(() => {})
    return () => controller.abort()
  }, [job?.status, draft.videoId])
  async function saveTemplate() {
    setSavingTemplate(true); setError('')
    try {
      const result = await fetchAPI<Template>(`/api/projects/${project.id}/stickman/templates`, { method: 'POST', body: JSON.stringify({ name: templateName, settings: JSON.parse(payload) }) })
      setTemplates(items => [...items.filter(t => t.id !== result.id), result]); setTemplateName('')
    } catch (cause) { setError(String(cause)) } finally { setSavingTemplate(false) }
  }
  function applySettings(s: Record<string, unknown>) {
    patch({ style: s.style as string, orientation: s.orientation as Orientation, language: s.language as Draft['language'],
      voice: s.preset_id as string, secondVoice: (s.second_preset_id || '') as string, character: (s.character_id || '') as string,
      secondCharacter: (s.second_character_id || '') as string, music: (s.music_filename || '') as string, musicGain: s.music_gain as number,
      motion: s.motion as Motion, dialogue: s.dialogue as boolean, speed: s.speed as number, speechCheck: Boolean(s.speech_check),
      videoModel: (s.video_model || '') as string,
      styleProfile: (s.style_profile_id || '') as string, previewFirst: Boolean(s.preview_first), designs: (s.characters || []) as StoryCharacter[],
      storyAssets: (s.story_assets || []) as StoryAsset[],
      visualLocks: normalizeVisualLocks(s.visual_locks),
      scenes: draft.scenes.map(scene => ({ ...scene, speaker: s.dialogue ? scene.speaker : 'A' })) })
    setVoiceActor('A')
  }

  function applyTemplate(id: string) {
    const settings = templates.find(t => t.id === id)?.settings
    if (settings) applySettings(settings)
  }
  function loadStory(story: SavedStory) {
    applySettings(story.settings)
    patch({ source: story.source, plannedSource: story.source, title: story.settings.title as string,
      scenes: story.settings.scenes as Scene[], analysis: normalizeAnalysis(story.settings.analysis),
      sceneVersions: [], videoId: story.video_id || '', sceneCount: null })
    setJob(null); setQuality(null); setUpdating(false); setError('')
  }

  return <div className="space-y-5">
    <Card>
      <CardHeader><CardTitle>1. Phong cách</CardTitle><CardDescription>Chọn mẫu và khung hình trước, rồi nhập nội dung để AI thiết kế nhân vật và cảnh theo phong cách này.</CardDescription></CardHeader>
      <CardContent className="space-y-4">
        {!!templates.length && <label className="block text-xs">Dùng mẫu đã lưu<select aria-label="Mẫu sản xuất người que" disabled={busy} value="" onChange={e => applyTemplate(e.target.value)} className="ml-2 rounded p-2" style={control}><option value="">Chọn mẫu…</option>{templates.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></label>}
        <StoryStylePicker key={project.id} projectId={project.id} presets={presets} value={draft.style} profileId={draft.styleProfile}
          characters={referenceCharacters} hasCharacters={Boolean(draft.character || draft.secondCharacter || draft.designs.length)}
          disabled={running || planning || improving || submitting || uploading} onBusy={setReferenceBusy}
          onCharacter={character => setUploadedCharacters(items => [...items.filter(c => c.id !== character.id), character])}
          onStyleSaved={() => setStyleRevision(value => value+1)}
          onSelect={(choice, policy) => patch({ ...styleChange(choice, policy, draft), storyAssets: policy === 'new' ? draft.storyAssets.map(a => ({ ...a, reference_id: null })) : draft.storyAssets, visualLocks: { ...defaultVisualLocks, context: draft.visualLocks.context } })} />
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs">Khung hình
            <select aria-label="Khung hình người que" disabled={busy} value={draft.orientation} onChange={e => patch({ orientation: e.target.value as Orientation })} className="block w-full rounded-md p-2" style={control}><option value="VERTICAL">Dọc · 9:16</option><option value="HORIZONTAL">Ngang · 16:9</option></select>
          </label>
        </div>
        <StoryVisualSettings value={draft.visualLocks} disabled={busy || draft.motion === 'talk'} onChange={visualLocks => patch({ visualLocks })} />
      </CardContent>
    </Card>
    <Card>
      <CardHeader><CardTitle>2. Nội dung / SRT</CardTitle><CardDescription>Dán lời bình, SRT hoặc nhập chủ đề. Có thể thêm phần “Prompt:” hoặc “Gợi ý hình ảnh:” sau SRT, cách một dòng trống. AI gợi ý từng cảnh để bạn duyệt.</CardDescription></CardHeader>
      <CardContent className="space-y-3">
        <textarea aria-label="Nội dung video người que" disabled={busy} rows={6} maxLength={12000} value={draft.source} onChange={e => patch({ source: e.target.value })} placeholder="Ví dụ: Đừng mang theo mọi áp lực. Biết buông bỏ để sống nhẹ nhàng hơn…" className="w-full rounded-md p-3 text-sm" style={control} />
        {draft.source.trim() && <p className="text-xs" style={{ color: 'var(--muted)' }}>{draft.source.includes('-->') ? `Đã nhận SRT · ${draft.source.match(/\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->/g)?.length || 0} câu. Giữ nguyên lời và thứ tự; thời lượng mới theo giọng đọc.` : 'Nội dung / chủ đề · AI đề xuất lời đọc và các cảnh minh họa để bạn duyệt.'}</p>}
        <div className="flex flex-wrap items-end gap-3">
          <label className="text-xs">Số cảnh<select aria-label="Cách chia cảnh" disabled={busy} value={draft.sceneCount === null ? 'auto' : 'custom'} onChange={e => patch({ sceneCount: e.target.value === 'auto' ? null : draft.scenes.length || 4 })} className="ml-2 rounded p-2" style={control}><option value="auto">Tự động theo nội dung</option><option value="custom">Tự chọn</option></select></label>
          {draft.sceneCount !== null && <input aria-label="Số cảnh người que" disabled={busy} type="number" min={1} max={MAX_STORY_SCENES} value={draft.sceneCount} onChange={e => patch({ sceneCount: Math.max(1, Math.min(MAX_STORY_SCENES, Math.round(Number(e.target.value)) || 1)) })} className="w-16 rounded p-2" style={control} />}
          {draft.sceneCount !== null && draft.motion === 'ai' && <p className="text-xs" style={{ color: 'var(--muted)' }}>Số cảnh theo ý chính; lời dài sẽ tự tách thêm cảnh ngắn để vừa clip.</p>}
          <label className="text-xs">Lời đọc<select aria-label="Ngôn ngữ người que" disabled={busy} value={draft.language} onChange={e => { patch({ language: e.target.value as Draft['language'], voice: '', secondVoice: '' }); setVoiceReady(false) }} className="ml-2 rounded p-2" style={control}><option value="vi">Tiếng Việt</option><option value="en">Tiếng Anh</option></select></label>
          <Button type="button" disabled={busy || draft.source.trim().length < 3} onClick={() => void storyboard()}>{planning ? 'AI đang lên kịch bản…' : 'AI phân tích và lên kịch bản'}</Button>
          <Button type="button" disabled={busy || uploading || !voiceReady || !draft.voice || !compatibleMotion || draft.source.trim().length < 3 || draft.dialogue && !draft.secondVoice} onClick={() => void storyboard(true)}>Tạo video tự động từ SRT / nội dung</Button>
        </div>
        {staleStory && !!draft.scenes.length && <p role="status" className="rounded-lg border p-3 text-xs" style={{ borderColor: 'var(--accent)' }}>Nội dung đã thay đổi. Bấm “AI phân tích và lên kịch bản” để cập nhật nhân vật và các cảnh trước khi tạo video. Hồ sơ cũ sẽ không được dùng để phân tích nội dung mới.</p>}
        {!staleStory && draft.analysis.summary && <section aria-label="AI hiểu câu chuyện" className="space-y-2 rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
          <h4 className="text-sm font-semibold">AI hiểu câu chuyện</h4><p className="text-sm leading-relaxed">{draft.analysis.summary}</p>
          <p className="text-xs" style={{ color: 'var(--muted)' }}>{[draft.analysis.environment, draft.analysis.era].filter(Boolean).join(' · ')}</p>
          <details className="text-xs"><summary className="cursor-pointer">Chỉnh phân tích và tính liên tục</summary><div className="mt-3 space-y-2">{([
            ['summary', 'Tóm tắt câu chuyện', 700], ['environment', 'Bối cảnh chung', 400], ['era', 'Thời đại', 120], ['continuity', 'Quy tắc liên tục', 400],
          ] as const).map(([field, label, limit]) => <label key={field} className="block">{label}<textarea aria-label={label} disabled={busy} rows={2} maxLength={limit} value={draft.analysis[field]} onChange={e => patch({ analysis: { ...draft.analysis, [field]: e.target.value } })} className="mt-1 w-full rounded p-2" style={control} /></label>)}</div></details>
        </section>}
        <p className="text-xs" style={{ color: 'var(--muted)' }}>AI đọc toàn bộ câu chuyện, tự phân nhân vật, bối cảnh, đạo cụ và các cảnh. SRT dài tự chia thêm cảnh ngắn, tối đa {MAX_STORY_SCENES} cảnh; giữ nguyên lời và thứ tự câu, bỏ dấu [cite: …]. Giọng kể không bắt buộc xuất hiện trong hình. Mốc thời gian mới theo tiếng đọc thực tế.</p>
        <p className="text-xs" style={{ color: 'var(--muted)' }}>Nút tự động lên kịch bản rồi tạo ảnh, clip, giọng, phụ đề và nhạc đã chọn. Google Flow tính tín dụng cho các lượt tạo; không tự chạy lại clip lỗi.</p>

      </CardContent>
    </Card>
    <Card>
      <CardHeader>
        <CardTitle>3. Nhân vật và chuyển động</CardTitle>
        <CardDescription>AI tự phân vai, chọn bối cảnh và đạo cụ cần đồng nhất. Ảnh từng cảnh là đầu vào tạo chuyển động.</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <details className="rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}><summary className="cursor-pointer text-sm font-medium">Nhân vật · {draft.designs.length || 'AI tự xác định'} · Chỉnh / tải ảnh riêng</summary><div className="mt-3">
        <StoryReferences projectId={project.id} characters={referenceCharacters} designs={draft.designs} selected={{ ...Object.fromEntries(draft.designs.map(c => [c.role, c.reference_id || ''])), A: draft.character || draft.designs.find(c => c.role === 'A')?.reference_id || '', B: draft.secondCharacter || draft.designs.find(c => c.role === 'B')?.reference_id || '' }}
          disabled={busy || draft.motion === 'talk'} onBusy={setReferenceBusy}
          onSelect={selectReference}
          onDesign={design => patch({ designs: [...draft.designs.filter(c => c.role !== design.role), design].sort((a, b) => a.role.localeCompare(b.role)) })}
          onCharacter={(character, role) => { setUploadedCharacters(items => [...items.filter(c => c.id !== character.id), character]); selectReference(role, character.id) }} />
        </div></details>
        <StoryWorldReferences assets={draft.storyAssets} library={characters} disabled={busy} onChange={storyAssets => patch({ storyAssets })} />
        <label className="block text-xs">Cách tạo chuyển động<select aria-label="Chuyển động người que" disabled={busy} value={draft.motion} onChange={e => patch({ motion: e.target.value as Motion })} className="mt-1 w-full rounded p-2" style={control}><optgroup label="Ảnh chuyển động"><option value="zoom">Zoom nhẹ</option><option value="pan">Pan từ trái sang phải</option><option value="still">Giữ ảnh tĩnh</option></optgroup><option value="ai">Hoạt hình AI · Google Flow</option><option value="talk" disabled={!selectedStyle?.local_actor || !!draft.styleProfile || hasVisualLocks}>Nhân vật vector nói chuyện · thử nghiệm</option></select></label>
        {draft.motion === 'ai' && <div className="space-y-2"><label className="block text-xs">Model hoạt hình<select aria-label="Model hoạt hình" disabled={busy} value={draft.videoModel} onChange={e => patch({ videoModel: e.target.value })} className="mt-1 w-full rounded p-2" style={control}><option value="">Theo Cài đặt · {videoModels.find(m => m.id === defaultModels[draft.orientation])?.name || 'Model mặc định'}</option>{draft.videoModel && !selectedModel && <option value={draft.videoModel}>Model đã lưu không còn trong danh sách</option>}{videoModels.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
          {selectedModel?.duration_seconds && <p className="rounded-lg border p-3 text-xs leading-relaxed" style={{ borderColor: 'var(--border)' }}>Luồng hiện tại: clip {selectedModel.duration_seconds}s · ảnh đầu → video · dọc/ngang · camera qua prompt. Ảnh cuối chưa được dùng trong luồng này.</p>}
          <p className="text-xs" style={{ color: 'var(--muted)' }}>Áp dụng cho clip mới hoặc còn thiếu. Quyền truy cập chưa được xác nhận; Google Flow quyết định khi tạo. Chưa có dữ liệu tín dụng chính xác cho từng model.</p>
          {!modelReady && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>Chọn một model có trong danh sách và hỗ trợ khung hình hiện tại.</p>}
        </div>}
        {selectedStyle && !selectedStyle.local_actor && <p className="text-xs" style={{ color: 'var(--muted)' }}>Phong cách này dùng ảnh tham chiếu hoặc Hoạt hình AI. Mẫu vector thử nghiệm không dựng được trang phục, 3D và anime.</p>}
        <p className="text-xs" style={{ color: 'var(--muted)' }}>{draft.motion === 'talk' ? 'Nhân vật vector có cử chỉ và miệng theo âm lượng WAV thật. Không dùng ảnh tham chiếu và chưa căn khẩu hình theo âm vị, kể cả tiếng Việt.' : draft.motion === 'ai' ? 'AI thiết kế ba nhịp chuyển động cho mỗi clip. Lời dài được chia thành cảnh ngắn khi lên kịch bản; tốc độ hình căn theo WAV thật để giữ trọn hành động.' : 'Zoom/pan chỉ di chuyển camera trên ảnh; nhân vật không tự hành động. Ảnh tham chiếu quyết định hình dáng nhân vật.'}</p>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" aria-label="Hai nhân vật đối thoại" disabled={busy} checked={draft.dialogue} onChange={e => { patch({ dialogue: e.target.checked, scenes: draft.scenes.map(s => ({ ...s, speaker: e.target.checked ? s.speaker : 'A' })) }); setVoiceActor('A') }} />Hai nhân vật đối thoại · A và B</label>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" aria-label="Xem ảnh trước khi tạo chuyển động" disabled={busy || draft.motion === 'talk'} checked={draft.previewFirst} onChange={e => patch({ previewFirst: e.target.checked })} />Xem toàn bộ ảnh trước khi tạo chuyển động</label>
      </CardContent>
    </Card>
    {!!draft.scenes.length && <Card>
      <CardHeader><CardTitle>4. Cảnh và prompt</CardTitle><CardDescription>Duyệt hành động, bố trí, ánh sáng và chuyển động của từng cảnh. Prompt tự cập nhật theo lựa chọn.</CardDescription></CardHeader>
      <CardContent>
        {!!draft.scenes.length && <div className="space-y-3 border-t pt-4" style={{ borderColor: 'var(--border)' }}>
          <p className="text-sm font-medium">Kịch bản · {draft.scenes.length} cảnh · Có thể sửa trước khi tạo video</p>
          <label className="block text-xs">Tên video<input aria-label="Tên video người que" disabled={busy} maxLength={120} value={draft.title} onChange={e => patch({ title: e.target.value })} className="mt-1 block w-full rounded p-2 text-sm" style={control} /></label>
          {draft.scenes.map((scene, i) => <StickmanSceneCard key={i} scene={scene} index={i} disabled={busy || staleStory} dialogue={draft.dialogue} motion={draft.motion}
            context={suggestionContext} characters={draft.designs} assets={draft.storyAssets} versions={draft.sceneVersions[i] || []} canDelete={draft.scenes.length > 1}
            onChange={(value, remember) => editScene(i, value, remember)} onSuggest={() => suggestScene(i)}
            onDelete={() => patch({ scenes: draft.scenes.filter((_, index) => i !== index), sceneVersions: draft.scenes.map((_, index) => draft.sceneVersions[index] || []).filter((_, index) => i !== index) })} />)}
          <StoryPromptBoard projectId={project.id} request={promptRequest} title={draft.title} disabled={busy || staleStory || invalidScene !== -1 || !!invalidDesign}
            contextKey={resourceKey} />
        </div>}
      </CardContent>
    </Card>}
    <Card>
      <CardHeader><CardTitle>5. Giọng đọc và nhạc</CardTitle><CardDescription>Dùng thư viện giọng và nhạc hiện có. Demo được lưu sẵn, không tạo lại mỗi lần mở.</CardDescription></CardHeader>
      <CardContent className="space-y-4">
        {draft.dialogue && <div className="flex gap-2"><Button type="button" size="sm" variant={voiceActor === 'A' ? 'default' : 'outline'} onClick={() => setVoiceActor('A')}>Chọn giọng A</Button><Button type="button" size="sm" variant={voiceActor === 'B' ? 'default' : 'outline'} onClick={() => setVoiceActor('B')}>Chọn giọng B</Button></div>}
        <div className="max-h-[360px] overflow-y-auto"><PresetVoiceLibrary key={engine} engine={engine} selected={voiceActor === 'A' ? draft.voice : draft.secondVoice} disabled={busy} onSelect={voice => patch(voiceActor === 'A' ? { voice } : { secondVoice: voice })} onReadyChange={setVoiceReady} onVoicesChange={chooseVoice} onPlay={play} /></div>
        <label className="flex items-center gap-3 text-xs">Tốc độ đọc · {draft.speed.toFixed(2)}×<input aria-label="Tốc độ đọc người que" type="range" min={.8} max={1.3} step={.05} disabled={busy} value={draft.speed} onChange={e => patch({ speed: Number(e.target.value) })} /></label>
        <label className="flex items-center gap-2 text-xs"><input type="checkbox" aria-label="Whisper kiểm tra thiếu từ" disabled={busy} checked={draft.speechCheck} onChange={e => patch({ speechCheck: e.target.checked })} />Whisper nghe lại để kiểm tra thiếu từ · lâu hơn</label>
        <label className="block text-xs">Nhạc nền<select aria-label="Nhạc video người que" disabled={busy || uploading} value={draft.music} onChange={e => patch({ music: e.target.value })} className="mt-1 block w-full rounded p-2 text-sm" style={control}><option value="">Không dùng nhạc</option>{music.map(m => <option key={m.filename} value={m.filename}>{m.filename}</option>)}</select></label>
        <input ref={upload} type="file" accept=".mp3,.wav" className="sr-only" onChange={e => void uploadMusic(e.target.files?.[0])} />
        <Button type="button" variant="outline" size="sm" disabled={busy || uploading} onClick={() => upload.current?.click()}>{uploading ? 'Đang tải nhạc…' : 'Tải nhạc MP3 / WAV'}</Button>
        {draft.music && <><audio controls src={resolveMediaUrl(music.find(m => m.filename === draft.music)?.url)} className="w-full" onPlay={e => play(e.currentTarget)} />
          <label className="flex items-center gap-3 text-xs">Nhạc {Math.round(draft.musicGain*100)}%<input aria-label="Âm lượng nhạc người que" type="range" min={0} max={100} disabled={busy} value={draft.musicGain*100} onChange={e => patch({ musicGain: Number(e.target.value)/100 })} /></label><p className="text-xs" style={{ color: 'var(--muted)' }}>Tự giảm nhạc khi có giọng đọc. Có thể chỉnh lại trong timeline.</p></>}
        <div className="flex flex-wrap gap-2"><input aria-label="Tên mẫu sản xuất" disabled={busy} placeholder="Tên mẫu giọng / bố cục…" value={templateName} onChange={e => setTemplateName(e.target.value)} className="rounded p-2 text-sm" style={control} /><Button type="button" variant="outline" size="sm" disabled={busy || savingTemplate || !ready || templateName.trim().length < 3} onClick={() => void saveTemplate()}>{savingTemplate ? 'Đang lưu mẫu…' : 'Lưu mẫu'}</Button></div>
        {draft.videoId && !running && <label className="flex items-center gap-2 text-sm"><input type="checkbox" aria-label="Chỉ cập nhật phần thay đổi" disabled={busy} checked={updating} onChange={e => setUpdating(e.target.checked)} />Cập nhật video đã tạo · chỉ tạo lại phần thay đổi</label>}
        {updating && <p className="text-xs" style={{ color: 'var(--muted)' }}>Giữ số cảnh; các bản xuất cũ vẫn còn. Nếu đã chỉnh timeline thủ công, tạo video mới hoặc sửa cử chỉ trực tiếp trên đoạn.</p>}
        {ready && !busy && <div className="space-y-2 rounded-lg border p-3 text-sm" style={{ borderColor: 'var(--border)' }}><p>{estimate ? `Cần tạo: ${estimate.reference_images} ảnh tham chiếu + ${estimate.scene_images} ảnh cảnh + ${estimate.flow_clips} clip Google Flow. Dựng ${estimate.local_clips} cảnh trên máy.${estimate.reused_scenes ? ` Dùng lại ảnh của ${estimate.reused_scenes} cảnh.` : ''}` : estimateError || 'Đang kiểm tra số ảnh và clip cần tạo…'}</p><p className="text-xs" style={{ color: 'var(--muted)' }}>{draft.motion === 'talk' ? 'Không dùng tín dụng Google Flow.' : draft.previewFirst ? 'Tạo toàn bộ ảnh để bạn duyệt một lần, rồi mới tạo lời đọc và chuyển động. Số lượt trên tính cho toàn bộ quy trình.' : 'Tạo các cảnh theo kịch bản đã duyệt. Google Flow quyết định số tín dụng.'}</p></div>}
        <Button type="button" disabled={busy || uploading || !ready || !estimate} onClick={() => void create()}>{submitting ? 'Đang gửi yêu cầu…' : updating ? 'Cập nhật phần thay đổi' : 'Tạo video hoàn chỉnh'}</Button>
        {!ready && !busy && <p className="text-xs" style={{ color: 'var(--muted)' }}>Duyệt kịch bản, đặt tên và chọn một giọng đã sẵn sàng để tạo video.</p>}
        {invalidScene !== -1 && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>Cảnh {invalidScene+1}: nhập lời đọc, hành động và đủ ba nhịp nếu đang sửa prompt chuyển động.</p>}
        {invalidDesign && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>Nhân vật {invalidDesign.role}: nhập tên vai và mô tả tạo hình trước khi tạo video.</p>}
      </CardContent>
    </Card>
    <StorySchedule key={project.id} projectId={project.id} source={draft.source} request={payload}
      ready={ready} disabled={busy || uploading} onLoad={loadStory} />
    {(job || draft.videoId) && <Card><CardContent className="space-y-3 pt-5" aria-live="polite">
      <div className="flex items-center justify-between text-sm"><strong>{job?.stage || 'Đang nạp tác vụ…'}</strong><span>{job?.progress || 0}%</span></div>
      <progress aria-label="Tiến trình video người que" max={100} value={job?.progress || 0} className="w-full" />
      {job?.video_model && <p className="text-xs">Model clip: {videoModels.find(m => m.id === job.video_model)?.name || job.video_model}</p>}
      {job?.model_notice && <p role="status" className="rounded border p-3 text-sm" style={{ borderColor: 'var(--border)' }}>{job.model_notice}</p>}
      {job?.error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{job.error}</p>}
      {running && job && <Button type="button" variant="outline" disabled={submitting} onClick={() => void jobAction('cancel')}>Dừng tác vụ</Button>}
      {job && ['FAILED', 'CANCELLED'].includes(job.status) && !job.result?.preview_url && (job.model_recovery
        ? <StickmanModelRecovery key={job.id} recovery={job.model_recovery} disabled={submitting} onRetry={model => void jobAction('retry', model)} />
        : <><Button type="button" disabled={submitting} onClick={() => void jobAction('retry')}>Tiếp tục phần còn thiếu</Button><p className="text-xs" style={{ color: 'var(--muted)' }}>Tiếp tục kịch bản đã lưu của tác vụ này, dùng lại phần đã tạo thành công.</p></>)}
      {!running && job?.result?.preview_url && <StickmanPreview key={job.id} videoId={draft.videoId} jobId={job.id} url={job.result.preview_url} disabled={submitting}
        onApprove={preview_media_id => void jobAction('retry', undefined, { preview_media_id })} onRegenerate={preview_scene_id => void jobAction('retry', undefined, { regenerate_preview: true, preview_scene_id })} />}
      {job?.status === 'COMPLETED' && job.result?.url && <><video controls preload="metadata" src={resolveMediaUrl(job.result?.url)} className="mx-auto max-h-[480px] w-full rounded-xl" />
        <Button type="button" onClick={() => onOpen(draft.videoId, 'postproduction')}>Mở timeline để chỉnh tiếp</Button><p className="text-xs" style={{ color: 'var(--muted)' }}>Các lớp hình, giọng, phụ đề và nhạc đã lưu riêng. Phụ đề chia theo độ dài câu; có thể chỉnh mốc khi cần.</p></>}
      {quality && <details className="text-xs"><summary>Kiểm tra chất lượng · {quality.scenes.length} cảnh</summary><p>Thời lượng hình khớp WAV thật; tiếng nằm trong cảnh. Phụ đề chia theo cụm từ, chưa căn từng từ.</p>{quality.scenes.map(s => <p key={s.scene}>Cảnh {s.scene}: giọng {s.voice_seconds.toFixed(2)}s / hình {s.picture_seconds.toFixed(2)}s · {s.caption_cps} ký tự/s{s.warning && ` · ${s.warning}`}</p>)}{quality.mouth_alignment === 'audio_energy' && <p>Miệng theo âm lượng; cần xem thử tiếng Việt trước khi xuất bản.</p>}{quality.character_check === 'needs_visual_review' && <p>Kiểm tra hình dáng nhân vật bằng Review ở Pipeline trước khi dùng chính thức.</p>}{quality.vision?.map(v => <p key={v.scene}>Cảnh {v.scene}: {v.score ? `${v.score}/10` : 'Chưa kiểm tra hình'}{v.warning && ` · ${v.warning}`}</p>)}{quality.words?.map(w => <p key={w.scene}>Cảnh {w.scene} · Whisper: {w.needs_review ? 'Cần nghe lại' : 'Chưa phát hiện thiếu từ'} · “{w.recognized}”</p>)}{!quality.words && <p>Chưa nghe lại để dò thiếu từ. Bật kiểm tra Whisper nếu cần đối chiếu nội dung tiếng đọc.</p>}</details>}
    </CardContent></Card>}
    {error && <p role="alert" className="rounded border p-3 text-sm" style={{ borderColor: 'var(--red)', color: 'var(--red)' }}>{error}</p>}
  </div>
}
