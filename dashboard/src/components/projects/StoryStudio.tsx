import { useEffect, useRef, useState, type FormEvent } from 'react'
import { fetchAPI } from '../../api/client'
import type { Character, Orientation, Project } from '../../types'
import { useTranslation } from '../../i18n/useTranslation'
import { Button } from '../ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card'
import FlowModelSummary from '../FlowModelSummary'
import PromptSuggestionDialog from './PromptSuggestionDialog'
import CharacterReferenceImage, { type ReferenceImageFeedback } from './CharacterReferenceImage'
import StickmanStudio from './StickmanStudio'

interface Material { id: string; name: string }
interface CreateResult { video_id: string }
interface ReferenceCreateResult { character: Character; request_id: string }
interface DraftScene {
  key: string
  action: string
  character_ids: string[]
  speaker_id: string
  dialogue: string
  image_prompt?: string
  video_prompt?: string
  prompt_context?: string
}

interface ParsedScript {
  scenes: DraftScene[]
  unknownNames: string[]
  truncated: boolean
}

const controlStyle = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }
const newScene = (): DraftScene => ({ key: crypto.randomUUID(), action: '', character_ids: [], speaker_id: '', dialogue: '' })

function normalizedName(value: string): string {
  return value.trim().normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').replace(/\s+/g, ' ').trim()
}

function parseScript(text: string, characters: Character[]): ParsedScript {
  const lines = text.split(/\r?\n/).map(line => line.trim()).filter(Boolean)
  if (lines.length === 0) return { scenes: [], unknownNames: [], truncated: false }
  const marker = /^(?:(?:cảnh|canh|scene|shot)\s*\d*\s*[:.-]?\s*|(?:int\.|ext\.|nội\.|ngoại\.))/i
  const blocks: { heading: string; lines: string[] }[] = []
  let current = { heading: '', lines: [] as string[] }
  for (const rawLine of lines) {
    const line = rawLine.replace(/^(?:#{1,6}\s*|[-*]\s+)/, '').trim()
    if (marker.test(line) && current.lines.length > 0) {
      blocks.push(current)
      current = { heading: line.replace(marker, '').trim(), lines: [] }
    } else if (marker.test(line)) {
      current.heading = line.replace(marker, '').trim()
    } else {
      current.lines.push(line)
    }
  }
  if (current.lines.length > 0 || current.heading) blocks.push(current)

  const known = characters.map(character => ({ character, name: normalizedName(character.name) }))
  const unknownNames = new Set<string>()
  const scenes: DraftScene[] = []
  const actionLabels = new Set(['action', 'setting', 'scene', 'bối cảnh', 'hành động'].map(normalizedName))

  function findCharacter(raw: string): Character | undefined {
    const cleaned = normalizedName(raw.replace(/\([^)]*\)/g, ' '))
    return known.find(item => item.name === cleaned || cleaned.startsWith(`${item.name} `) || item.name.startsWith(`${cleaned} `))?.character
  }

  for (const block of blocks) {
    const actions: string[] = []
    const dialogue: { rawSpeaker: string; text: string }[] = []
    for (const line of block.lines) {
      const match = line.match(/^([^:：]{1,48})[:：]\s*(.+)$/)
      if (match && !actionLabels.has(normalizedName(match[1]))) dialogue.push({ rawSpeaker: match[1].trim(), text: match[2].trim() })
      else actions.push(line.replace(/^(?:action|setting|bối cảnh|hành động)\s*[:：]\s*/i, '').trim())
    }
    const action = actions.join(' ') || block.heading || 'Các nhân vật tương tác trong bối cảnh đã chọn.'
    const dialogueCharacters = dialogue.map(entry => findCharacter(entry.rawSpeaker)?.id).filter((id): id is string => Boolean(id))
    const blockCharacters = [...new Set([
      ...known.filter(item => normalizedName(action).includes(item.name)).map(item => item.character.id),
      ...dialogueCharacters,
    ])]
    const entries = dialogue.length > 0 ? dialogue : [{ rawSpeaker: '', text: '' }]
    for (const entry of entries) {
      const speaker = entry.rawSpeaker ? findCharacter(entry.rawSpeaker) : undefined
      if (entry.rawSpeaker && !speaker) unknownNames.add(entry.rawSpeaker)
      const cast = [...new Set([...blockCharacters, ...(speaker ? [speaker.id] : [])])]
      const words = entry.text ? entry.text.split(/\s+/) : ['']
      for (let i = 0; i < words.length; i += 15) {
        const chunk = entry.text ? words.slice(i, i + 15).join(' ') : ''
        const unknownAction = entry.text && !speaker ? ` ${entry.rawSpeaker} nói: "${chunk}"` : ''
        scenes.push({
          key: crypto.randomUUID(),
          action: `${action}${unknownAction}`.trim(),
          character_ids: cast,
          speaker_id: speaker?.id || '',
          dialogue: speaker ? chunk : '',
        })
      }
    }
  }
  return { scenes: scenes.slice(0, 24), unknownNames: [...unknownNames], truncated: scenes.length > 24 }
}

interface StudioDraft { title: string; orientation: Orientation; material: string; scenes: DraftScene[] }

function readDraft(project: Project): StudioDraft {
  try {
    const draft = JSON.parse(localStorage.getItem(`flowkit.studio.${project.id}`) || 'null')
    if (draft && typeof draft.title === 'string' && typeof draft.material === 'string'
      && ['VERTICAL', 'HORIZONTAL'].includes(draft.orientation)
      && Array.isArray(draft.scenes) && draft.scenes.length > 0 && draft.scenes.length <= 24
      && draft.scenes.every((scene: DraftScene) => typeof scene.key === 'string' && typeof scene.action === 'string'
        && Array.isArray(scene.character_ids) && scene.character_ids.every(id => typeof id === 'string')
        && typeof scene.speaker_id === 'string' && typeof scene.dialogue === 'string'
        && [scene.image_prompt, scene.video_prompt, scene.prompt_context].every(value => value === undefined || typeof value === 'string'))) return draft
  } catch { /* A damaged or unavailable draft must not stop the editor. */ }
  return { title: '', orientation: 'VERTICAL', material: project.material || 'realistic', scenes: [newScene()] }
}

function apiError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error)
  const json = message.replace(/^API \d+: /, '')
  try {
    const detail = JSON.parse(json).detail
    return typeof detail === 'string' ? detail : message
  } catch {
    return message
  }
}

