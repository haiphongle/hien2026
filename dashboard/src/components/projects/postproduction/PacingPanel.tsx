import { MAX_DURATION } from '../../../lib/mediaLimits'
import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { Download, Loader2, Play, Plus, RefreshCw, Save, Scissors, Square, Trash2, Upload, Volume2 } from 'lucide-react'
import { fetchAPI, resolveMediaUrl } from '../../../api/client'
import { usePolling } from '../../../api/usePolling'
import { Button } from '../../ui/button'

interface Asset { id: string; name: string; duration: number; builtin: boolean; url: string }
interface Cut { id: string; start: number; end: number }
interface Draft { source: string; plan_id: string | null; cut_ids: string[]; effects: { id: string; asset_id: string; start: number; volume: number }[]; base_volume: number }
interface Plan { id: string; duration: number; cuts: Cut[]; warnings: string[]; method: string; threshold: number; minimum: number; padding: number }
interface State { revision: number; draft: Draft; plan: Plan | null; assets: Asset[]; sources: { id: string; name: string; url: string; duration?: number }[]; source_available: boolean; speech_available: boolean }
interface Job { id: string; kind: string; status: string; progress: number; stage: string; error?: string; result?: { url?: string; duration?: number } }

const seconds = (n: number) => `${Math.floor(n / 60)}:${(n % 60).toFixed(2).padStart(5, '0')}`

