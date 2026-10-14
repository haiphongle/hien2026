import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { fetchAPI } from './client'
import { usePolling } from './usePolling'

export interface AudioMixJob {
  job_id: string
  video_id: string
  kind: 'music' | 'narration' | 'speech' | 'edit'
  status: 'QUEUED' | 'PROCESSING' | 'CANCELLING' | 'CANCELLED' | 'COMPLETED' | 'FAILED'
  stage: 'waiting' | 'validating' | 'downloading' | 'assembling' | 'narration' | 'music' | 'layout' | 'subtitles' | 'saving' | 'completed' | 'interrupted' | 'cancelling' | 'cancelled'
  progress: number
  stage_progress: number
  elapsed_seconds: number
  finished_at?: number | null
  error: string | null
  variant_index?: number; variant_total?: number
  result: { url: string; width?: number; height?: number; duration?: number; revision?: number } | null
}

export function useAudioMix(videoId: string, onCompleted: () => void | Promise<void>) {
  const [state, setState] = useState<{ videoId: string; job: AudioMixJob | null }>({ videoId, job: null })
  const [checkedVideoId, setCheckedVideoId] = useState('')
  const [pending, setPending] = useState<{ videoId: string; kind: AudioMixJob['kind'] } | null>(null)
  const [pollError, setPollError] = useState('')
  const submitController = useRef<AbortController | null>(null)
  const notified = useRef('')
  const notifyCompleted = useEffectEvent(onCompleted)
  const job = state.videoId === videoId ? state.job : null
  const checking = Boolean(videoId && checkedVideoId !== videoId)
  const submitting = pending?.videoId === videoId
  const running = job?.status === 'QUEUED' || job?.status === 'PROCESSING' || job?.status === 'CANCELLING'

  useEffect(() => () => submitController.current?.abort(), [videoId])

  usePolling<AudioMixJob | null>(
    videoId && (checking || running || pollError) ? `/api/videos/${videoId}/audio-mix-job` : null,
    1000,
    result => {
      if (result && result.video_id !== videoId) return
      setCheckedVideoId(videoId)
      setPollError('')
      setState({ videoId, job: result ?? (running && job ? { ...job, status: 'FAILED', stage: 'interrupted', error: null } : null) })
    },
    cause => setPollError(cause instanceof Error ? cause.message : String(cause)),
  )

  useEffect(() => {
    if (job?.status === 'COMPLETED' && notified.current !== job.job_id) {
      notified.current = job.job_id
      void Promise.resolve(notifyCompleted()).catch(cause => setPollError(String(cause)))
    }
  }, [job?.job_id, job?.status])

  async function start(kind: AudioMixJob['kind'], body: Record<string, unknown>) {
    if (!videoId || checking || running || submitting) return
    const controller = new AbortController()
    const pendingRequest = { videoId, kind }
    submitController.current = controller
    setPending(pendingRequest)
    setPollError('')
    try {
      const path = kind === 'edit' ? 'editing/jobs' : `mix-${kind === 'speech' ? 'narration' : kind}/jobs`
      const queued = await fetchAPI<AudioMixJob>(`/api/videos/${videoId}/${path}`, {
        method: 'POST', body: JSON.stringify(body), signal: controller.signal,
      })
      if (!controller.signal.aborted) setState({ videoId, job: queued })
    } catch (cause) {
      if (!controller.signal.aborted) {
        // A lost POST response may still have queued the export. Read status
        // before enabling another attempt, including a conflict from another tab.
        setCheckedVideoId('')
        throw cause
      }
    } finally {
      setPending(current => current === pendingRequest ? null : current)
    }
  }

  async function cancel() {
    if (!job || !running || job.status === 'CANCELLING') return
    const stopped = await fetchAPI<AudioMixJob>(`/api/videos/${videoId}/editing/jobs/${job.job_id}/cancel`, { method: 'POST' })
    setState({ videoId, job: stopped })
  }

  return { job, busy: checking || running || submitting || Boolean(pollError),
    pendingKind: submitting ? pending.kind : null, pollError, start, cancel }
}

export type AudioMixController = ReturnType<typeof useAudioMix>
