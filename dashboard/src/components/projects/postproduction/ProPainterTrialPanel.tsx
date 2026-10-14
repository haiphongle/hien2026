import { useEffect, useRef, useState } from 'react'
import { CheckCircle2, Download, Eraser, Loader2, RotateCcw, Square } from 'lucide-react'
import { fetchAPI, resolveMediaUrl } from '../../../api/client'
import { usePolling } from '../../../api/usePolling'
import { Button } from '../../ui/button'
import { DEFAULT_CAPTION_STYLE, DEFAULT_COLORS, DEFAULT_FRAME, type EditRegion, type EditingSettings } from './editingTypes'
import { ProPainterRegionPicker } from './ProPainterRegionPicker'
import { maskProblem, trialDraft, trialLimit, type TrialDraft, type TrialSource, type RestorationResult } from './propainter'

import type { Asset, Clip, TimelineController } from './timeline/types'
type Result = RestorationResult
interface Status {
  available: boolean; unavailable_reason?: string; scope: string; source: TrialSource | null; source_error?: string
  results: Result[]; benchmark: { inference_seconds?: number }
}
interface Job { id: string; status: string; stage: string; progress: number; created_at: number; error?: string; result?: Partial<Result> & { trial_ids?: string[] } }
interface Props { videoId: string; blocked: boolean; timeline?: TimelineController; onAddTimeline?: (results: Result[], restore?: boolean) => Promise<void> }
interface Selection { selectedClip?: Clip; sourceAsset?: Asset; clipIds?: string[] }
// Inpaint the source before applying the timeline's output crop/zoom/colors.
// Otherwise those transforms would be baked in and applied a second time.
const settings: EditingSettings = { frame: { ...DEFAULT_FRAME }, colors: { ...DEFAULT_COLORS }, captions: { ...DEFAULT_CAPTION_STYLE }, mode: 'original', voice_source: 'speech', source_volume: 1, narration_volume: 1, use_music: false, regions: [] }
const clock = (n: number) => `${Math.floor(n / 60).toString().padStart(2, '0')}:${(n % 60).toFixed(2).padStart(5, '0')}`

export function ProPainterTrialPanel(props: Props) {
  const document = props.timeline?.getDocument(), assets = props.timeline?.getAssets() ?? []
  const track = document?.tracks.find(t => t.kind === 'video')
  const pictures = document?.clips.filter(c => c.track_id === track?.id).sort((a, b) => a.start-b.start) ?? []
  const [selected] = useState(() => props.timeline?.getSelectedVideoIds() ?? [])
  const [chosen, setChosen] = useState(() => selected[0] ?? pictures[0]?.id ?? '')
  const [scope, setScope] = useState<'one' | 'selected' | 'all'>(selected.length > 1 ? 'selected' : 'one')
  const clip = pictures.find(c => c.id === chosen)
  const asset = assets.find(a => a.id === clip?.asset_id && !a.missing)
  const clipIds = scope === 'all' ? pictures.map(c => c.id) : scope === 'selected' ? selected : clip ? [clip.id] : []
  return <>
    {props.timeline && <section className="pp-edit-card"><h3>Chọn cảnh cần xóa logo</h3><div className="pp-edit-grid">
      <label>Cảnh để khoanh vùng<select value={chosen} disabled={props.blocked} onChange={e => setChosen(e.target.value)}>{pictures.map((c, i) => <option value={c.id} key={c.id}>Cảnh {i+1} · {clock(c.start)} · {c.duration.toFixed(2)}s · {assets.find(a => a.id === c.asset_id)?.name}</option>)}</select></label>
      <label>Áp dụng vùng xóa cho<select value={scope} disabled={props.blocked} onChange={e => setScope(e.target.value as typeof scope)}><option value="one">Cảnh đang chọn</option>{selected.length > 1 && <option value="selected">{selected.length} cảnh đã chọn trên timeline</option>}<option value="all">Tất cả {pictures.length} cảnh</option></select></label>
    </div><p>Khoanh logo trên khung nguồn. Với nhiều cảnh, vùng xóa dùng cùng vị trí tương đối; kiểm tra các cảnh trước khi chạy. Mỗi cảnh từ 1–30 giây nguồn.</p></section>}
    {props.timeline && !asset ? <p role="alert">Chọn một cảnh có tư liệu còn dùng được trên timeline.</p> : <ProPainterSource key={asset?.id ?? 'source'} {...props} selectedClip={clip} sourceAsset={asset} clipIds={clipIds} />}
  </>
}

