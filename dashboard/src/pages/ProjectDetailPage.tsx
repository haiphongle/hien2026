import { useState, useEffect, useCallback, useRef } from 'react'
import { RefreshCw } from 'lucide-react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { fetchAPI, patchAPI, resolveMediaUrl } from '../api/client'
import type { Project, Character, Video, Scene, Request } from '../types'
import EditableText from '../components/projects/EditableText'
import PipelineView from '../components/pipeline/PipelineView'
import StoryStudio from '../components/projects/StoryStudio'
import CharacterReferenceImage from '../components/projects/CharacterReferenceImage'
import PostProductionPanel from '../components/projects/PostProductionPanel'
import FlowModelSummary from '../components/FlowModelSummary'
import { count, charStatus, videoStageBreakdown, videoProgress, type SceneStage } from '../lib/stageStats'
import { useTranslation } from '../i18n/useTranslation'
import { useWebSocketContext } from '../api/useWebSocketContext'
import { statusLabel, stateLabel, projectStatusLabel, stageLowerLabel } from '../i18n/labels'
import { Card, CardHeader, CardTitle, CardContent } from '../components/ui/card'
import { Badge } from '../components/ui/badge'
import { Progress } from '../components/ui/progress'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs'
import { Table, TableHeader, TableRow, TableHead, TableBody, TableCell } from '../components/ui/table'
import { Button } from '../components/ui/button'

type Tab = 'overview' | 'studio' | 'characters' | 'videos' | 'pipeline' | 'postproduction'
const STAGE_KEYS: ('refs' | SceneStage)[] = ['refs', 'image', 'video', 'upscale']

interface Props {
  projectId: string
  onBack: () => void
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString()
}

const STATUS_COLOR: Record<string, string> = {
  COMPLETED: 'var(--green)',
  PROCESSING: 'var(--yellow)',
  FAILED: 'var(--red)',
  PENDING: 'var(--muted)',
}