export function PacingPanel({ videoId, active, blocked, onExport }: { videoId: string; active: boolean; blocked: boolean; onExport: (url: string) => Promise<void> }) {
  const path = `/api/videos/${videoId}/pacing`
  const [state, setState] = useState<State | null>(null), [draft, setDraft] = useState<Draft | null>(null)
  const [job, setJob] = useState<Job | null>(null), [checked, setChecked] = useState(false)
  const [pending, setPending] = useState(false), [error, setError] = useState(''), [message, setMessage] = useState('')
  const [method, setMethod] = useState('level'), [threshold, setThreshold] = useState(-40), [minimum, setMinimum] = useState(.7), [padding, setPadding] = useState(.18)
  const [page, setPage] = useState(0), [time, setTime] = useState(0), [skipCuts, setSkipCuts] = useState(true), [duration, setDuration] = useState(0)
  const player = useRef<HTMLVideoElement>(null), input = useRef<HTMLInputElement>(null), processed = useRef('')
  const running = job?.status === 'QUEUED' || job?.status === 'PROCESSING'
  const ownJob = job?.kind.startsWith('pacing-')
  const busy = blocked || pending || running
  const plan = state?.plan?.id === draft?.plan_id && state?.plan ? state.plan : null
  const source = state?.sources.find(s => s.id === draft?.source)
  const selected = plan?.cuts.filter(c => draft?.cut_ids.includes(c.id)) ?? []
  const sourceDuration = plan?.duration ?? source?.duration ?? duration
  const removed = selected.reduce((n, cut) => n + cut.end - cut.start, 0)
  const outputDuration = Math.max(0, sourceDuration - removed)
  const outputTime = Math.max(0, time - selected.reduce((n, cut) => n + Math.max(0, Math.min(time, cut.end) - cut.start), 0))
  const dirty = Boolean(draft && state && JSON.stringify(draft) !== JSON.stringify(state.draft))
  const limit = 15, currentPage = Math.min(page, Math.max(0, Math.ceil((plan?.cuts.length ?? 0) / limit) - 1))

  async function load() {
    const data = await fetchAPI<State>(path)
    setState(data); setDraft(data.draft)
    if (data.plan) { setMethod(data.plan.method); setThreshold(data.plan.threshold); setMinimum(data.plan.minimum); setPadding(data.plan.padding) }
  }
  useEffect(() => { void load().catch(cause => setError(String(cause))) }, [path]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!active) return
    void fetchAPI<State>(path).then(data => setState(previous => previous ? { ...previous, sources: data.sources, assets: data.assets, source_available: data.source_available, speech_available: data.speech_available } : data)).catch(cause => setError(String(cause)))
  }, [active, path])
  usePolling<Job | null>(!checked || running ? `${path}/job` : null, 1000, data => { setChecked(true); setJob(data) }, cause => setError(String(cause)))
  const complete = useEffectEvent(async (data: Job) => {
    if (data.kind === 'pacing-analysis' || data.kind === 'pacing-prepare') await load()
    else if (data.result?.url) await onExport(data.result.url)
  })
  useEffect(() => {
    if (job?.status === 'COMPLETED' && job.kind.startsWith('pacing-') && processed.current !== job.id) {
      processed.current = job.id
      void complete(job).catch(cause => setError(String(cause)))
    }
  }, [job])

  async function save() {
    if (!draft || !state) throw new Error('Chưa nạp lựa chọn.')
    const data = await fetchAPI<{ revision: number; draft: Draft }>(path, { method: 'PUT', body: JSON.stringify({ revision: state.revision, draft }) })
    setState({ ...state, ...data }); setDraft(data.draft)
    return data.revision
  }
  async function action(kind: 'analyze' | 'preview' | 'export' | 'save') {
    if (!draft) return
    setPending(true); setError(''); setMessage(''); player.current?.pause()
    try {
      if (kind === 'analyze') {
        setJob(await fetchAPI<Job>(`${path}/analyze`, { method: 'POST', body: JSON.stringify({ source: draft.source, method, threshold, minimum, padding }) }))
      } else {
        const revision = await save()
        if (kind === 'save') setMessage('Đã lưu lựa chọn và hiệu ứng. Mở lại video sẽ giữ nguyên.')
        else setJob(await fetchAPI<Job>(`${path}/render`, { method: 'POST', body: JSON.stringify({ revision, preview: kind === 'preview' }) }))
      }
    } catch (cause) { setError(String(cause)); setChecked(false) }
    finally { setPending(false) }
  }
  async function upload(file: File) {
    setPending(true); setError('')
    try {
      const body = new FormData(); body.append('file', file)
      const asset = await fetchAPI<Asset>(`${path}/assets`, { method: 'POST', body })
      setState(previous => previous ? { ...previous, assets: [...previous.assets, asset] } : previous)
      setMessage(`Đã lưu ${asset.name} vào thư viện của video. Bấm “Thêm” để đặt mốc.`)
    } catch (cause) { setError(String(cause)) }
    finally { setPending(false) }
  }
  function changeSource(id: string) {
    if (!draft) return
    player.current?.pause(); setTime(0); setDuration(0); setPage(0)
    setDraft({ ...draft, source: id, plan_id: null, cut_ids: [], effects: [] })
    setMessage('Đổi nguồn đã xóa lựa chọn cắt và mốc hiệu ứng trong bản nháp. Phân tích lại nguồn mới.')
  }
  function seek(at: number) {
    if (player.current) { player.current.currentTime = at; setTime(at) }
  }

  if (!state || !draft) return <section className="pp-editing"><h3>Nhịp dựng & hiệu ứng âm thanh</h3><p>{error || 'Đang nạp…'}</p></section>
  return <div className="pp-edit-stack pp-pacing">
    <header className="pp-edit-card pp-edit-toolbar"><div><h3><Scissors size={19} /> Nhịp dựng & hiệu ứng</h3><p>Cắt khoảng nghỉ, giữ nhịp nói và nhấn những khoảnh khắc đáng nhớ.</p></div>
      <Button variant="outline" disabled={busy || !dirty} onClick={() => void action('save')}><Save size={15} />Lưu lựa chọn</Button>
      <Button variant="outline" disabled={busy} onClick={() => void load().then(() => setError('')).catch(cause => setError(String(cause)))}><RefreshCw size={15} />Nạp lại</Button></header>

    <section className="pp-edit-card"><h3>1. Chọn video để dựng</h3>
      <label>Video đầu vào<select value={draft.source} disabled={busy} onChange={e => changeSource(e.target.value)}>{state.sources.map(s => <option key={s.id} value={s.id} disabled={s.id === 'original' && !state.source_available}>{s.name}</option>)}</select></label>
      <p>Chọn bản xuất đã ghép giọng, nhạc và chữ để cắt tất cả cùng nhau. Video nguồn dùng âm thanh đang có trong file. Thiết lập chưa xuất ở tab khác chưa được áp dụng tại đây.</p>
      {!state.source_available && <Button variant="outline" disabled={busy} onClick={() => { setPending(true); void fetchAPI<Job>(`${path}/prepare`, { method: 'POST' }).then(setJob).catch(cause => setError(String(cause))).finally(() => setPending(false)) }}><Download size={15} />Nạp video nguồn để dựng</Button>}
      {source && (draft.source !== 'original' || state.source_available) && <><video key={source.url} ref={player} src={resolveMediaUrl(source.url)} controls preload="metadata" aria-label="Xem nhanh nhịp cắt"
        onLoadedMetadata={e => setDuration(e.currentTarget.duration)} onTimeUpdate={e => {
          const value = e.currentTarget.currentTime
          const cut = skipCuts && !e.currentTarget.paused ? selected.find(c => value >= c.start && value < c.end) : null
          if (cut) e.currentTarget.currentTime = cut.end
          setTime(cut ? cut.end : value)
        }} />
        <label className="pp-pacing-check"><input type="checkbox" checked={skipCuts} onChange={e => setSkipCuts(e.target.checked)} />Bỏ qua các đoạn đã chọn khi phát thử</label>
        <p>Nguồn: {seconds(time)} · Sau cắt: {seconds(outputTime)}. Đây là xem nhanh; bản dựng bên dưới áp dụng hiệu ứng âm thanh.</p></>}
    </section>

    <section className="pp-edit-card"><h3>2. Tìm và chọn khoảng nghỉ</h3>
      <div className="pp-edit-grid"><label>Cách tìm<select value={method} disabled={busy} onChange={e => setMethod(e.target.value)}><option value="level">Độ nhỏ của âm thanh</option><option value="speech" disabled={!state.speech_available || draft.source !== 'original'}>Khoảng trống giữa lời thoại đã lưu</option></select></label>
        <label>Ngưỡng im lặng: {threshold} dB<input type="range" min={-65} max={-15} value={threshold} disabled={busy || method === 'speech'} onChange={e => setThreshold(Number(e.target.value))} /><small>Gần −65: giữ tiếng nhỏ. Gần −15: cắt mạnh hơn.</small></label>
        <label>Khoảng nghỉ tối thiểu (giây)<input type="number" min={.2} max={5} step={.1} value={minimum} disabled={busy} onChange={e => setMinimum(Number(e.target.value))} /></label>
        <label>Giữ đệm mỗi đầu (giây)<input type="number" min={.05} max={.8} step={.01} value={padding} disabled={busy} onChange={e => setPadding(Number(e.target.value))} /></label></div>
      <div className="pp-edit-actions"><Button disabled={busy || !checked || (draft.source === 'original' && !state.source_available)} onClick={() => void action('analyze')}><Scissors size={15} />Phân tích khoảng nghỉ</Button>
        {plan && <><Button variant="outline" disabled={busy} onClick={() => setDraft({ ...draft, cut_ids: plan.cuts.map(c => c.id) })}>Chọn tất cả</Button><Button variant="outline" disabled={busy} onClick={() => setDraft({ ...draft, cut_ids: [] })}>Giữ tất cả</Button></>}</div>
      <p>Phân tích lại sẽ thay danh sách cắt và xóa các mốc hiệu ứng cũ. Video có nhạc liên tục nên thử cách tìm theo lời thoại đã lưu; kiểm tra mốc SRT trước.</p>
      {plan?.warnings.map(w => <p className="pp-note" key={w}>{w}</p>)}
      {plan && <><div className="pp-pacing-summary"><strong>{selected.length} / {plan.cuts.length} đoạn cắt</strong><span>Giảm {removed.toFixed(2)}s</span><span>{sourceDuration.toFixed(2)}s → khoảng {outputDuration.toFixed(2)}s</span></div>
        <div className="pp-pacing-cuts">{plan.cuts.slice(currentPage * limit, (currentPage + 1) * limit).map(cut => <div key={cut.id}><label className="pp-pacing-check"><input type="checkbox" disabled={busy} checked={draft.cut_ids.includes(cut.id)} onChange={e => setDraft({ ...draft, cut_ids: e.target.checked ? [...draft.cut_ids, cut.id] : draft.cut_ids.filter(id => id !== cut.id) })} />{seconds(cut.start)} → {seconds(cut.end)}<small>{(cut.end - cut.start).toFixed(2)}s</small></label><Button variant="ghost" onClick={() => { setSkipCuts(false); seek(Math.max(0, cut.start - .3)) }}><Play size={13} />Nghe đoạn này</Button></div>)}</div>
        {plan.cuts.length > limit && <div className="pp-edit-actions"><Button variant="outline" disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Trước</Button><span>Trang {currentPage + 1} / {Math.ceil(plan.cuts.length / limit)}</span><Button variant="outline" disabled={(currentPage + 1) * limit >= plan.cuts.length} onClick={() => setPage(currentPage + 1)}>Sau</Button></div>}</>}
    </section>

    <section className="pp-edit-card"><h3>3. Chèn hiệu ứng âm thanh</h3><p>Mốc hiệu ứng tính trên video <strong>sau khi cắt</strong>. Nếu đổi đoạn cắt, kiểm tra lại các mốc. Tiếng cười, vỗ tay hoặc giọng “wow” có thể tải lên từ file của bạn.</p>
      <div className="pp-edit-actions"><Button variant="outline" disabled={busy} onClick={() => input.current?.click()}><Upload size={15} />Tải hiệu ứng âm thanh</Button><small>MP3, WAV, M4A, OGG, FLAC, AAC · tối đa 16 MB / 30 giây</small></div>
      <input hidden ref={input} type="file" accept=".mp3,.wav,.m4a,.ogg,.flac,.aac" onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) void upload(file) }} />
      <div className="pp-sfx-library">{state.assets.map(asset => <article key={asset.id}><strong>{asset.name}</strong><small>{asset.duration.toFixed(2)}s · {asset.builtin ? 'Mẫu tổng hợp sẵn' : 'File đã tải lên'}</small><audio src={resolveMediaUrl(asset.url)} controls preload="none" aria-label={`Nghe ${asset.name}`} /><Button variant="outline" disabled={busy || draft.effects.length >= 40} onClick={() => setDraft({ ...draft, effects: [...draft.effects, { id: crypto.randomUUID(), asset_id: asset.id, start: Math.max(0, Math.min(Number(outputTime.toFixed(2)), outputDuration - asset.duration)), volume: .5 }] })}><Plus size={14} />Thêm tại {seconds(outputTime)}</Button></article>)}</div>
      {draft.effects.map((event, index) => <div className="pp-sfx-event" key={event.id}><strong>{index + 1}. {state.assets.find(a => a.id === event.asset_id)?.name ?? 'Hiệu ứng'}</strong>
        <label>Mốc bắt đầu sau cắt (s)<input type="number" min={0} max={outputDuration || MAX_DURATION} step={.05} value={event.start} disabled={busy} onChange={e => setDraft({ ...draft, effects: draft.effects.map(item => item.id === event.id ? { ...item, start: Number(e.target.value) } : item) })} /></label>
        <label>Âm lượng: {Math.round(event.volume * 100)}%<input type="range" min={0} max={2} step={.05} disabled={busy} value={event.volume} onChange={e => setDraft({ ...draft, effects: draft.effects.map(item => item.id === event.id ? { ...item, volume: Number(e.target.value) } : item) })} /></label>
        <Button variant="ghost" disabled={busy} aria-label={`Xóa hiệu ứng ${index + 1}`} onClick={() => setDraft({ ...draft, effects: draft.effects.filter(item => item.id !== event.id) })}><Trash2 size={15} /></Button></div>)}
      <label><Volume2 size={14} /> Âm lượng video đầu vào: {Math.round(draft.base_volume * 100)}%<input type="range" min={0} max={2} step={.05} disabled={busy} value={draft.base_volume} onChange={e => setDraft({ ...draft, base_volume: Number(e.target.value) })} /></label>
    </section>

    <section className="pp-edit-card"><h3>4. Xem thử và lưu bản dựng</h3><p>Bản mới giữ hình, tiếng và phụ đề đã gắn vào video đầu vào cùng nhịp cắt. Kịch bản và video nguồn vẫn được lưu để chỉnh tiếp. Bản xuất xuất hiện trong “Khung hình & chữ → Các bản xuất đã lưu”.</p>
      <div className="pp-edit-actions"><Button variant="outline" disabled={busy || !checked || !sourceDuration} onClick={() => void action('preview')}><Play size={15} />Dựng xem thử 12 giây đầu</Button><Button disabled={busy || !checked || !sourceDuration} onClick={() => void action('export')}><Scissors size={15} />Xuất bản dựng mới</Button></div>
      {ownJob && <><p role="status">{running && <Loader2 size={14} className="animate-spin" />} {job?.stage} · {job?.progress}%</p>{running && <><progress max={100} value={job?.progress ?? 0} /><Button variant="outline" onClick={() => void fetchAPI<Job>(`${path}/jobs/${job!.id}/cancel`, { method: 'POST' }).then(setJob).catch(cause => setError(String(cause)))}><Square size={13} />Dừng tác vụ</Button></>}
        {job?.status === 'COMPLETED' && job.result?.url && <><p>{dirty ? 'Video dưới đây là bản dựng trước; dựng lại để nghe các thay đổi mới.' : 'Bản dựng đã lưu với các lựa chọn tại thời điểm xuất.'}</p><video key={job.result.url} src={resolveMediaUrl(job.result.url)} controls preload="metadata" aria-label="Bản dựng nhịp và hiệu ứng" /><Button asChild variant="outline"><a href={resolveMediaUrl(job.result.url)} download><Download size={15} />Tải bản dựng</a></Button></>}</>}
      {running && !ownJob && <p>Video đang được xử lý ở mục khác. Đợi hoàn tất trước khi dựng.</p>}
      {dirty && <p className="pp-note">Có thay đổi chưa lưu. Nút xem thử và xuất sẽ lưu lựa chọn trước khi dựng.</p>}
      {message && <p role="status">{message}</p>}
      {(error || (ownJob && job?.error)) && <p role="alert" className="pp-note">{error || job?.error}</p>}
    </section>
  </div>
}