async function readImage(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.onerror = () => reject(new Error('Could not read image'))
    reader.readAsDataURL(file)
  })
}

export default function StoryStudio({ project, characters, onCharacterAdded, onVideoCreated, onRefreshReference, refreshingReferenceId, referenceImageRevision, referenceImageFeedback }: {
  project: Project
  characters: Character[]
  onCharacterAdded: (character: Character) => void
  onVideoCreated: (videoId: string, target?: 'pipeline' | 'postproduction') => void
  onRefreshReference: (character: Character) => void
  refreshingReferenceId: string | null
  referenceImageRevision: number
  referenceImageFeedback: ReferenceImageFeedback
}) {
  const { t } = useTranslation()
  const [studioMode, setStudioMode] = useState<'video' | 'stickman'>('video')
  const [draft] = useState(() => readDraft(project))
  const photoInput = useRef<HTMLInputElement>(null)
  const [photoPreview, setPhotoPreview] = useState('')
  const [materials, setMaterials] = useState<Material[]>([])
  const [referenceMode, setReferenceMode] = useState<'upload' | 'generate'>('upload')
  const [name, setName] = useState('')
  const [photo, setPhoto] = useState<File | null>(null)
  const [voice, setVoice] = useState('')
  const [referencePrompt, setReferencePrompt] = useState('')
  const [uploading, setUploading] = useState(false)
  const [generatingReference, setGeneratingReference] = useState(false)
  const [generatedCharacterId, setGeneratedCharacterId] = useState<string | null>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [title, setTitle] = useState(draft.title)
  const [orientation, setOrientation] = useState<Orientation>(draft.orientation)
  const [material, setMaterial] = useState(draft.material)
  const [scenes, setScenes] = useState<DraftScene[]>(draft.scenes)
  const [scriptText, setScriptText] = useState('')
  const [scriptMessage, setScriptMessage] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [suggesting, setSuggesting] = useState<DraftScene | null>(null)

  function promptContext(scene: DraftScene): string {
    return JSON.stringify([scene.action, scene.character_ids.map(id => [id, characters.find(c => c.id === id)?.media_id]),
      scene.speaker_id, scene.dialogue, orientation, material])
  }
  const promptsNeedReview = scenes.some(scene => (scene.image_prompt || scene.video_prompt) && scene.prompt_context !== promptContext(scene))

  useEffect(() => {
    fetchAPI<Material[]>('/api/materials').then(setMaterials).catch(e => setCreateError(apiError(e)))
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(`flowkit.studio.${project.id}`, JSON.stringify({ title, orientation, material, scenes }))
    } catch { /* Editing can continue when browser storage is unavailable. */ }
  }, [project.id, title, orientation, material, scenes])

  useEffect(() => {
    if (!photo) return
    const url = URL.createObjectURL(photo)
    setPhotoPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [photo])

  // A generated reference is queued in the same worker as scene images. Refresh
  // this one character until Flow returns its UUID, so it becomes selectable in
  // the scene cast without asking the user to reload the page.
  useEffect(() => {
    if (!generatedCharacterId) return
    let cancelled = false
    let timer: number | undefined
    let attempts = 0
    const refresh = async () => {
      try {
        const character = await fetchAPI<Character>(`/api/characters/${generatedCharacterId}`)
        if (cancelled) return
        onCharacterAdded(character)
        if (character.media_id || character.reference_image_url) {
          setGeneratedCharacterId(null)
          setGeneratingReference(false)
          return
        }
      } catch { /* The worker may still be writing the character result. */ }
      attempts += 1
      if (!cancelled && attempts < 48) timer = window.setTimeout(refresh, 5000)
      else if (!cancelled) setGeneratingReference(false)
    }
    timer = window.setTimeout(refresh, 4000)
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [generatedCharacterId, onCharacterAdded])

  async function addCharacter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (uploading || !photo || !name.trim()) return
    if (photo.size > 10 * 1024 * 1024) {
      setUploadError(t('studio.photoTooLarge'))
      return
    }
    setUploading(true)
    setUploadError(null)
    try {
      const image_base64 = await readImage(photo)
      const character = await fetchAPI<Character>(`/api/projects/${project.id}/characters/upload`, {
        method: 'POST',
        body: JSON.stringify({ name: name.trim(), image_base64, voice_description: voice.trim() }),
      })
      onCharacterAdded(character)
      if (characters.length === 0) {
        setScenes(current => current.map(scene => scene.character_ids.length ? scene : { ...scene, character_ids: [character.id] }))
      }
      setName('')
      setPhoto(null)
      setVoice('')
      if (photoInput.current) photoInput.current.value = ''
    } catch (e) {
      setUploadError(apiError(e))
    } finally {
      setUploading(false)
    }
  }

  async function generateCharacter(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (generatingReference || !name.trim() || referencePrompt.trim().length < 3) return
    setGeneratingReference(true)
    setUploadError(null)
    try {
      const result = await fetchAPI<ReferenceCreateResult>(`/api/projects/${project.id}/characters/generate`, {
        method: 'POST',
        body: JSON.stringify({
          name: name.trim(),
          image_prompt: referencePrompt.trim(),
          description: referencePrompt.trim(),
          voice_description: voice.trim(),
        }),
      })
      onCharacterAdded(result.character)
      if (characters.length === 0) {
        setScenes(current => current.map(scene => scene.character_ids.length ? scene : { ...scene, character_ids: [result.character.id] }))
      }
      setGeneratedCharacterId(result.character.id)
      setName('')
      setVoice('')
      setReferencePrompt('')
    } catch (e) {
      setGeneratingReference(false)
      setUploadError(apiError(e))
    }
  }

  function updateScene(key: string, patch: Partial<DraftScene>) {
    setScenes(current => current.map(scene => scene.key === key ? { ...scene, ...patch } : scene))
  }

  function classifyScript(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const parsed = parseScript(scriptText, characters)
    if (parsed.scenes.length === 0) {
      setScriptMessage(t('studio.scriptNoScenes'))
      return
    }
    setScenes(parsed.scenes)
    const details = [t('studio.scriptParsed', { n: parsed.scenes.length })]
    if (parsed.unknownNames.length > 0) details.push(`${t('studio.scriptUnknownCharacters')}: ${parsed.unknownNames.join(', ')}`)
    if (parsed.truncated) details.push(t('studio.scriptTooManyScenes'))
    setScriptMessage(details.join(' '))
  }

  function toggleCharacter(scene: DraftScene, id: string) {
    const selected = scene.character_ids.includes(id)
      ? scene.character_ids.filter(value => value !== id)
      : [...scene.character_ids, id]
    updateScene(scene.key, {
      character_ids: selected,
      speaker_id: selected.includes(scene.speaker_id) ? scene.speaker_id : '',
    })
  }

  function moveScene(index: number, direction: -1 | 1) {
    const target = index + direction
    if (target < 0 || target >= scenes.length) return
    setScenes(current => {
      const next = [...current]
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  async function createVideo(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (creating) return
    setCreateError(null)
    if (promptsNeedReview) {
      setCreateError('Nội dung cảnh đã thay đổi. Kiểm tra lại prompt đã áp dụng hoặc xóa prompt trước khi tạo video.')
      return
    }
    if (scenes.some(scene => scene.action.trim().length < 3)) {
      setCreateError(t('studio.actionRequired'))
      return
    }
    if (scenes.some(scene => scene.dialogue.trim() && !scene.speaker_id)) {
      setCreateError(t('studio.speakerRequired'))
      return
    }
    if (scenes.some(scene => scene.dialogue.trim().split(/\s+/).length > 15)) {
      setCreateError(t('studio.dialogueTooLong'))
      return
    }
    setCreating(true)
    try {
      const result = await fetchAPI<CreateResult>('/api/videos/quick-create', {
        method: 'POST',
        body: JSON.stringify({
          project_id: project.id,
          idea: title.trim(),
          orientation,
          material,
          scenes: scenes.map(scene => ({
            action: scene.action.trim(),
            character_ids: scene.character_ids,
            speaker_id: scene.speaker_id || null,
            dialogue: scene.dialogue.trim(),
            image_prompt: scene.image_prompt?.trim() || null,
            video_prompt: scene.video_prompt?.trim() || null,
          })),
        }),
      })
      try { localStorage.removeItem(`flowkit.studio.${project.id}`) } catch { /* Optional browser storage. */ }
      onVideoCreated(result.video_id)
    } catch (e) {
      setCreateError(apiError(e))
    } finally {
      setCreating(false)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex gap-2" role="group" aria-label="Kiểu video trong Studio">
        <Button type="button" variant={studioMode === 'video' ? 'default' : 'outline'} onClick={() => setStudioMode('video')}>Video AI</Button>
        <Button type="button" variant={studioMode === 'stickman' ? 'default' : 'outline'} onClick={() => setStudioMode('stickman')}>Video người que</Button>
      </div>
      {studioMode === 'stickman' ? <StickmanStudio key={project.id} project={project} characters={characters} onOpen={onVideoCreated} /> : <>
      <Card className="py-5 gap-4">
        <CardHeader>
          <CardTitle className="text-base">{t('studio.charactersTitle')}</CardTitle>
          <CardDescription className="text-xs">{t('studio.charactersHelp')}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          {characters.length > 0 && (
            <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(160px, 200px))' }}>
              {characters.map(character => (
                <div key={character.id} className="overflow-hidden rounded-md border" style={{ borderColor: 'var(--border)' }}>
                  <CharacterReferenceImage character={character} onRefresh={onRefreshReference} refreshing={refreshingReferenceId === character.id || refreshingReferenceId === 'all'} disabled={refreshingReferenceId !== null} revision={referenceImageRevision} feedback={referenceImageFeedback} />
                  {!character.reference_image_url && generatedCharacterId === character.id && <p className="p-2 text-[11px]" style={{ color: 'var(--muted)' }}>{t('studio.referenceQueued')}</p>}
                  <div className="p-2 text-xs">
                    <div className="font-semibold">{character.name}</div>
                    <div className="text-[11px]" style={{ color: 'var(--muted)' }}>{character.voice_description || t('studio.noVoice')}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
          <div className="flex flex-col gap-3 rounded-lg border p-3" style={{ borderColor: 'var(--border)', background: 'color-mix(in srgb, var(--surface) 70%, transparent)' }}>
            <div className="flex flex-wrap gap-2" role="tablist" aria-label={t('studio.charactersTitle')}>
              <Button type="button" size="sm" variant={referenceMode === 'upload' ? 'default' : 'outline'} onClick={() => { setReferenceMode('upload'); setUploadError(null) }}>{t('studio.referenceModeUpload')}</Button>
              <Button type="button" size="sm" variant={referenceMode === 'generate' ? 'default' : 'outline'} onClick={() => { setReferenceMode('generate'); setUploadError(null) }}>{t('studio.referenceModeGenerate')}</Button>
            </div>
            <div className="flex flex-col gap-1.5">
              <label htmlFor="studio-name" className="text-xs">{t('studio.characterName')}</label>
              <input id="studio-name" value={name} onChange={e => setName(e.target.value)} required maxLength={100} placeholder={t('studio.characterPlaceholder')} className="rounded-md px-3 py-2 text-sm" style={controlStyle} />
            </div>

            {referenceMode === 'upload' ? (
              <form onSubmit={addCharacter} className="grid gap-3 sm:grid-cols-2">
                <div className="flex flex-col gap-1.5 sm:col-span-2">
                  <span className="text-xs">{t('studio.photo')}</span>
                  <p className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('studio.referenceUploadHelp')}</p>
                  <input ref={photoInput} id="studio-photo" type="file" accept="image/png,image/jpeg,image/webp" onChange={e => setPhoto(e.target.files?.[0] ?? null)} required className="sr-only" />
                  <div className="flex flex-wrap items-center gap-3">
                    <Button type="button" variant="outline" onClick={() => photoInput.current?.click()}>{t('studio.choosePhoto')}</Button>
                    <span className="text-xs" style={{ color: 'var(--muted)' }}>{photo?.name || t('studio.noPhotoSelected')}</span>
                    {photo && photoPreview && <img src={photoPreview} alt={t('studio.photoPreview')} className="h-20 w-16 rounded-md object-cover" />}
                  </div>
                </div>
                <div className="flex flex-col gap-1.5 sm:col-span-2">
                  <label htmlFor="studio-voice" className="text-xs">{t('studio.voice')}</label>
                  <input id="studio-voice" value={voice} onChange={e => setVoice(e.target.value)} maxLength={300} placeholder={t('studio.voicePlaceholder')} className="rounded-md px-3 py-2 text-sm" style={controlStyle} />
                  <p className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('studio.voiceHint')}</p>
                </div>
                {uploadError && <p role="alert" className="text-xs sm:col-span-2" style={{ color: 'var(--red)' }}>{uploadError}</p>}
                <div className="sm:col-span-2"><Button type="submit" disabled={uploading || !photo || !name.trim()}>{uploading ? t('studio.uploading') : t('studio.addCharacter')}</Button></div>
              </form>
            ) : (
              <form onSubmit={generateCharacter} className="grid gap-3">
                <div className="flex flex-col gap-1.5">
                  <label htmlFor="studio-reference-prompt" className="text-xs">{t('studio.referencePrompt')}</label>
                  <textarea id="studio-reference-prompt" value={referencePrompt} onChange={e => setReferencePrompt(e.target.value)} required minLength={3} maxLength={1000} rows={3} placeholder={t('studio.referencePromptPlaceholder')} className="rounded-md px-3 py-2 text-sm" style={controlStyle} />
                  <p className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('studio.referenceGenerateHelp')}</p>
                </div>
                <div className="flex flex-col gap-1.5">
                  <label htmlFor="studio-generated-voice" className="text-xs">{t('studio.voice')}</label>
                  <input id="studio-generated-voice" value={voice} onChange={e => setVoice(e.target.value)} maxLength={300} placeholder={t('studio.voicePlaceholder')} className="rounded-md px-3 py-2 text-sm" style={controlStyle} />
                  <p className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('studio.voiceHint')}</p>
                </div>
                {uploadError && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{uploadError}</p>}
                <div><Button type="submit" disabled={generatingReference || !name.trim() || referencePrompt.trim().length < 3}>{generatingReference ? t('studio.generatingReference') : t('studio.createReference')}</Button></div>
              </form>
            )}
          </div>
          {generatedCharacterId && <p className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('studio.referenceQueued')}</p>}
        </CardContent>
      </Card>

      <Card className="py-5 gap-4">
        <CardHeader>
          <CardTitle className="text-base">{t('studio.scriptTitle')}</CardTitle>
          <CardDescription className="text-xs">{t('studio.scriptHelp')}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={classifyScript} className="flex flex-col gap-3">
            <textarea
              id="studio-script"
              value={scriptText}
              onChange={e => setScriptText(e.target.value)}
              rows={8}
              maxLength={12000}
              placeholder={t('studio.scriptPlaceholder')}
              className="rounded-md px-3 py-2 text-sm"
              style={controlStyle}
            />
            <div className="flex flex-wrap items-center gap-3">
              <Button type="submit" disabled={scriptText.trim().length < 3}>{t('studio.scriptAnalyze')}</Button>
              {scriptMessage && <span className="text-xs" style={{ color: 'var(--muted)' }}>{scriptMessage}</span>}
            </div>
          </form>
        </CardContent>
      </Card>

      <Card className="py-5 gap-4">
        <CardHeader>
          <CardTitle className="text-base">{t('studio.videoTitle')}</CardTitle>
          <CardDescription className="text-xs">{t('studio.videoHelp')}</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="mb-4">
            <FlowModelSummary />
          </div>
          <form onSubmit={createVideo} className="flex flex-col gap-5">
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="flex flex-col gap-1.5 sm:col-span-3">
                <label htmlFor="studio-title" className="text-xs">{t('studio.title')}</label>
                <input id="studio-title" value={title} onChange={e => setTitle(e.target.value)} required minLength={3} maxLength={200} placeholder={t('studio.titlePlaceholder')} className="rounded-md px-3 py-2 text-sm" style={controlStyle} />
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="studio-orientation" className="text-xs">{t('quickCreate.orientation')}</label>
                <select id="studio-orientation" value={orientation} onChange={e => setOrientation(e.target.value as Orientation)} className="rounded-md px-3 py-2 text-sm" style={controlStyle}>
                  <option value="VERTICAL">{t('quickCreate.vertical')}</option>
                  <option value="HORIZONTAL">{t('quickCreate.horizontal')}</option>
                </select>
              </div>
              <div className="flex flex-col gap-1.5">
                <label htmlFor="studio-material" className="text-xs">{t('quickCreate.material')}</label>
                <select id="studio-material" value={material} onChange={e => setMaterial(e.target.value)} className="rounded-md px-3 py-2 text-sm" style={controlStyle}>
                  {materials.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
                </select>
              </div>
              <div className="flex items-end text-xs" style={{ color: 'var(--muted)' }}>{t('studio.duration', { scenes: scenes.length, seconds: scenes.length * 8 })}</div>
            </div>

            <div className="flex flex-col gap-3">
              {scenes.map((scene, index) => (
                <div key={scene.key} className="flex flex-col gap-3 rounded-md border p-3" style={{ borderColor: 'var(--border)' }}>
                  <div className="flex items-center gap-2">
                    <strong className="text-xs">{t('studio.scene', { n: index + 1 })}</strong>
                    <span className="text-[11px]" style={{ color: 'var(--muted)' }}>8s</span>
                    <div className="ml-auto flex gap-1">
                      <Button type="button" size="sm" variant="outline" aria-label={t('studio.moveUp')} disabled={index === 0} onClick={() => moveScene(index, -1)}>↑</Button>
                      <Button type="button" size="sm" variant="outline" aria-label={t('studio.moveDown')} disabled={index === scenes.length - 1} onClick={() => moveScene(index, 1)}>↓</Button>
                      <Button type="button" size="sm" variant="outline" disabled={scenes.length === 1} onClick={() => setScenes(current => current.filter(item => item.key !== scene.key))}>{t('studio.removeScene')}</Button>
                    </div>
                  </div>
                  <div className="flex flex-col gap-1.5">
                    <label htmlFor={`studio-action-${scene.key}`} className="text-xs">{t('studio.action')}</label>
                    <textarea id={`studio-action-${scene.key}`} value={scene.action} onChange={e => updateScene(scene.key, { action: e.target.value })} required minLength={3} maxLength={1000} rows={2} placeholder={t('studio.actionPlaceholder')} className="rounded-md px-3 py-2 text-sm" style={controlStyle} />
                  </div>
                  <fieldset className="flex flex-wrap gap-3">
                    <legend className="mb-1.5 text-xs">{t('studio.cast')} · {scene.character_ids.length}/6</legend>
                    {characters.length === 0 ? <span className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('studio.noCharacters')}</span> : characters.map(character => (
                      <label key={character.id} className="flex items-center gap-1.5 text-xs">
                        <input type="checkbox" checked={scene.character_ids.includes(character.id)} disabled={!character.media_id || (!scene.character_ids.includes(character.id) && scene.character_ids.length >= 6)} onChange={() => toggleCharacter(scene, character.id)} />
                        {character.reference_image_url && <img src={character.reference_image_url} alt="" className="h-8 w-8 rounded object-cover" />}
                        {character.name}
                      </label>
                    ))}
                  </fieldset>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <div className="flex flex-col gap-1.5">
                      <label htmlFor={`studio-speaker-${scene.key}`} className="text-xs">{t('studio.speaker')}</label>
                      <select id={`studio-speaker-${scene.key}`} value={scene.speaker_id} onChange={e => updateScene(scene.key, { speaker_id: e.target.value })} className="rounded-md px-3 py-2 text-sm" style={controlStyle}>
                        <option value="">{t('studio.noSpeaker')}</option>
                        {characters.filter(character => scene.character_ids.includes(character.id)).map(character => <option key={character.id} value={character.id}>{character.name}</option>)}
                      </select>
                    </div>
                    <div className="flex flex-col gap-1.5 sm:col-span-2">
                      <label htmlFor={`studio-dialogue-${scene.key}`} className="text-xs">{t('studio.dialogue')}</label>
                      <input id={`studio-dialogue-${scene.key}`} value={scene.dialogue} onChange={e => updateScene(scene.key, { dialogue: e.target.value })} maxLength={200} placeholder={t('studio.dialoguePlaceholder')} className="rounded-md px-3 py-2 text-sm" style={controlStyle} />
                      <span className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('studio.dialogueHint')}</span>
                    </div>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button type="button" size="sm" variant="outline" disabled={creating || scene.action.trim().length < 3} onClick={() => setSuggesting({ ...scene })}>✦ Gợi ý prompt</Button>
                    <span className="text-[11px]" style={{ color: 'var(--muted)' }}>Chọn tham chiếu, nhập hành động rồi nhờ AI gợi ý.</span>
                  </div>
                  {(scene.image_prompt || scene.video_prompt) && <details open className="rounded-md border p-3" style={{ borderColor: 'var(--border)' }}>
                    <summary className="cursor-pointer text-xs font-medium">Prompt đã áp dụng · lưu cùng bản nháp</summary>
                    <div className="mt-3 grid gap-3">
                      <label className="grid gap-1 text-xs">Prompt ảnh
                        <textarea rows={3} maxLength={3000} value={scene.image_prompt || ''} onChange={e => updateScene(scene.key, { image_prompt: e.target.value })} className="rounded-md p-2 text-sm" style={controlStyle} />
                      </label>
                      <label className="grid gap-1 text-xs">Prompt video
                        <textarea rows={4} maxLength={4000} value={scene.video_prompt || ''} onChange={e => updateScene(scene.key, { video_prompt: e.target.value })} className="rounded-md p-2 text-sm" style={controlStyle} />
                      </label>
                      {scene.prompt_context !== promptContext(scene) && <div role="alert" className="text-xs" style={{ color: 'var(--yellow)' }}>
                        Hành động, lời thoại, tham chiếu hoặc khung hình đã đổi. Đọc và sửa prompt cho phù hợp.
                        <Button type="button" size="sm" variant="outline" className="ml-2" onClick={() => updateScene(scene.key, { prompt_context: promptContext(scene) })}>Đã kiểm tra prompt</Button>
                      </div>}
                      <Button type="button" size="sm" variant="ghost" className="justify-self-start" onClick={() => updateScene(scene.key, { image_prompt: undefined, video_prompt: undefined, prompt_context: undefined })}>Xóa prompt tùy chỉnh</Button>
                    </div>
                  </details>}
                </div>
              ))}
              <Button type="button" variant="outline" disabled={scenes.length >= 24} onClick={() => setScenes(current => [...current, { ...newScene(), character_ids: [...current[current.length - 1].character_ids] }])}>{t('studio.addScene')}</Button>
            </div>

            {createError && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{createError}</p>}
            <div className="flex flex-wrap items-center justify-between gap-3">
              <span className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('studio.creditNote')}</span>
              <Button type="submit" disabled={creating || promptsNeedReview || materials.length === 0 || title.trim().length < 3}>{creating ? t('studio.creating') : t('studio.create')}</Button>
            </div>
          </form>
        </CardContent>
      </Card>
      {suggesting && <PromptSuggestionDialog projectId={project.id} orientation={orientation} material={material} characters={characters}
        scene={suggesting} onClose={() => setSuggesting(null)} onApply={pair => {
          updateScene(suggesting.key, { ...pair, prompt_context: promptContext(suggesting) }); setSuggesting(null)
        }} />}
      </>}
    </div>
  )
}
