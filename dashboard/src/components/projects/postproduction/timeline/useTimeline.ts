import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { fetchAPI } from '../../../../api/client'
import { usePolling } from '../../../../api/usePolling'
import type { Document, ImportReport, Job, ResultOptions, TimelineState, Visuals } from './types'
import { readHistory, writeHistory } from './history'
import { validate } from './operations'
import { VisualsLoader } from './visualsLoader'
import { nextPreparation } from './preparation'

export function useTimeline(videoId: string, active: boolean, onExport: () => Promise<void>) {
  const path = `/api/videos/${videoId}/timeline`
  const [state, setState] = useState<TimelineState | null>(null), [doc, setDoc] = useState<Document | null>(null)
  const [error, setError] = useState(''), [saving, setSaving] = useState(false), [dirty, setDirty] = useState(false)
  const [importNotice, setImportNotice] = useState('')
  const [pollError, setPollError] = useState('')
  const [saveBlocked, setSaveBlocked] = useState(false)
  const [history, setHistory] = useState(0), [reload, setReload] = useState(0)
  const [historyNotice, setHistoryNotice] = useState('')
  const [job, setJob] = useState<Job | null>(null), [checked, setChecked] = useState(false), [pending, setPending] = useState(false)
  const [visuals, setVisuals] = useState<Record<string, Visuals>>({})
  const current = useRef<Document | null>(null), revision = useRef(0), saved = useRef('')
  const undo = useRef<Document[]>([]), redo = useRef<Document[]>([]), alive = useRef(true)
  const request = useRef<Promise<number> | null>(null), processed = useRef('')
  const loading = useRef<Promise<void> | null>(null), generation = useRef(0), refreshSequence = useRef(0)
  const edits = useRef(0)
  const visualLoader = useRef<VisualsLoader | null>(null)
  const automaticVisuals = useRef(new Set<string>())
  const automaticProxies = useRef(new Set<string>())
  const commandedJob = useRef<string | null>(null)
  const running = job?.status === 'QUEUED' || job?.status === 'PROCESSING'
  useEffect(() => {
    alive.current = true
    const controller = new AbortController()
    const epoch = ++generation.current, before = edits.current
    const loader = new VisualsLoader((id, signal) => fetchAPI<Visuals | null>(`${path}/assets/${id}/visuals`, { signal }), cause => {
      if (alive.current && generation.current === epoch) setError(String(cause))
    })
    visualLoader.current = loader
    const load = (async () => {
      // A reload must read after any outstanding PUT, not race it with a GET.
      while (request.current) { try { await request.current } catch { break } }
      if (controller.signal.aborted) return
      const data = await fetchAPI<TimelineState>(path, { signal: controller.signal })
      if (controller.signal.aborted) return
      if (edits.current !== before) {
        setError('Bản dựng vừa thay đổi trong lúc nạp. Đã giữ các chỉnh sửa mới; bấm nạp lại nếu muốn bỏ chúng.')
        return
      }
      current.current = data.document; revision.current = data.revision; saved.current = JSON.stringify(data.document)
      let restored = { undo: [] as Document[], redo: [] as Document[] }
      try { restored = readHistory(localStorage, videoId, data.document) } catch { /* Local history is optional. */ }
      undo.current = restored.undo; redo.current = restored.redo
      setState(data); setDoc(data.document); setDirty(false); setError(''); setImportNotice(''); setSaveBlocked(false); setHistory(v => v + 1)
    })().catch(cause => { if (!controller.signal.aborted) setError(String(cause)) }).finally(() => {
      if (loading.current === load) loading.current = null
    })
    loading.current = load
    return () => { alive.current = false; controller.abort(); loader.dispose() }
  }, [path, reload, videoId])

  async function refresh(additional: TimelineState['assets'] = []) {
    const epoch = generation.current, sequence = ++refreshSequence.current
    // The catalog includes imported voices only once their document is saved.
    while (request.current) await request.current
    if (!alive.current || epoch !== generation.current || sequence !== refreshSequence.current) return
    const before = current.current
    const data = await fetchAPI<TimelineState>(path)
    data.assets = [...data.assets, ...additional.filter(a => !data.assets.some(b => b.id === a.id))]
    if (!alive.current || epoch !== generation.current || sequence !== refreshSequence.current) return
    // Closing an AI tool refreshes the library, but must retain import warnings.
    setState(old => old ? { ...data, warnings: [...new Set([...old.warnings, ...data.warnings])] } : data)
    if (!data.source_available || !current.current || current.current !== before || current.current.clips.length || JSON.stringify(current.current) !== saved.current || request.current || loading.current) return data
    // A newly prepared source can initialize an untouched empty document.
    current.current = data.document; revision.current = data.revision; saved.current = JSON.stringify(data.document)
    setDoc(data.document)
    return data
  }
  useEffect(() => {
    if (active && current.current) void refresh().catch(cause => setError(String(cause)))
  }, [active, path]) // eslint-disable-line react-hooks/exhaustive-deps

  function persistHistory(snapshot: Document) {
    let stored = false
    try { stored = writeHistory(localStorage, videoId, snapshot, { undo: undo.current, redo: redo.current }) } catch { /* Storage can be disabled. */ }
    setHistoryNotice(stored ? '' : 'Trình duyệt không cho lưu lịch sử: hoàn tác chỉ dùng được trong lần mở này. Bản dựng vẫn lưu trên máy.')
  }
  async function save(): Promise<number> {
    while (loading.current || request.current) await (loading.current ?? request.current)
    if (!alive.current) return revision.current
    const snapshot = current.current
    if (!snapshot) return revision.current
    if (saved.current === JSON.stringify(snapshot)) { persistHistory(snapshot); setError(''); setSaveBlocked(false); return revision.current }
    setSaving(true)
    const pending = fetchAPI<{ revision: number; document: Document }>(path, { method: 'PUT', body: JSON.stringify({ revision: revision.current, document: snapshot }) }).then(data => {
      revision.current = data.revision; saved.current = JSON.stringify(data.document)
      if (current.current === snapshot) { current.current = data.document; setDoc(data.document) }
      if (alive.current) {
        setDirty(JSON.stringify(current.current) !== saved.current); setSaveBlocked(false); setError('')
        if (JSON.stringify(current.current) === saved.current) persistHistory(data.document)
      }
      return data.revision
    }).catch(cause => { if (alive.current) { setError(String(cause)); setSaveBlocked(true) } throw cause }).finally(() => {
      if (request.current === pending) request.current = null
      if (alive.current) setSaving(false)
    })
    request.current = pending
    await pending
    // A navigation/export must flush changes made during the first request too.
    return JSON.stringify(current.current) !== saved.current ? save() : revision.current
  }
  useEffect(() => {
    if (!dirty || saveBlocked) return
    const timer = setTimeout(() => { void save().catch(() => {}) }, 700)
    return () => clearTimeout(timer)
  }, [doc, dirty, saveBlocked, path]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (dirty) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  function apply(next: Document, remember = true) {
    if (JSON.stringify(next) === JSON.stringify(current.current)) return
    edits.current++
    if (remember && current.current) { undo.current = [...undo.current.slice(-49), current.current]; redo.current = [] }
    current.current = next; setDoc(next); setDirty(JSON.stringify(next) !== saved.current); setHistory(v => v + 1); setError('')
    if (JSON.stringify(next) === saved.current) persistHistory(next)
  }
  function travel(back: boolean) {
    const from = back ? undo.current : redo.current, to = back ? redo.current : undo.current
    const next = from.at(-1)
    if (!next || !current.current) return
    try { validate(next, state?.assets ?? []) } catch (cause) { setError(`Không thể hoàn tác/làm lại: ${String(cause)}`); return }
    from.pop()
    to.push(current.current); apply(next, false)
  }
  usePolling<Job | null>(!checked || running || active ? `${path}/job` : null, running ? 1000 : 4000,
    data => {
      // An old in-flight poll must not replace the job just started by a button.
      if (commandedJob.current && data?.id !== commandedJob.current) return
      if (data && !['QUEUED', 'PROCESSING'].includes(data.status)) commandedJob.current = null
      setJob(data); setChecked(true); setPollError('')
    }, cause => setPollError(String(cause)))
  const complete = useEffectEvent(async (data: Job) => {
    if (data.kind === 'timeline-export') await onExport()
    await refresh()
    if (data.kind === 'timeline-visuals') await loadVisuals(data.result?.asset_ids ?? [], true)
  })
  useEffect(() => {
    if (job?.status === 'COMPLETED' && (job.kind.startsWith('timeline-') || job.kind === 'pacing-prepare') && processed.current !== job.id) {
      processed.current = job.id; void complete(job).catch(cause => setError(String(cause)))
    }
  }, [job])
  async function loadVisuals(ids: string[], retry = false) {
    const loader = visualLoader.current
    if (!loader) return
    const result = await loader.load(ids, retry)
    if (alive.current && visualLoader.current === loader && Object.keys(result).length) setVisuals(old => {
      if (Object.entries(result).every(([id, value]) => old[id] === value)) return old
      const next = { ...old }
      Object.assign(next, result)
      return next
    })
  }
  useEffect(() => {
    if (!state) return
    const used = new Set(doc?.clips.map(c => c.asset_id))
    const ids = state.assets.filter(a => a.visuals_ready && !visuals[a.id] && used.has(a.id)).map(a => a.id)
    if (!ids.length) return
    void loadVisuals(ids, true).catch(cause => setError(String(cause)))
  }, [state, doc, visuals, reload])
  async function action(suffix: string, body?: unknown, alternate?: string) {
    setPending(true); setError('')
    try {
      const data = await fetchAPI<Job>(alternate ?? `${path}/${suffix}`, { method: 'POST', ...(body ? { body: JSON.stringify(body) } : {}) })
      commandedJob.current = ['QUEUED', 'PROCESSING'].includes(data.status) ? data.id : null
      setJob(data)
    } catch (cause) { setError(String(cause)) } finally { setPending(false) }
  }
  async function render(preview: boolean, range?: { start: number; end: number }) {
    setPending(true)
    try { const rev = await save(); await action('render', { revision: rev, preview, ...range }) } catch (cause) { setError(String(cause)) } finally { setPending(false) }
  }
  async function importResults(options: ResultOptions = {}) {
    setPending(true);setError('');setImportNotice('')
    try {
      const rev=await save()
      const before=JSON.stringify(current.current)
      const data=await fetchAPI<{document:Document;assets:TimelineState['assets'];warnings:string[];added:number;report:ImportReport}>(`${path}/import-results`,{method:'POST',body:JSON.stringify({revision:rev,...options})})
      if(!alive.current)return
      if(JSON.stringify(current.current)!==before)throw new Error('Bản dựng đã đổi trong khi nạp kết quả. Bấm nạp lại để giữ các chỉnh sửa mới.')
      ++refreshSequence.current // Discard catalog reads started before this import.
      setState(old=>old?{...old,assets:[...old.assets.filter(a=>!data.assets.some(b=>b.id===a.id)),...data.assets],warnings:data.warnings}:old)
      apply(data.document)
      await save()
      setImportNotice(data.report.voices ? `Đã nạp giọng ${data.report.voices} đoạn${data.report.fitted ? ` · ${data.report.fitted} đoạn được tăng tốc nhẹ` : ''}${data.report.blocked.length ? ` · ${data.report.blocked.length} đoạn cần xử lý thêm` : ''}.` : data.added ? 'Đã cập nhật kết quả AI trên timeline.' : 'Đã kiểm tra kết quả AI đã lưu; không có thay đổi mới.')
      return data.report
    }catch(cause){setError(String(cause));throw cause}finally{setPending(false)}
  }
  async function handoff(exportId:string) {
    await save()
    return fetchAPI<{video_id:string}>(`${path}/exports/${exportId}/handoff`,{method:'POST'})
  }
  const prepareNext = useEffectEvent((kind: string, ids: string[]) => action(kind, { asset_ids: ids }))
  useEffect(() => {
    if (!active || !checked || running || pending || !doc || !state) return
    const next = nextPreparation(doc,state.assets,automaticVisuals.current,automaticProxies.current)
    if (!next) return
    const attempted = (next.kind==='visuals'?automaticVisuals:automaticProxies).current
    next.ids.forEach(id=>attempted.add(id))
    void prepareNext(next.kind,next.ids)
  }, [active, checked, running, pending, doc, state])
  return { state, doc, error: error || pollError, setError, dirty, saving, saveBlocked, save, apply, refresh, visuals, job, running, pending, historyNotice, importNotice, revision: revision.current,
    busy: running || pending, reload: () => setReload(v => v + 1),
    undo: () => travel(true), redo: () => travel(false), canUndo: history >= 0 && undo.current.length > 0, canRedo: history >= 0 && redo.current.length > 0,
    render, visualize: (ids: string[]) => action('visuals', { asset_ids: [...new Set(ids)] }),
    proxies: (ids: string[])=>action('proxies',{asset_ids:ids.slice(0,8)}),importResults,handoff,getDocument:()=>current.current,
    prepare: () => action('', undefined, `/api/videos/${videoId}/pacing/prepare`),
    cancel: () => job && action(`jobs/${job.id}/cancel`),
  }
}
