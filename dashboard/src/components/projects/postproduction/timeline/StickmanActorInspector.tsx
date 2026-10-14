import { useEffect, useRef, useState } from 'react'
import { fetchAPI } from '../../../../api/client'
import { usePolling } from '../../../../api/usePolling'
import { emotions, gestures, type Emotion, type Gesture } from '../../stickmanOptions'
import type { Asset } from './types'

interface Actor { emotion: Emotion; gesture: Gesture }
interface ActorJob { id: string; status: string; error?: string; result?: { asset_id: string; asset: Asset } }

/** Replace only this picture asset. Timing, linked audio and undo stay in the editor. */
export function StickmanActorInspector({ videoId, assetId, disabled, before, apply }: {
  videoId: string; assetId: string; disabled: boolean; before: () => Promise<number>; apply: (asset: Asset) => Promise<void>
}) {
  const [actor, setActor] = useState<Actor | null>(null), [job, setJob] = useState<ActorJob | null>(null)
  const [pending, setPending] = useState(false), [error, setError] = useState('')
  const completed = useRef('')
  const running = job?.status === 'QUEUED' || job?.status === 'PROCESSING'
  useEffect(() => {
    const controller = new AbortController()
    fetchAPI<Actor>(`/api/videos/${videoId}/stickman/actors/${assetId}`, { signal: controller.signal })
      .then(setActor).catch(() => {})
    return () => controller.abort()
  }, [videoId, assetId])
  usePolling<ActorJob>(running ? `/api/videos/${videoId}/timeline/job` : null, 1000, next => {
    if (next?.id !== job?.id) return
    setJob(next)
    if (next.status === 'FAILED') setError(next.error || 'Không tạo được cử chỉ.')
    if (next.status === 'COMPLETED' && next.result?.asset && completed.current !== next.id) {
      completed.current = next.id
      void apply(next.result.asset).catch(cause => setError(String(cause)))
    }
  }, cause => setError(String(cause)))
  async function render() {
    if (!actor) return
    setPending(true); setError('')
    try {
      await before()
      setJob(await fetchAPI<ActorJob>(`/api/videos/${videoId}/stickman/actors`, {
        method: 'POST', body: JSON.stringify({ asset_id: assetId, emotion: actor.emotion, gesture: actor.gesture }),
      }))
    } catch (cause) { setError(String(cause)) } finally { setPending(false) }
  }
  if (!actor) return null
  return <fieldset disabled={disabled || pending || running} className="tl-layer-fields">
    <legend>Nhân vật nói chuyện</legend>
    <label>Biểu cảm<select aria-label="Biểu cảm nhân vật đã chọn" value={actor.emotion} onChange={e => setActor({ ...actor, emotion: e.target.value as Emotion })}>{Object.entries(emotions).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
    <label>Cử chỉ<select aria-label="Cử chỉ nhân vật đã chọn" value={actor.gesture} onChange={e => setActor({ ...actor, gesture: e.target.value as Gesture })}>{Object.entries(gestures).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
    <button onClick={() => void render()}>{pending || running ? 'Đang dựng cử chỉ…' : 'Áp dụng cho đoạn này'}</button>
    <p className="tl-help">Giữ tiếng và thời gian. Có thể hoàn tác sau khi thay hình. Miệng theo âm lượng, chưa phải khẩu hình theo âm vị.</p>
    {error && <p className="tl-error" role="alert">{error}</p>}
  </fieldset>
}
