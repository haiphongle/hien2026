import { lazy, Suspense, useRef, useState } from 'react'
import { Clapperboard, Film } from 'lucide-react'
import type { Character, Project, Video } from '../../types'
import { useTranslation } from '../../i18n/useTranslation'
import { resolveMediaUrl } from '../../api/client'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/dialog'
import { ImportVideoButton } from './postproduction/ImportVideoButton'
import { TimelineEditor } from './postproduction/timeline/TimelineEditor'
import { MediaLibraryActions, MediaTrash, type LibraryChange } from './postproduction/MediaLibraryActions'
import type { AITool, TimelineController } from './postproduction/timeline/types'
import './postproduction/postproduction.css'

// Only mount the requested generator. Editing, preview and export belong to
// the timeline; these panels produce reusable media or scripts.
const SpeechPanel = lazy(() => import('./postproduction/SpeechPanel').then(m => ({ default: m.SpeechPanel })))
const NarrationPanel = lazy(() => import('./postproduction/NarrationPanel').then(m => ({ default: m.NarrationPanel })))
const MusicPanel = lazy(() => import('./postproduction/MusicPanel').then(m => ({ default: m.MusicPanel })))
const ThumbnailPanel = lazy(() => import('./postproduction/ThumbnailPanel').then(m => ({ default: m.ThumbnailPanel })))
const ProPainterTrialPanel = lazy(() => import('./postproduction/ProPainterTrialPanel').then(m => ({ default: m.ProPainterTrialPanel })))

interface Props {
  project: Project; characters: Character[]; videos: Video[]; videoId: string
  onVideoSelected: (videoId: string) => void
  onVideoUpdated: () => void | Promise<void>
}
const toolNames: Record<AITool, string> = {
  speech: 'Nhận dạng, dịch & lồng tiếng', narration: 'Tạo lời đọc theo cảnh',
  music: 'Tạo nhạc Suno', cover: 'Tạo ảnh bìa', restore: 'Xóa vật thể bằng AI · ProPainter',
}

export default function PostProductionPanel(props: Props) {
  return <PostProductionWorkspace key={props.videoId || props.project.id} {...props} />
}

