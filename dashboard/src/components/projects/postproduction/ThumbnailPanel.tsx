import { useEffect, useState, type FormEvent } from 'react'
import { Check, Download, ImagePlus, Sparkles } from 'lucide-react'
import { fetchAPI, patchAPI, resolveMediaUrl } from '../../../api/client'
import type { Character, Project, Video } from '../../../types'
import { useTranslation } from '../../../i18n/useTranslation'
import { Button } from '../../ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../../ui/card'

interface ThumbnailResult {
  success: boolean
  media_id: string | null
  file_url: string | null
  output_path: string | null
}

interface SavedThumbnail {
  filename: string
  file_url: string
  created_at: string
}

const controlStyle = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

export function ThumbnailPanel({ project, characters, video, onVideoUpdated }: {
  project: Project
  characters: Character[]
  video: Video | null
  onVideoUpdated: () => void | Promise<void>
}) {
  const { t } = useTranslation()
  const [prompt, setPrompt] = useState('')
  const [aspectRatio, setAspectRatio] = useState('LANDSCAPE')
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [savingSelection, setSavingSelection] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [thumbnails, setThumbnails] = useState<SavedThumbnail[]>([])
  const [selection, setSelection] = useState<{ videoId: string | undefined; url: string } | null>(null)
  const defaultFileUrl = thumbnails.find(item => item.file_url === video?.thumbnail_url)?.file_url
    || thumbnails.find(item => item.file_url === project.thumbnail_url)?.file_url
    || thumbnails[0]?.file_url || video?.thumbnail_url || project.thumbnail_url || ''
  const selectedFileUrl = selection?.videoId === video?.id ? selection?.url || defaultFileUrl : defaultFileUrl
  const selectFile = (url: string) => setSelection({ videoId: video?.id, url })

  useEffect(() => {
    let active = true
    fetchAPI<SavedThumbnail[]>(`/api/projects/${project.id}/thumbnails`)
      .then(saved => {
        if (!active) return
        setThumbnails(saved)
      })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : String(cause)) })
    return () => { active = false }
  }, [project.id])

  function toggleCharacter(name: string) {
    setSelected(current => current.includes(name) ? current.filter(item => item !== name) : [...current, name])
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (prompt.trim().length < 3) return
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const filename = 'thumbnail_' + crypto.randomUUID() + '.jpg'
      const result = await fetchAPI<ThumbnailResult>(`/api/projects/${project.id}/generate-thumbnail`, {
        method: 'POST',
        body: JSON.stringify({
          prompt: prompt.trim(),
          aspect_ratio: aspectRatio,
          character_names: selected,
          output_filename: filename,
        }),
      })
      if (!result.file_url) throw new Error(t('postProduction.thumbnailSaveError'))
      const saved: SavedThumbnail = {
        filename,
        file_url: result.file_url,
        created_at: new Date().toISOString(),
      }
      setThumbnails(current => [saved, ...current.filter(item => item.file_url !== saved.file_url)])
      selectFile(saved.file_url)
      setMessage(t('postProduction.thumbnailSaved'))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setBusy(false)
    }
  }

  async function applyThumbnailToVideo() {
    if (!video || !selectedFileUrl || savingSelection) return
    setSavingSelection(true)
    setError('')
    setMessage('')
    try {
      await patchAPI(`/api/videos/${video.id}`, { thumbnail_url: selectedFileUrl })
      setMessage(t('postProduction.thumbnailSelected', { video: video.title }))
      await onVideoUpdated()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSavingSelection(false)
    }
  }

  const selectedImageUrl = resolveMediaUrl(selectedFileUrl)
  const selectedAsset = thumbnails.find(item => item.file_url === selectedFileUrl)

  return (
    <Card className="py-4">
      <CardHeader>
        <div className="flex items-center gap-2">
          <ImagePlus className="size-4" style={{ color: 'var(--accent)' }} />
          <CardTitle className="text-sm">{t('postProduction.thumbnailTitle')}</CardTitle>
        </div>
        <CardDescription className="text-xs">{t('postProduction.thumbnailHelp')}</CardDescription>
      </CardHeader>
      <CardContent className="pp-thumbnail-content grid gap-5">
        <form onSubmit={submit} className="flex flex-col gap-3">
          <label htmlFor="thumbnail-prompt" className="text-xs">{t('postProduction.prompt')}</label>
          <textarea
            id="thumbnail-prompt"
            value={prompt}
            onChange={event => setPrompt(event.target.value)}
            rows={4}
            maxLength={2000}
            placeholder={t('postProduction.thumbnailPlaceholder')}
            className="rounded-md px-3 py-2 text-xs resize-y"
            style={controlStyle}
          />
          <div className="flex flex-wrap items-center gap-3">
            <label htmlFor="thumbnail-ratio" className="text-xs">{t('postProduction.ratio')}</label>
            <select id="thumbnail-ratio" value={aspectRatio} onChange={event => setAspectRatio(event.target.value)} className="rounded-md px-2.5 py-1.5 text-xs" style={controlStyle}>
              <option value="LANDSCAPE">{t('postProduction.landscape')}</option>
              <option value="PORTRAIT">{t('postProduction.portrait')}</option>
              <option value="SQUARE">{t('postProduction.square')}</option>
            </select>
          </div>
          {characters.length > 0 && (
            <fieldset className="flex flex-wrap gap-x-4 gap-y-2 rounded-md border p-3" style={{ borderColor: 'var(--border)' }}>
              <legend className="px-1 text-[11px]" style={{ color: 'var(--muted)' }}>{t('postProduction.references')}</legend>
              {characters.map(character => (
                <label key={character.id} className="flex items-center gap-1.5 text-xs" style={{ opacity: character.media_id ? 1 : 0.55 }}>
                  <input type="checkbox" disabled={!character.media_id} checked={selected.includes(character.name)} onChange={() => toggleCharacter(character.name)} />
                  {character.name}
                </label>
              ))}
            </fieldset>
          )}
          <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.flowCreditNote')}</p>
          {error && <p role="alert" className="m-0 text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
          <Button type="submit" size="sm" disabled={busy || prompt.trim().length < 3} className="self-start">
            <Sparkles className="size-3.5" />{busy ? t('postProduction.generating') : t('postProduction.generateThumbnail')}
          </Button>
        </form>
        <div className="flex min-h-44 flex-col items-center justify-center gap-2 rounded-lg p-3" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
          {selectedImageUrl ? (
            <>
              <img src={selectedImageUrl} alt={t('postProduction.thumbnailTitle')} className="max-h-64 w-full rounded object-contain" />
              {selectedAsset && <span className="self-start text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.thumbnailSavedAt', { date: new Date(selectedAsset.created_at).toLocaleString() })}</span>}
              <a href={selectedImageUrl} download={selectedAsset?.filename ?? 'thumbnail.jpg'} className="inline-flex items-center gap-1.5 self-start text-xs" style={{ color: 'var(--accent)' }}>
                <Download className="size-3.5" />{t('postProduction.downloadThumbnail')}
              </a>
              {video && selectedAsset && <Button type="button" size="sm" variant={video.thumbnail_url === selectedAsset.file_url ? 'outline' : 'default'} disabled={savingSelection || video.thumbnail_url === selectedAsset.file_url} onClick={() => void applyThumbnailToVideo()} className="self-start">
                {video.thumbnail_url === selectedAsset.file_url ? <><Check className="size-3.5" />{t('postProduction.thumbnailInUse')}</> : savingSelection ? t('postProduction.thumbnailSavingSelection') : t('postProduction.thumbnailUseForVideo')}
              </Button>}
            </>
          ) : <span className="text-xs" style={{ color: 'var(--muted)' }}>{t('postProduction.thumbnailPreview')}</span>}
          {message && <p role="status" className="m-0 self-start text-[10px]" style={{ color: 'var(--green)' }}>{message}</p>}
        </div>
        <p className="m-0 text-[10px] pp-thumbnail-wide" style={{ color: 'var(--muted)' }}>{t('postProduction.thumbnailStorageNote')}</p>
        {thumbnails.length > 0 && <section className="pp-thumbnail-wide flex flex-col gap-2">
          <h3 className="m-0 text-xs font-medium">{t('postProduction.savedThumbnails', { n: thumbnails.length })}</h3>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
            {thumbnails.map(thumbnail => {
              const active = thumbnail.file_url === selectedFileUrl
              const inUse = thumbnail.file_url === video?.thumbnail_url
              return <button key={thumbnail.file_url} type="button" aria-pressed={active} onClick={() => { selectFile(thumbnail.file_url); setMessage('') }} className="flex flex-col gap-1 rounded-md p-1 text-left" style={{ background: 'var(--surface)', border: `1px solid ${active ? 'var(--accent)' : 'var(--border)'}` }}>
                <img src={resolveMediaUrl(thumbnail.file_url)} alt={thumbnail.filename} className="aspect-video w-full rounded object-cover" />
                <span className="flex w-full items-center justify-between gap-1 text-[10px]">
                  <span className="truncate">{new Date(thumbnail.created_at).toLocaleString()}</span>
                  {inUse && <span className="shrink-0" style={{ color: 'var(--green)' }}>{t('postProduction.thumbnailInUse')}</span>}
                </span>
              </button>
            })}
          </div>
        </section>}
      </CardContent>
    </Card>
  )
}