export default function ProjectDetailPage({ projectId, onBack }: Props) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const tab = (searchParams.get('tab') as Tab) ?? 'overview'
  const requestedVideoId = searchParams.get('video')

  const [project, setProject] = useState<Project | null>(null)
  const [characters, setCharacters] = useState<Character[]>([])
  const [videos, setVideos] = useState<Video[]>([])
  const [scenesByVideo, setScenesByVideo] = useState<Record<string, Scene[]>>({})
  const [requests, setRequests] = useState<Request[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [pipelineVideoId, setPipelineVideoId] = useState<string>('')
  const [refreshingReferenceId, setRefreshingReferenceId] = useState<string | null>(null)
  const referenceRefreshPending = useRef(false)
  const [referenceImageRevision, setReferenceImageRevision] = useState(0)
  const [referenceImageMessage, setReferenceImageMessage] = useState('')
  const [referenceImageError, setReferenceImageError] = useState(false)
  const [referenceImageTarget, setReferenceImageTarget] = useState<string | null>(null)
  const { lastEvent } = useWebSocketContext()

  const handleCharacterAdded = useCallback((character: Character) => {
    setCharacters(previous => {
      const existing = previous.some(item => item.id === character.id)
      return existing ? previous.map(item => item.id === character.id ? character : item) : [...previous, character]
    })
  }, [])

  async function refreshReferenceImages(character?: Character) {
    if (referenceRefreshPending.current) return
    referenceRefreshPending.current = true
    setRefreshingReferenceId(character?.id ?? 'all')
    setReferenceImageTarget(character?.id ?? 'all')
    setReferenceImageMessage('')
    setReferenceImageError(false)
    try {
      const path = character ? `${character.id}/refresh-image` : 'refresh-images'
      const result = await fetchAPI<{ refreshed: number; skipped: number; failed: number; errors: string[]; characters: Character[] }>(
        `/api/projects/${projectId}/characters/${path}`, { method: 'POST' })
      setCharacters(result.characters)
      setReferenceImageRevision(value => value + 1)
      setReferenceImageError(result.failed > 0)
      setReferenceImageMessage([
        character ? `Đã nạp lại ảnh ${character.name} từ Google Flow.` : `Đã nạp lại ${result.refreshed} ảnh từ Google Flow.`,
        result.skipped ? `${result.skipped} nhân vật chưa có ảnh trên Flow.` : '',
        result.failed ? `${result.failed} ảnh chưa nạp được. ${result.errors.join(' ')}` : '',
      ].filter(Boolean).join(' '))
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause)
      let detail = message
      try { detail = JSON.parse(message.replace(/^API \d+: /, '')).detail || message } catch { /* Plain network error. */ }
      setReferenceImageError(true)
      setReferenceImageMessage(detail)
    } finally {
      referenceRefreshPending.current = false
      setRefreshingReferenceId(null)
    }
  }

  const fetchAll = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const [proj, chars, vids] = await Promise.all([
        fetchAPI<Project>(`/api/projects/${projectId}`),
        fetchAPI<Character[]>(`/api/projects/${projectId}/characters`),
        fetchAPI<Video[]>(`/api/videos?project_id=${projectId}`),
      ])
      const sceneLists = await Promise.all(vids.map(v => fetchAPI<Scene[]>(`/api/scenes?video_id=${v.id}`)))
      const sbv: Record<string, Scene[]> = {}
      vids.forEach((v, i) => { sbv[v.id] = sceneLists[i] })
      const reqs = await fetchAPI<Request[]>(`/api/requests?project_id=${projectId}`)

      setProject(proj)
      setCharacters(chars)
      setVideos(vids)
      setScenesByVideo(sbv)
      setRequests(reqs)
      setPipelineVideoId(prev => prev && vids.some(v => v.id === prev) ? prev : (vids[0]?.id ?? ''))
    } catch (cause) {
      setLoadError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setLoading(false)
    }
  }, [projectId])

  // Saving a thumbnail or audio export changes videos only; keep form drafts mounted.
  const refreshVideos = useCallback(async () => {
    setVideos(await fetchAPI<Video[]>(`/api/videos?project_id=${projectId}`))
  }, [projectId])

  useEffect(() => { Promise.resolve().then(fetchAll) }, [fetchAll])

  useEffect(() => {
    if (!lastEvent || tab === 'studio') return
    if (lastEvent.type === 'request_update' || lastEvent.type === 'urls_refreshed') {
      Promise.resolve().then(fetchAll)
    }
  }, [lastEvent, tab, fetchAll])

  function setTab(t: Tab) {
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      next.set('tab', t)
      return next
    })
  }

  function selectPipelineVideo(videoId: string) {
    setPipelineVideoId(videoId)
    setSearchParams(prev => {
      const next = new URLSearchParams(prev)
      next.set('tab', 'pipeline')
      next.set('video', videoId)
      return next
    })
  }

  async function patchProject(field: string, value: string) {
    await patchAPI(`/api/projects/${projectId}`, { [field]: value })
    fetchAll()
  }

  async function patchChar(cid: string, field: string, value: string) {
    await patchAPI(`/api/characters/${cid}`, { [field]: value })
    fetchAll()
  }

  if (!project || project.id !== projectId) {
    return <div role={loadError ? 'alert' : 'status'} className="text-xs" style={{ color: loadError ? 'var(--red)' : 'var(--muted)' }}>{loadError || t('projectDetail.loading')}</div>
  }

  const allScenes = videos.flatMap(v => scenesByVideo[v.id] ?? [])
  const referenceImageFeedback = { characterId: referenceImageTarget, message: referenceImageMessage, error: referenceImageError }
  const selectedPipelineVideoId = requestedVideoId && videos.some(v => v.id === requestedVideoId)
    ? requestedVideoId : pipelineVideoId
  const stageRollup: Record<'refs' | SceneStage, ReturnType<typeof count>> = {
    refs: count(characters.map(c => charStatus(c, requests))),
    ...videoStageBreakdown(allScenes),
  }
  const stageKeys = requests.some(r => r.type === 'UPSCALE_VIDEO') ? STAGE_KEYS : STAGE_KEYS.filter(key => key !== 'upscale')

  return (
    <div className="flex flex-col gap-4" aria-busy={loading}>
      {loadError && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{loadError}</p>}
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-baseline gap-3">
            <h1 className="m-0 text-lg font-semibold" style={{ color: 'var(--text)' }}>{project.name}</h1>
            <Badge variant="outline">{project.material}</Badge>
            <Badge variant="outline">{projectStatusLabel(t, project.status)}</Badge>
          </div>
          <span className="text-[11px]" style={{ color: 'var(--muted)' }}>
            {t('projectDetail.header', { id: project.id, date: formatDate(project.created_at), videos: videos.length, scenes: allScenes.length })}
          </span>
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={() => setTab('studio')}>{t('studio.open')}</Button>
          <Button size="sm" variant="outline" onClick={() => navigate(`/?project_id=${projectId}#create-video`)}>{t('quickCreate.title')}</Button>
          <Button variant="ghost" size="sm" onClick={onBack}>{t('projectDetail.back')}</Button>
        </div>
      </div>

      {(tab === 'studio' || tab === 'characters') && characters.length > 0 && <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border px-4 py-3" style={{ borderColor: 'var(--border)', background: 'var(--surface)' }}>
        <div className="text-xs">
          <div className="font-medium">Ảnh tham chiếu từ Google Flow</div>
          <div className="mt-1" style={{ color: 'var(--muted)' }}>Bấm ảnh để xem lớn, hoặc nạp lại khi liên kết hết hạn. Cần mở Google Flow và kết nối extension.</div>
          {referenceImageMessage && <div role={referenceImageError ? 'alert' : 'status'} className="mt-1" style={{ color: referenceImageError ? 'var(--red)' : 'var(--green)' }}>{referenceImageMessage}</div>}
        </div>
        <Button size="sm" variant="outline" disabled={refreshingReferenceId !== null || !characters.some(character => character.media_id)} onClick={() => void refreshReferenceImages()}>
          <RefreshCw className={`size-3.5 ${refreshingReferenceId === 'all' ? 'animate-spin' : ''}`} />
          {refreshingReferenceId === 'all' ? 'Đang nạp ảnh…' : 'Nạp lại tất cả ảnh'}
        </Button>
      </div>}

      <Tabs value={tab} onValueChange={v => setTab(v as Tab)}>
        <TabsList>
          <TabsTrigger value="overview">{t('projectDetail.tab.overview')}</TabsTrigger>
          <TabsTrigger value="studio">{t('studio.tab')}</TabsTrigger>
          <TabsTrigger value="characters">{t('projectDetail.tab.characters', { n: characters.length })}</TabsTrigger>
          <TabsTrigger value="videos">{t('projectDetail.tab.videos', { n: videos.length })}</TabsTrigger>
          <TabsTrigger value="pipeline">{t('projectDetail.tab.pipeline')}</TabsTrigger>
          <TabsTrigger value="postproduction">{t('projectDetail.tab.postproduction')}</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="pt-4">
          <div className="grid gap-4" style={{ gridTemplateColumns: '1.4fr 1fr' }}>
            <Card className="py-4">
              <CardHeader>
                <CardTitle className="text-xs tracking-widest uppercase">{t('projectDetail.card.projectFields')}</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="flex flex-col gap-3.5">
                  {[
                    { label: t('projectDetail.field.name'), value: project.name, field: 'name', multiline: false },
                    { label: t('projectDetail.field.description'), value: project.description ?? '', field: 'description', multiline: true },
                    { label: t('projectDetail.field.story'), value: project.story ?? '', field: 'story', multiline: true },
                  ].map(f => (
                    <div key={f.field} className="flex flex-col gap-1">
                      <span className="text-[9px] tracking-widest" style={{ color: 'var(--muted)' }}>{f.label}</span>
                      <div className="rounded-md px-2.5 py-2 text-xs" style={{ background: 'var(--surface)', border: '1px solid var(--border)' }}>
                        <EditableText value={f.value} onSave={v => patchProject(f.field, v)} multiline={f.multiline} className="text-xs" />
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            <div className="flex flex-col gap-4">
              <Card className="py-4">
                <CardHeader>
                  <CardTitle className="text-xs tracking-widest uppercase">{t('projectDetail.card.narrator')}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-1.5 text-[11px]">
                    <div className="flex justify-between"><span style={{ color: 'var(--muted)' }}>{t('projectDetail.narrator.enabled')}</span><span>{project.narrator_voice ? t('projectDetail.true') : t('projectDetail.false')}</span></div>
                    <div className="flex justify-between"><span style={{ color: 'var(--muted)' }}>{t('projectDetail.narrator.voice')}</span><span>{project.narrator_voice ?? t('projectDetail.noNarration')}</span></div>
                    <div className="flex justify-between"><span style={{ color: 'var(--muted)' }}>{t('projectDetail.narrator.refAudio')}</span><span>{project.narrator_ref_audio ?? t('common.dash')}</span></div>
                  </div>
                </CardContent>
              </Card>

              <Card className="py-4">
                <CardHeader>
                  <CardTitle className="text-xs tracking-widest uppercase">{t('projectDetail.card.stageRollup')}</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-col gap-2.5">
                    {stageKeys.map(key => {
                      const c = stageRollup[key]
                      const pct = c.total > 0 ? Math.round((c.done / c.total) * 100) : 0
                      return (
                        <div key={key} className="grid items-center gap-2.5" style={{ gridTemplateColumns: '60px 1fr 50px' }}>
                          <span className="text-[10px] tracking-wide uppercase" style={{ color: 'var(--text)' }}>{stageLowerLabel(t, key)}</span>
                          <Progress value={pct} className="h-1" />
                          <span className="text-[10px] text-right" style={{ color: 'var(--muted)' }}>{c.done}/{c.total}</span>
                        </div>
                      )
                    })}
                  </div>
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="studio" className="pt-4">
          <StoryStudio
            project={project}
            characters={characters}
            onCharacterAdded={handleCharacterAdded}
            onRefreshReference={character => void refreshReferenceImages(character)}
            refreshingReferenceId={refreshingReferenceId}
            referenceImageRevision={referenceImageRevision}
            referenceImageFeedback={referenceImageFeedback}
            onVideoCreated={async (videoId, target) => {
              await fetchAll()
              if (target === 'postproduction') {
                setSearchParams(previous => { const next = new URLSearchParams(previous); next.set('tab', target); next.set('video', videoId); return next })
              } else selectPipelineVideo(videoId)
            }}
          />
        </TabsContent>

        <TabsContent value="characters" className="pt-4">
          <Button size="sm" className="mb-4" onClick={() => setTab('studio')}>{t('studio.addCharacter')}</Button>
          {characters.length === 0 ? (
            <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('projectDetail.noCharacters')}</div>
          ) : (
            <div className="grid gap-3.5" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
              {characters.map(ch => {
                const st = charStatus(ch, requests)
                return (
                  <Card key={ch.id} className="py-0 gap-0 overflow-hidden h-full">
                    <div className="relative">
                      <CharacterReferenceImage character={ch} onRefresh={character => void refreshReferenceImages(character)} refreshing={refreshingReferenceId === ch.id || refreshingReferenceId === 'all'} disabled={refreshingReferenceId !== null} revision={referenceImageRevision} feedback={referenceImageFeedback} imageClassName="aspect-square w-full object-contain" />
                      <span className="pointer-events-none absolute top-2 left-2 flex items-center gap-1.5 px-1.5 py-0.5 rounded text-[9px] tracking-wide" style={{ background: 'rgba(10,10,20,0.8)', color: STATUS_COLOR[st] }}>
                        <span className="w-1.5 h-1.5 rounded-full" style={{ background: STATUS_COLOR[st] }} />{statusLabel(t, st)}
                      </span>
                    </div>
                    <div className="p-3 flex flex-col gap-1.5">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold">{ch.name}</span>
                        <Badge variant="outline">{ch.entity_type}</Badge>
                      </div>
                      <div className="text-[11px]" style={{ color: 'var(--muted)' }}>
                        <EditableText value={ch.description ?? ''} onSave={v => patchChar(ch.id, 'description', v)} multiline className="text-[11px]" />
                      </div>
                      <div className="text-[11px]" style={{ color: 'var(--muted)' }}>
                        {t('studio.voice')}: <EditableText value={ch.voice_description ?? ''} onSave={v => patchChar(ch.id, 'voice_description', v)} className="text-[11px]" />
                      </div>
                      <span className="text-[9px] tracking-wide" style={{ color: 'var(--muted)' }}>{t('projectDetail.character.updated', { date: formatDate(ch.updated_at) })}</span>
                    </div>
                  </Card>
                )
              })}
            </div>
          )}
        </TabsContent>

        <TabsContent value="videos" className="pt-4">
          {videos.length === 0 ? (
            <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('projectDetail.noVideos')}</div>
          ) : (
            <Card className="py-4">
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t('projectDetail.table.video')}</TableHead>
                      <TableHead>{t('projectDetail.table.scenes')}</TableHead>
                      <TableHead>{t('projectDetail.table.progress')}</TableHead>
                      <TableHead>{t('projectDetail.table.state')}</TableHead>
                      <TableHead></TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {videos.map(v => {
                      const scenes = scenesByVideo[v.id] ?? []
                      const pct = videoProgress(scenes, v.orientation)
                      const anyProcessing = requests.some(r => r.video_id === v.id && r.status === 'PROCESSING')
                      const state: 'COMPLETED' | 'RUNNING' | 'QUEUED' | 'FAILED' =
                        v.status === 'FAILED' ? 'FAILED'
                          : v.status === 'COMPLETED' ? 'COMPLETED'
                            : v.status === 'PROCESSING' || anyProcessing ? 'RUNNING'
                              : pct === 100 && scenes.length > 0 ? 'COMPLETED' : 'QUEUED'
                      return (
                        <TableRow key={v.id}>
                          <TableCell>
                            <div className="text-xs">{v.title}</div>
                            <div className="text-[9px]" style={{ color: 'var(--muted)' }}>{v.id.slice(0, 8)}</div>
                          </TableCell>
                          <TableCell className="text-xs" style={{ color: 'var(--muted)' }}>{scenes.length}</TableCell>
                          <TableCell>
                            <div className="flex flex-col gap-1" style={{ width: 120 }}>
                              <span className="text-[10px]" style={{ color: 'var(--muted)' }}>{pct}%</span>
                              <Progress value={pct} className="h-1" />
                            </div>
                          </TableCell>
                          <TableCell><Badge variant={state === 'FAILED' ? 'destructive' : state === 'COMPLETED' ? 'secondary' : state === 'RUNNING' ? 'default' : 'outline'}>{stateLabel(t, state)}</Badge></TableCell>
                          <TableCell>
                            <span
                              className="text-[10px] cursor-pointer"
                              style={{ color: 'var(--accent)' }}
                              onClick={() => selectPipelineVideo(v.id)}
                            >
                              {t('projectDetail.pipelineLink')}
                            </span>
                          </TableCell>
                        </TableRow>
                      )
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="postproduction" className="pt-4">
          <PostProductionPanel
            key={project.id}
            project={project}
            characters={characters}
            videos={videos}
            videoId={selectedPipelineVideoId}
            onVideoSelected={videoId => setSearchParams(previous => {
              const next = new URLSearchParams(previous)
              next.set('video', videoId)
              return next
            })}
            onVideoUpdated={refreshVideos}
          />
        </TabsContent>

        <TabsContent value="pipeline" className="pt-4">
          {videos.length === 0 ? (
            <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('projectDetail.pipeline.noVideos')}</div>
          ) : (
            <div className="flex flex-col gap-4">
              <div className="flex items-center gap-2">
                <span className="text-[9px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('projectDetail.pipeline.videoLabel')}</span>
                {videos.map(v => (
                  <Button key={v.id} variant={v.id === selectedPipelineVideoId ? 'default' : 'outline'} size="sm" onClick={() => selectPipelineVideo(v.id)}>
                    {v.title}
                  </Button>
                ))}
              </div>
              <FlowModelSummary />
              {(() => {
                const selectedVideo = videos.find(v => v.id === selectedPipelineVideoId)
                const finalUrl = resolveMediaUrl(selectedVideo?.orientation === 'HORIZONTAL'
                  ? selectedVideo.horizontal_url
                  : selectedVideo?.vertical_url)
                if (!selectedVideo || selectedVideo.status !== 'COMPLETED' || !finalUrl) return null
                return (
                  <Card className="py-4">
                    <CardHeader>
                      <CardTitle className="text-xs tracking-widest uppercase">{t('gallery.finishedVideos')}</CardTitle>
                    </CardHeader>
                    <CardContent>
                      <div className="flex flex-col gap-3">
                        <video
                          key={finalUrl}
                          src={finalUrl}
                          controls
                          preload="metadata"
                          className="w-full rounded-md"
                          style={{ maxHeight: 520, background: '#000' }}
                        />
                        <a
                          href={finalUrl}
                          download={`${selectedVideo.title || 'frameweaver-video'}.mp4`}
                          className="self-start text-xs px-3 py-1.5 rounded font-semibold"
                          style={{ background: 'var(--accent)', color: '#fff', textDecoration: 'none' }}
                        >
                          {t('gallery.downloadVideo')}
                        </a>
                      </div>
                    </CardContent>
                  </Card>
                )
              })()}
              {selectedPipelineVideoId && <PipelineView projectId={projectId} videoId={selectedPipelineVideoId} onAssembled={fetchAll} />}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
