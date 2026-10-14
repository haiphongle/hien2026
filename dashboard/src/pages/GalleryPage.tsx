import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchAPI, resolveMediaUrl } from '../api/client'
import type { Project, Video, Scene } from '../types'
import VideoGallery from '../components/gallery/VideoGallery'
import { useTranslation } from '../i18n/useTranslation'
import { useWebSocketContext } from '../api/useWebSocketContext'

function hasExpiringSignedUrl(url: string | null | undefined): boolean {
  if (!url) return false
  try {
    const expires = Number(new URL(url, window.location.origin).searchParams.get('Expires'))
    return Number.isFinite(expires) && expires > 0 && expires * 1000 <= Date.now() + 5 * 60 * 1000
  } catch {
    return false
  }
}

export default function GalleryPage() {
  const { t } = useTranslation()
  const { lastEvent } = useWebSocketContext()
  const [projects, setProjects] = useState<Project[]>([])
  const [selectedProject, setSelectedProject] = useState<string>('')
  const [videos, setVideos] = useState<Video[]>([])
  const [scenes, setScenes] = useState<(Scene & { videoTitle: string; orientation: string | null })[]>([])
  const [loading, setLoading] = useState(false)
  const refreshAttempts = useRef(new Map<string, number>())
  const refreshRequests = useRef(new Map<string, Promise<boolean>>())

  useEffect(() => {
    fetchAPI<Project[]>('/api/projects')
      .then(ps => {
        const active = ps.filter(p => p.status !== 'DELETED')
        setProjects(active)
        if (active.length > 0) setSelectedProject(active[0].id)
      })
      .catch(console.error)
  }, [])

  const loadProjectMedia = useCallback(async (projectId: string) => {
    setLoading(true)
    const fetchMedia = async () => {
      const vids = await fetchAPI<Video[]>(`/api/videos?project_id=${projectId}`)
      const sceneLists = await Promise.all(vids.map(v => fetchAPI<Scene[]>(`/api/scenes?video_id=${v.id}`)))
      const merged = vids.flatMap((v, i) => sceneLists[i].map(s => ({ ...s, videoTitle: v.title, orientation: v.orientation ?? 'VERTICAL' })))
      return { vids, sceneLists, merged }
    }

    const refreshUrls = async () => {
      const inFlight = refreshRequests.current.get(projectId)
      if (inFlight) return inFlight

      const attemptedAt = refreshAttempts.current.get(projectId) ?? 0
      if (Date.now() - attemptedAt < 5 * 60 * 1000) return false

      refreshAttempts.current.set(projectId, Date.now())
      const request = fetchAPI<{ refreshed: number; found: number }>(`/api/flow/refresh-urls/${projectId}`, { method: 'POST' })
        .then(result => result.refreshed > 0)
        .catch(error => {
          console.warn('Gallery could not refresh expired Flow media URLs:', error)
          return false
        })
        .finally(() => refreshRequests.current.delete(projectId))
      refreshRequests.current.set(projectId, request)
      return request
    }

    try {
      let media = await fetchMedia()
      const expired = [
        ...media.vids.flatMap(video => [video.vertical_url, video.horizontal_url, video.thumbnail_url]),
        ...media.sceneLists.flat().flatMap(scene => [
          scene.vertical_image_url,
          scene.horizontal_image_url,
          scene.vertical_video_url,
          scene.horizontal_video_url,
          scene.vertical_upscale_url,
          scene.horizontal_upscale_url,
        ]),
      ].some(hasExpiringSignedUrl)

      if (expired && await refreshUrls()) media = await fetchMedia()
      setVideos(media.vids)
      setScenes(media.merged)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!selectedProject) return
    Promise.resolve().then(() => loadProjectMedia(selectedProject).catch(console.error))
  }, [selectedProject, loadProjectMedia])

  useEffect(() => {
    if (lastEvent?.type === 'request_update' && selectedProject) {
      Promise.resolve().then(() => loadProjectMedia(selectedProject).catch(console.error))
    }
  }, [lastEvent, selectedProject, loadProjectMedia])

  const finishedVideos = videos.filter(video => video.status === 'COMPLETED' && (video.vertical_url || video.horizontal_url))

  return (
    <div className="flex flex-col gap-4">
      {/* Filters */}
      <div className="flex gap-3 flex-wrap items-end">
        <div className="flex flex-col gap-1">
          <label className="text-xs" style={{ color: 'var(--muted)' }}>{t('gallery.projectLabel')}</label>
          <select
            value={selectedProject}
            onChange={e => setSelectedProject(e.target.value)}
            className="text-xs px-2 py-1.5 rounded outline-none"
            style={{ background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }}
          >
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
        <span className="text-[11px] ml-auto" style={{ color: 'var(--muted)' }}>
          {t('gallery.count', { videos: videos.length, scenes: scenes.length })}
        </span>
      </div>

      {loading ? (
        <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('gallery.loading')}</div>
      ) : (
        <>
          {finishedVideos.length > 0 && (
            <section className="flex flex-col gap-3">
              <h2 className="text-sm font-semibold">{t('gallery.finishedVideos')}</h2>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {finishedVideos.map(video => {
                  const src = resolveMediaUrl(video.orientation === 'HORIZONTAL' ? video.horizontal_url : video.vertical_url)
                  const poster = resolveMediaUrl(video.thumbnail_url)
                  return (
                    <div key={video.id} className="rounded-lg p-3 flex flex-col gap-2" style={{ background: 'var(--card)', border: '1px solid var(--border)' }}>
                      <div className="text-xs font-semibold truncate">{video.title}</div>
                      <video src={src} poster={poster} controls preload="metadata" className="w-full rounded-md" style={{ maxHeight: 340, background: '#000' }} />
                      <a href={src ?? undefined} download={`${video.id}.mp4`} className="text-xs" style={{ color: 'var(--accent)' }}>{t('gallery.downloadVideo')}</a>
                    </div>
                  )
                })}
              </div>
            </section>
          )}
          <VideoGallery scenes={scenes} />
        </>
      )}
    </div>
  )
}