function ProPainterSource(props: Props & Selection) {
  const [status, setStatus] = useState<Status | null>(null), [error, setError] = useState('')
  usePolling<Status>(!status ? `/api/videos/${props.videoId}/editing/propainter${props.sourceAsset ? `?asset_id=${props.sourceAsset.id}` : ''}` : null, 5000,
    data => { setStatus(data); setError('') }, cause => setError(String(cause)))
  if (!status) return <section className="pp-edit-card"><p role="status"><Loader2 size={16} className="animate-spin" />Đang kiểm tra công cụ xóa vật thể…</p>{error && <p role="alert">{error}</p>}</section>
  return <ProPainterWorkspace key={`${props.videoId}:${status.source?.fingerprint}`} {...props} status={status} onRetry={() => setStatus(null)} />
}

function ProPainterWorkspace({ videoId, blocked, onAddTimeline, timeline, selectedClip, sourceAsset, clipIds, status, onRetry }: Props & Selection & { status: Status; onRetry: () => void }) {
  const source = status.source
  const storageKey = `flowkit:propainter:${videoId}:${source?.fingerprint}:${JSON.stringify([settings.frame, settings.colors])}`
  const [draft, setDraft] = useState<TrialDraft>(() => {
    let saved: unknown
    try { saved = JSON.parse(localStorage.getItem(storageKey) ?? 'null') } catch { /* A new selection remains available. */ }
    return source ? trialDraft(source, saved) : { start: 0, duration: 1, dilation: 4, maxSide: 192 as const, box: null }
  })
  const [regionId] = useState(() => crypto.randomUUID())
  const [job, setJob] = useState<Job | null>(null), [checked, setChecked] = useState(false)
  const [pending, setPending] = useState(false), [error, setError] = useState(''), [notice, setNotice] = useState('')
  const [pollError, setPollError] = useState('')
  const [history, setHistory] = useState(status.results ?? []), [chosen, setChosen] = useState(status.results?.[0]?.trial_id ?? '')
  const [side, setSide] = useState<'before' | 'after'>('after'), [adding, setAdding] = useState(false)
  const [framePosition, setFramePosition] = useState(0), [previewReady, setPreviewReady] = useState(false)
  const [now, setNow] = useState(Date.now())
  const commanded = useRef<string | null>(null), terminal = useRef<string | null>(null)
  const running = job?.status === 'QUEUED' || job?.status === 'PROCESSING'
  const busy = running || pending || blocked || !checked
  usePolling<Job | null>(!checked || running ? `/api/videos/${videoId}/editing/propainter/job` : null, 1500, data => {
    if (commanded.current && data?.id !== commanded.current) return
    if (data?.id === terminal.current && ['QUEUED', 'PROCESSING'].includes(data.status)) return
    if (data && !['QUEUED', 'PROCESSING'].includes(data.status)) { terminal.current = data.id; commanded.current = null }
    setChecked(true); setJob(data); setPollError('')
  }, cause => setPollError(String(cause)))
  useEffect(() => {
    if (!job || ['QUEUED', 'PROCESSING'].includes(job.status)) return
    const abort = new AbortController()
    fetchAPI<Status>(`/api/videos/${videoId}/editing/propainter${sourceAsset ? `?asset_id=${sourceAsset.id}` : ''}`, { signal: abort.signal })
      .then(data => { if (!abort.signal.aborted) { setHistory(data.results); setChosen(job.result?.trial_ids?.[0] ?? job.result?.trial_id ?? data.results[0]?.trial_id ?? ''); setSide('after') } })
      .catch(cause => { if (!abort.signal.aborted) setPollError(String(cause)) })
    return () => abort.abort()
  }, [job, videoId, sourceAsset])
  useEffect(() => {
    if (!source) return
    const timer = setTimeout(() => {
      try { localStorage.setItem(storageKey, JSON.stringify(draft)) } catch { /* Saving the result is handled by the server. */ }
    }, 250)
    return () => clearTimeout(timer)
  }, [source, storageKey, draft])
  useEffect(() => {
    if (!running) return
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [running])
  function change(patch: Partial<TrialDraft>) {
    setDraft(value => ({ ...value, ...patch })); setNotice('')
  }
  const maxLength = source ? trialLimit(source) : 0
  const rangeStart = selectedClip?.source_in ?? draft.start, rangeDuration = selectedClip ? selectedClip.duration * (selectedClip.speed ?? 1) : draft.duration
  const problem = !source ? status.source_error || 'Chưa có video nguồn trên máy. Ghép hoặc tải video lên trước.'
    : maxLength < 1 ? 'Video quá ngắn để xử lý. Cần ít nhất 1 giây.'
    : !status.available ? status.unavailable_reason || 'Môi trường AI chưa sẵn sàng. Cần kiểm tra model và môi trường ProPainter.'
    : selectedClip && (rangeDuration < 1 || rangeDuration > 30) ? 'Mỗi cảnh xử lý từ 1–30 giây nguồn. Tách cảnh dài trên timeline trước.'
    : !checked ? 'Đang kiểm tra tác vụ…'
    : maskProblem(draft.box) || (!previewReady ? 'Đợi khung hình được nạp trước khi xử lý.' : '')
  const result = history.find(r => r.trial_id === chosen) ?? history[0]
  async function start() {
    if (!source || !draft.box || problem || busy) return
    setPending(true); setError(''); setNotice('')
    const region: EditRegion = { id: regionId, kind: 'blur', ...draft.box, start: rangeStart, end: rangeStart + rangeDuration, strength: 12, color: '#000000', opacity: 1, image_id: null }
    try {
      const revision = timeline ? await timeline.save() : 0
      const body = timeline ? { revision, clip_ids: clipIds, region, dilation: draft.dilation, max_side: draft.maxSide } : {
        settings: { ...settings, mode: 'original', regions: [region] }, region_id: regionId,
        start: draft.start, duration: draft.duration, dilation: draft.dilation, max_side: draft.maxSide, source_fingerprint: source.fingerprint,
      }
      const queued = await fetchAPI<Job>(`/api/videos/${videoId}/editing/propainter${timeline ? '/clips' : ''}`, { method: 'POST', body: JSON.stringify(body) })
      commanded.current = queued.id; terminal.current = null; setJob(queued)
    } catch (cause) { setError(String(cause)); setChecked(false) }
    finally { setPending(false) }
  }
  async function cancel() {
    if (!job || pending) return
    setPending(true); setError('')
    try {
      const stopped = await fetchAPI<Job>(`/api/videos/${videoId}/editing/propainter/cancel?job_id=${job.id}`, { method: 'POST' })
      if (stopped) terminal.current = stopped.id
      commanded.current = null; setJob(stopped)
    } catch (cause) { commanded.current = null; setError(String(cause)); setChecked(false) }
    finally { setPending(false) }
  }
  async function add(results: Result[], restore = false) {
    if (!results.length || !onAddTimeline || adding || busy) return
    setAdding(true); setError(''); setNotice('')
    try { await onAddTimeline(results, restore); setNotice(restore ? 'Đã khôi phục cảnh gốc tại vị trí hiện tại.' : 'Đã thay hình tại vị trí cũ. Thời lượng, nhạc, giọng và phụ đề giữ nguyên. Đóng công cụ để xem hoặc Hoàn tác.') }
    catch (cause) { setError(String(cause)) }
    finally { setAdding(false) }
  }
  const batch = history.filter(r => job?.result?.trial_ids?.includes(r.trial_id) && r.full_resolution)
  return <div className="pp-restore-workspace">
    <div className="pp-restore-intro"><span className="pp-restore-icon"><Eraser size={24} /></span><div><h3>Xóa vật thể bằng AI</h3><p>Khoanh một vật thể nhỏ, AI thử lấp lại nền phía sau. Phù hợp vật thể ít di chuyển, nền đơn giản.</p></div><span className="pp-restore-badge">{timeline ? `Trọn cảnh · ${clipIds?.length ?? 0} cảnh` : `Bản thử · ${draft.maxSide} px`}</span></div>
    <ol className="pp-restore-steps"><li>1. Chọn đoạn</li><li>2. Khoanh vật thể</li><li>3. Xem & dùng kết quả</li></ol>
    {source && maxLength >= 1 && <section className="pp-edit-card pp-restore-compose">
      <div className="pp-restore-controls">
      <h3>1. {timeline ? 'Xử lý các cảnh đã chọn' : 'Chọn đoạn trong video nguồn'}</h3>
      <p>Chọn trên video và khung gốc. Khi đưa kết quả vào timeline, khung dọc/ngang và zoom của bản dựng sẽ được áp dụng sau.</p>
      <fieldset disabled={busy} className="pp-restore-fields">
        <div className="pp-edit-grid">
          {!timeline && <><label>Bắt đầu (giây)<input type="number" min={0} max={Math.max(0, source.duration - draft.duration)} step={.1} value={draft.start} onChange={e => change({ start: Math.max(0, Math.min(source.duration - draft.duration, Number(e.target.value))) })} /></label>
          <label>Độ dài đoạn thử<select value={draft.duration} onChange={e => { const duration = Number(e.target.value); change({ duration, start: Math.min(draft.start, source.duration - duration) }) }}>{[...new Set([1, Math.min(2, maxLength), maxLength])].sort((a, b) => a - b).map(value => <option key={value} value={value}>{value} giây</option>)}</select></label></>}
          <label>Chế độ xử lý<select value={draft.maxSide} onChange={e => change({ maxSide: Number(e.target.value) as 192 | 320 })}><option value={192}>Nhanh · vùng AI 192 px</option><option value={320}>Chi tiết · vùng AI 320 px</option></select></label>
        </div>
        {!timeline && <input aria-label="Tìm đoạn trong video nguồn" type="range" min={0} max={Math.max(0, source.duration - draft.duration)} step={.1} value={draft.start} onChange={e => change({ start: Number(e.target.value) })} />}
        <div className="pp-restore-range"><strong>{clock(rangeStart)} → {clock(rangeStart + rangeDuration)}</strong><span>Video nguồn {clock(source.duration)} · {source.fps_number.toFixed(2)} FPS</span></div>
      </fieldset>
      <details className="pp-restore-advanced"><summary>Tinh chỉnh vùng & mép xóa</summary>
        <fieldset disabled={busy} className="pp-restore-fields pp-edit-grid">
          <label>Nới mép vùng xóa<input type="range" min={1} max={8} step={1} value={draft.dilation} onChange={e => change({ dilation: Number(e.target.value) })} /><span>{draft.dilation} px · tăng nhẹ nếu còn viền vật thể</span></label>
          <Button variant="outline" onClick={() => change({ box: { x: .4, y: .4, width: .2, height: .2 } })}>Đặt vùng giữa ảnh</Button>
          {draft.box && (['x', 'y', 'width', 'height'] as const).map(key => <label key={key}>{({ x: 'Vị trí ngang', y: 'Vị trí dọc', width: 'Chiều rộng', height: 'Chiều cao' })[key]} (%)<input type="number" min={key === 'x' || key === 'y' ? 0 : 1} max={100} step={1} value={Math.round(draft.box![key] * 100)} onChange={e => {
            const box = { ...draft.box!, [key]: Math.max(key === 'x' || key === 'y' ? 0 : .01, Math.min(1, Number(e.target.value) / 100)) }
            box.x = Math.min(box.x, 1 - box.width); box.y = Math.min(box.y, 1 - box.height); change({ box })
          }} /></label>)}
        </fieldset>
      </details>
      <div className="pp-restore-run"><div><strong>{running ? job.stage : timeline ? 'Xử lý trọn cảnh, chia nhỏ tự động' : 'Tạo một đoạn thử riêng'}</strong><p>{problem || 'Vùng đã sẵn sàng. Video nguồn và bản dựng hiện tại được giữ nguyên.'}</p></div>
        <Button disabled={busy || !!problem} onClick={() => void start()}>{busy ? <Loader2 size={16} className="animate-spin" /> : <Eraser size={16} />}{timeline ? `Xóa logo · ${clipIds?.length ?? 0} cảnh` : `Xóa vật thể · ${draft.duration}s`}</Button>
      </div>
      </div>
      <div className="pp-restore-canvas">
      <h3>2. Kéo chuột khoanh vật thể trên ảnh</h3>
      <p>Vùng chọn giữ cố định suốt đoạn, chưa tự bám theo vật thể. Có thể kiểm tra ảnh đầu, giữa và cuối trước khi chạy.</p>
      <div className="pp-edit-actions">{['Đầu đoạn', 'Giữa đoạn', 'Cuối đoạn'].map((label, i) => <Button key={label} variant="outline" disabled={busy} aria-pressed={framePosition === i / 2} onClick={() => setFramePosition(i / 2)}>{label}</Button>)}</div>
      <ProPainterRegionPicker videoId={videoId} settings={settings} sourceUrl={sourceAsset?.url} time={rangeStart + Math.max(0, rangeDuration - 1 / source.fps_number) * framePosition} box={draft.box} disabled={busy} onChange={box => change({ box })} onReady={setPreviewReady} />
      </div>
    </section>}
    {(!source || maxLength < 1 || !status.available) && <div className="pp-edit-card"><p role="alert">{problem}</p><Button variant="outline" onClick={onRetry}>Kiểm tra lại</Button></div>}
    {running && <section className="pp-edit-card pp-restore-progress" role="status"><strong>{job.stage}</strong><progress aria-label="Tiến trình xử lý AI" max={100} value={job.progress} /><p>{job.progress}% theo giai đoạn · Đã chạy {Math.max(0, Math.floor(now / 1000 - job.created_at))} giây. Nạp model có thể mất một lúc.</p><Button variant="outline" disabled={pending} onClick={() => void cancel()}><Square size={14} />Dừng xử lý</Button></section>}
    {(error || job?.error || pollError) && <p className="pp-edit-alert" role="alert">{error || job?.error || pollError}</p>}
    {job?.status === 'CANCELLED' && <p role="status">Đã dừng. Các phần trọn cảnh đã xử lý được giữ để tiếp tục khi bấm Xóa logo với cùng lựa chọn.</p>}
    <section className="pp-edit-card pp-restore-results"><h3><CheckCircle2 size={18} />3. So sánh và dùng kết quả</h3>
      {!result ? <p>Kết quả sẽ hiện ở đây khi hoàn tất và được lưu để mở lại sau.</p> : <>
        <label>Các đoạn đã lưu ({history.length})<select value={result.trial_id} onChange={e => { setChosen(e.target.value); setSide('after'); setNotice('') }}>{history.map(r => <option value={r.trial_id} key={r.trial_id}>{clock(r.start)} · {r.duration.toFixed(1)}s · {new Date(r.created_at * 1000).toLocaleString('vi-VN')}</option>)}</select></label>
        <div className="pp-edit-actions"><Button variant="outline" aria-pressed={side === 'before'} disabled={!result.before_url} onClick={() => setSide('before')}>Trước khi xóa</Button><Button variant="outline" aria-pressed={side === 'after'} onClick={() => setSide('after')}>Sau khi xóa</Button></div>
        <video key={`${result.trial_id}:${side}`} className="pp-trial-video" src={resolveMediaUrl(side === 'before' ? result.before_url! : result.url)} controls playsInline loop preload="metadata" aria-label={side === 'before' ? 'Đoạn gốc để đối chiếu' : 'Kết quả xóa vật thể'} />
        <p>{result.width} × {result.height} · {result.duration.toFixed(2)} giây · Giữ âm gốc. {result.full_resolution ? 'Giữ kích thước gốc, chỉ tái tạo vùng đã khoanh. Xem kỹ trước khi áp dụng.' : 'Bản thử độ phân giải thấp; xử lý trọn cảnh để thay trên timeline.'}</p>
        <div className="pp-edit-actions"><Button asChild variant="outline"><a href={resolveMediaUrl(`${result.url}?download=true`)} download><Download size={14} />Tải MP4</a></Button>{onAddTimeline && result.full_resolution && <>
          <Button disabled={busy || adding} onClick={() => void add([result])}><CheckCircle2 size={14} />Thay cảnh này trên timeline</Button>
          <Button variant="outline" disabled={busy || adding} onClick={() => void add([result], true)}><RotateCcw size={14} />Khôi phục cảnh gốc</Button>
        </>}{onAddTimeline && batch.length > 1 && <Button disabled={busy || adding} onClick={() => void add(batch)}>Áp dụng {batch.length} cảnh đã xử lý</Button>}</div>
        <p>Kết quả lưu trong thư viện. Áp dụng sẽ thay tại vị trí cũ, giữ các lớp liên quan và không kéo dài video.</p>
        {notice && <p role="status">{notice}</p>}
        <details><summary>Thông số xử lý</summary><p>{result.device.toUpperCase()} · AI xử lý {result.inference_seconds.toFixed(1)} giây · RAM tiến trình {(result.peak_rss_bytes / 1024 ** 3).toFixed(2)} GB · MPS gồm cache {(result.peak_mps_driver_bytes / 1024 ** 3).toFixed(2)} GB (có thể dùng chung bộ nhớ).</p></details>
      </>}
    </section>
    <details className="pp-restore-about"><summary>Phạm vi xử lý & thông tin ProPainter</summary><p>{timeline ? 'Mỗi cảnh tối đa 30 giây nguồn; nhiều cảnh chạy lần lượt. AI xử lý các cửa sổ tối đa 60 khung hình có vùng nối, chỉ ghép vùng đã sửa vào hình gốc. Dừng rồi chạy lại cùng lựa chọn sẽ dùng các phần đã xong.' : status.scope} Chưa tự bám theo logo di chuyển. Tốc độ và chất lượng phụ thuộc nội dung, vùng xóa và máy.</p><p>ProPainter — Shangchen Zhou và cộng sự, S-Lab, NTU. <a href="https://github.com/sczhou/ProPainter" target="_blank" rel="noreferrer">Mã nguồn</a> · <a href="https://github.com/sczhou/ProPainter/blob/main/LICENSE" target="_blank" rel="noreferrer">Giấy phép phi thương mại</a>.</p></details>
  </div>
}