function PostProductionWorkspace({ project, characters, videos, videoId, onVideoSelected, onVideoUpdated }: Props) {
  const { t } = useTranslation()
  const video = videos.find(item => item.id === videoId) ?? null
  const timeline = useRef<TimelineController | null>(null)
  const workspace = useRef<HTMLDivElement>(null), player = useRef<HTMLVideoElement>(null)
  const [tool, setTool] = useState<AITool | null>(null)
  const [suggestedVoice, setSuggestedVoice] = useState('')
  const [previewTime, setPreviewTime] = useState(0), [previewPlaying, setPreviewPlaying] = useState(false)
  const [navigationBusy, setNavigationBusy] = useState(false), [error, setError] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')

  function pauseMedia() {
    workspace.current?.querySelectorAll<HTMLMediaElement>('audio, video').forEach(media => media.pause())
    player.current?.pause()
  }
  function openTool(next: AITool) {
    pauseMedia(); setPreviewTime(0); setPreviewPlaying(false); setError('')
    const source = timeline.current?.getAssets().find(a => a.owner === videoId && a.ref === 'original' && !a.missing)
    setSourceUrl(resolveMediaUrl(source?.url) ?? '')
    setTool(next)
  }
  async function changeVideo(next: string) {
    setNavigationBusy(true); setError('')
    try { await timeline.current?.save(); pauseMedia(); onVideoSelected(next) }
    catch (cause) { setError(String(cause)) }
    finally { setNavigationBusy(false) }
  }
  async function applyResults() {
    if (!timeline.current) throw new Error('Bàn dựng chưa sẵn sàng. Đóng công cụ rồi nạp lại.')
    const report = await timeline.current.importResults({ captions: true, voices: true, effects: false, source: tool === 'narration' ? 'scenes' : 'speech' })
    pauseMedia()
    if (!report?.blocked.length) setTool(null)
    return report
  }
  async function addMusic(filename: string) {
    if (!timeline.current) throw new Error('Bàn dựng chưa sẵn sàng.')
    await timeline.current.addMusic(filename)
    pauseMedia(); setTool(null)
  }
  async function beforeLibraryChange() {
    pauseMedia(); await timeline.current?.save()
  }
  async function libraryChanged(change: LibraryChange) {
    try {
      if (change.kind === 'video' && change.id === videoId && (change.action === 'trash' || change.action === 'purge')) {
        timeline.current = null
        onVideoSelected(videos.find(item => item.id !== change.id)?.id ?? '')
      } else {
        await timeline.current?.refresh()
        if (!video && change.kind === 'video' && change.action === 'restore') onVideoSelected(change.id)
      }
    } finally {
      await onVideoUpdated()
    }
  }
  return <div className="post-production" ref={workspace} onPlayCapture={event => {
    if (!(event.target instanceof HTMLMediaElement)) return
    const target = event.target, timelineMedia = target.hasAttribute('data-timeline-media')
    workspace.current?.querySelectorAll<HTMLMediaElement>('audio, video').forEach(media => {
      if (media !== target && !(timelineMedia && media.hasAttribute('data-timeline-media'))) media.pause()
    })
  }}>
    <header className="pp-header">
      <div className="pp-brand"><span className="pp-brand-icon"><Clapperboard size={23} /></span><div><h2>{t('postEditor.title')}</h2><p>Một bản dựng cho hình, tiếng, phụ đề và hiệu ứng.</p></div></div>
      <div className="pp-video-controls"><label className="pp-video-select" htmlFor="post-production-video"><span>{t('postEditor.video')}</span><select id="post-production-video" value={videoId} onChange={e => void changeVideo(e.target.value)} disabled={!videos.length || navigationBusy}>{videos.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
      {video && <MediaLibraryActions projectId={project.id} itemId={video.id} name={video.title} disabled={navigationBusy || !!tool} beforeChange={beforeLibraryChange} onChanged={libraryChanged} />}</div>
      <ImportVideoButton projectId={project.id} onImported={async id => { await timeline.current?.save(); await onVideoUpdated(); onVideoSelected(id) }} />
      <MediaTrash projectId={project.id} disabled={navigationBusy || !!tool} beforeChange={beforeLibraryChange} onChanged={libraryChanged} />
    </header>
    {error && <p role="alert" className="tl-error">{error}</p>}
    {!video ? <div className="pp-empty"><Film size={34} /><p>{t('postEditor.noVideo')}</p></div> : <>
      <TimelineEditor videoId={video.id} projectId={project.id} title={video.title} active={!tool} blocked={navigationBusy} registerController={controller => { timeline.current = controller }} onExport={async () => { await onVideoUpdated() }} onOpenAI={openTool} onHandoff={async id => { await onVideoUpdated(); onVideoSelected(id) }} onLibraryChanged={libraryChanged} />
      <Dialog open={tool !== null} onOpenChange={open => { if (!open) { pauseMedia(); setTool(null) } }}>
        <DialogContent className="pp-ai-dialog" onPlayCapture={event => {
          if (!(event.target instanceof HTMLMediaElement)) return
          const media = event.target
          media.closest('.pp-ai-dialog')?.querySelectorAll<HTMLMediaElement>('audio, video').forEach(other => { if (other !== media) other.pause() })
        }}>
          <DialogHeader><DialogTitle>{tool ? toolNames[tool] : 'Công cụ AI'}</DialogTitle><DialogDescription>Tạo nội dung ở đây, đưa kết quả lên timeline để chỉnh sửa, nghe thử và xuất video.</DialogDescription></DialogHeader>
          <div className="pp-ai-body post-production"><Suspense fallback={<p className="pp-note">Đang mở công cụ…</p>}>
            {tool === 'speech' && <SpeechPanel video={video} active previewTime={previewTime} previewPlaying={previewPlaying} onPreviewPause={() => player.current?.pause()} onSeek={time => { if (player.current) player.current.currentTime = time; setPreviewTime(time) }} onCaptionChange={() => {}} onVideoUpdated={onVideoUpdated} suggestedNarratorVoiceId={suggestedVoice} onDismissNarratorVoice={() => setSuggestedVoice('')} onApplyTimeline={async () => { await applyResults() }} />}
            {tool === 'narration' && <NarrationPanel project={project} video={video} active previewPlaying={previewPlaying} onPreviewPause={() => player.current?.pause()} onOpenTimedSpeech={voice => { pauseMedia(); setSuggestedVoice(voice); setTool('speech') }} onApplyTimeline={applyResults} onEditScene={id => { pauseMedia(); setTool(null); timeline.current?.selectScene(id) }} />}
            {tool === 'music' && <MusicPanel projectId={project.id} video={video} onAddTimeline={addMusic} />}
            {tool === 'cover' && <ThumbnailPanel project={project} characters={characters} video={video} onVideoUpdated={onVideoUpdated} />}
            {tool === 'restore' && <ProPainterTrialPanel videoId={video.id} blocked={navigationBusy} timeline={timeline.current ?? undefined} onAddTimeline={async (results, restore) => {
              if (!timeline.current) throw new Error('Bàn dựng chưa sẵn sàng. Đóng rồi mở lại công cụ.')
              await timeline.current.addRestoration(results, restore)
            }} />}
          </Suspense></div>
          {tool === 'speech' && sourceUrl && <details className="pp-ai-source"><summary>Xem video nguồn để kiểm tra mốc câu</summary><video ref={player} src={sourceUrl} controls preload="none" onTimeUpdate={e => setPreviewTime(e.currentTarget.currentTime)} onPlay={() => setPreviewPlaying(true)} onPause={() => setPreviewPlaying(false)} /></details>}
        </DialogContent>
      </Dialog>
    </>}
  </div>
}
