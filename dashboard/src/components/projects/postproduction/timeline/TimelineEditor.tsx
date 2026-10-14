import { useEffect, useMemo, useRef, useState } from 'react'
import { ArrowDownToLine, ArrowLeft, ArrowRight, Check, Copy, Eye, EyeOff, Film, GripVertical, Link, Lock, Magnet, Maximize2, Minimize2, Music2, Pause, Play, Plus, Redo2, RefreshCw, Save, Scissors, Square, Trash2, Type, Undo2, Unlink, Unlock, Upload, Volume2, VolumeX, Waves } from 'lucide-react'
import { fetchAPI, resolveMediaUrl } from '../../../../api/client'
import type { AITool, Asset, Clip, Document, TimelineController } from './types'
import * as op from './operations'
import { TimelinePreview } from './TimelinePreview'
import { clock } from './operations'
import { ClipVisual } from './ClipVisual'
import { useTimeline } from './useTimeline'
import { ExportVersionsPanel } from '../ExportVersionsPanel'
import { useLibraryUpload } from './useLibraryUpload'
import type { UploadKind } from './useLibraryUpload'
import { SilencePanel } from './SilencePanel'
import type { TimeRange } from './silence'
import { AudioHandles } from './AudioHandles'
import { LayerInspector } from './LayerInspector'
import { FrameInspector } from './FrameInspector'
import { applyRestorations } from './restoreResult'
import { TextLibrary } from './TextLibrary'
import { applyReviewPreset, insertOverlay } from './videoOverlay'
import { DuckingControls } from './DuckingControls'
import { VideoLayerInspector } from './VideoLayerInspector'
import { VideoLibraryUpload } from './VideoLibraryUpload'
import { MediaLibraryActions, type LibraryChange } from '../MediaLibraryActions'
import { StickmanActorInspector } from './StickmanActorInspector'
import { TRANSITIONS, setTransition, transitionJoins } from './transitions'
import { TransitionControls } from './TransitionControls'
import './timeline.css'

type Gesture = { base: Document; ids: string[]; id: string; x: number; kind: 'move' | 'left' | 'right'; next: Document }
type Marquee = { left: number; top: number; width: number; height: number }
type MarqueeGesture = { pointerId: number; clientX: number; clientY: number; additive: boolean; baseSelection: string[]; moved: boolean }

function Field({ label, value, step = .01, disabled, commit }: { label: string; value: number; step?: number; disabled?: boolean; commit: (n: number) => void }) {
  return <label className="tl-field"><span>{label}</span><input key={value} type="number" defaultValue={Number(value.toFixed(4))} step={step} disabled={disabled}
    onBlur={e => { const n = Number(e.target.value); if (e.target.value.trim() && Number.isFinite(n) && n !== value) commit(n); e.currentTarget.value = String(Number(value.toFixed(4))) }} onKeyDown={e => { if (e.key === 'Enter') e.currentTarget.blur() }} /></label>
}

export function TimelineEditor({ videoId, projectId, title, active, blocked, onExport, registerController,onOpenAI,onHandoff,onLibraryChanged }: { videoId: string; projectId: string; title: string; active: boolean; blocked: boolean; onExport: () => Promise<void>; registerController: (controller: TimelineController) => void;onOpenAI:(tool:AITool)=>void;onHandoff:(id:string)=>Promise<void>;onLibraryChanged:(change:LibraryChange)=>Promise<void> }) {
  const editor = useTimeline(videoId, active, onExport)
  const upload = useLibraryUpload(videoId, projectId, editor.refresh, editor.setError)
  const [libraryTab, setLibraryTab] = useState<'media' | 'text'>('media')
  const [uploadKind, setUploadKind] = useState<UploadKind>('music')
  const uploadInput = useRef<HTMLInputElement>(null)
  const [selection, setSelection] = useState<string[]>([]), [time, setTime] = useState(0), [playing, setPlaying] = useState(false)
  const [scale, setScale] = useState(36), [snapping, setSnapping] = useState(true), [scroll, setScroll] = useState(0), [viewport, setViewport] = useState(700)
  const [previewDoc, setPreviewDoc] = useState<Document | null>(null), [dragNote, setDragNote] = useState('')
  const [filter, setFilter] = useState('all'), [search, setSearch] = useState(''), [resultOpen, setResultOpen] = useState(false)
  const [focus, setFocus] = useState(false), [libraryWidth, setLibraryWidth] = useState(220), [inspectorWidth, setInspectorWidth] = useState(220)
  const [range,setRange]=useState<{start:number;end:number}|null>(null),[selectRange,setSelectRange]=useState(false),[threshold,setThreshold]=useState(-35),[minimum,setMinimum]=useState(.4),[silenceOpen,setSilenceOpen]=useState(false),[handoffBusy,setHandoffBusy]=useState(false)
  const [timelineHeight,setTimelineHeight]=useState(260)
  const [inspector, setInspector] = useState<'clip' | 'frame'>('clip')
  const [transitionId, setTransitionId] = useState<string | null>(null)
  const stackGesture=useRef<{y:number;height:number}|null>(null)
  const rangeBegin=useRef<number|null>(null),logoInput=useRef<HTMLInputElement>(null)
  const panelGesture = useRef<{ x: number; width: number; side: 'library' | 'inspector' } | null>(null)
  const board = useRef<HTMLDivElement>(null), canvas = useRef<HTMLDivElement>(null), root = useRef<HTMLDivElement>(null), gesture = useRef<Gesture | null>(null)
  const marqueeGesture = useRef<MarqueeGesture | null>(null)
  const [marquee, setMarquee] = useState<Marquee | null>(null)
  const doc = previewDoc ?? editor.doc
  const assets = useMemo(() => editor.state?.assets ?? [], [editor.state?.assets])
  const selected = doc && !transitionId ? op.members(doc, selection) : []
  const clip = transitionId ? undefined : doc?.clips.find(c => selection.includes(c.id)), asset = assets.find(a => a.id === clip?.asset_id)
  const duration = doc ? op.length(doc) : 0
  const busy = blocked || editor.busy
  const visibleLibrary = assets.filter(a => (filter === 'all' || a.kind === filter || a.category === filter) && a.name.toLowerCase().includes(search.toLowerCase()))
  const joins = useMemo(() => doc ? transitionJoins(doc) : [], [doc])
  const selectedJoin = joins.find(j => j.next.id === transitionId)
  const hasTransitions = !!doc?.clips.some(c => c.transition)

  useEffect(() => { registerController({ save: editor.save, refresh: editor.refresh, importResults: editor.importResults, getDocument: editor.getDocument, getAssets: () => assets,
    selectScene: sceneId => {
      const current = editor.getDocument()
      const picture = current?.clips.find(c => current.tracks.find(t => t.id === c.track_id)?.kind === 'video' && assets.find(a => a.id === c.asset_id)?.ref.startsWith(`stickman:${sceneId}:`))
      if (picture) { setPlaying(false); setTime(picture.start); setSelection([picture.id]); setInspector('clip') }
    },
    getSelectedVideoIds: () => {
      const current = editor.getDocument(), track = current?.tracks.find(t => t.kind === 'video')
      const pictures = current?.clips.filter(c => c.track_id === track?.id) ?? []
      const chosen = pictures.filter(c => selection.includes(c.id))
      return (chosen.length ? chosen : pictures.filter(c => c.start <= time && op.end(c) > time).slice(0, 1)).map(c => c.id)
    },
    addRestoration: (results, restore = false) => applyRestorations(editor, results, restore, (at, ids) => {
      setPlaying(false); setTime(at); setSelection(ids)
    }), addMusic: async filename => {
    const state = await editor.refresh()
    const a = state?.assets.find(a => a.ref === `music:${filename}` && !a.missing)
    const current = editor.getDocument()
    if (!a || !current) throw new Error('Không đọc được nhạc đã lưu. Nạp lại thư viện rồi thử lại.')
    // The complete-song asset can be longer than the edit. Add a bounded layer
    // at zero, rather than lengthening the video by appending the song.
    const next = op.insert(current, a, 0), created = next.clips.at(-1)!
    created.duration = Math.min(created.duration, op.length(current) || created.duration)
    editor.apply(op.validate(next, state!.assets)); await editor.save()
  } }) }, [registerController, editor, assets, selection, time])
  useEffect(() => {
    const element = board.current
    if (!element) return
    const observer = new ResizeObserver(() => setViewport(element.clientWidth))
    observer.observe(element); return () => observer.disconnect()
  }, [!!doc]) // eslint-disable-line react-hooks/exhaustive-deps
  const stopOnInactive = useRef(playing)
  useEffect(() => { stopOnInactive.current = playing }, [playing])
  useEffect(() => {
    if (!active && stopOnInactive.current) queueMicrotask(() => setPlaying(false))
  }, [active])
  useEffect(() => {
    if (editor.doc) {
      const total = op.length(editor.doc)
      queueMicrotask(() => setTime(at => Math.min(at, total)))
    }
  }, [editor.doc])
  function seek(n: number) { setPlaying(false); setTime(Math.max(0, Math.min(duration, doc ? op.frame(n, doc.fps) : n))) }
  function commit(next: Document, available = assets) {
    try { editor.apply(op.validate(next, available)); editor.setError(''); setPlaying(false); setTime(at => Math.min(at, op.length(next))); return true } catch (cause) { editor.setError(cause instanceof Error ? cause.message : String(cause)); return false }
  }
  function cutPauses(ranges: TimeRange[]) {
    if (!editor.doc || busy) return false
    try {
      const next = op.cutRanges(editor.doc, ranges)
      if (!commit(next)) return false
      setRange(null); setSelection([]); setTime(Math.min(ranges[0]?.start ?? 0, op.length(next)))
      return true
    } catch (cause) { editor.setError(String(cause)); return false }
  }
  function showPause(range: TimeRange, play: boolean) {
    setRange(range); seek(range.start)
    if (board.current) board.current.scrollLeft = Math.max(0, range.start * scale - viewport / 3)
    if (play) setPlaying(true)
  }
  function action(fn: (d: Document) => Document) {
    if (!editor.doc) return false
    try { return commit(fn(editor.doc)) } catch (cause) { editor.setError(cause instanceof Error ? cause.message : String(cause)); return false }
  }
  function removeSelected(ripple = false) {
    if (selectedJoin) { action(d => setTransition(d, selectedJoin.next.id, null)); return }
    if (action(d => op.remove(d, selection, ripple))) setSelection([])
  }
  function splitSelected() {
    if (!editor.doc) return
    try {
      const result = op.splitSelection(editor.doc, selection, time)
      if (commit(result.document)) { setSelection(result.selection); setInspector('clip') }
    } catch (cause) { editor.setError(String(cause)) }
  }
  function select(id: string, multiple = false) {
    setTransitionId(null)
    setInspector('clip')
    setSelection(previous => multiple ? previous.includes(id) ? previous.filter(x => x !== id) : [...previous, id] : [id])
    setPlaying(false)
  }
  function begin(e: React.PointerEvent, c: Clip, kind: Gesture['kind']) {
    if (!editor.doc || e.button !== 0) return
    e.stopPropagation(); root.current?.focus({ preventScroll: true }); setPlaying(false); setInspector('clip')
    const ids = e.shiftKey ? selection.includes(c.id) ? selection.filter(id => id !== c.id) : [...selection, c.id] : selected.includes(c.id) ? selection : [c.id]
    setTransitionId(null)
    setSelection(ids)
    if (e.shiftKey && kind === 'move') return
    if (doc?.tracks.find(t => t.id === c.track_id)?.locked) { editor.setError('Mở khóa lớp trước khi kéo hoặc cắt.'); return }
    gesture.current = { base: editor.doc, ids, id: c.id, x: e.clientX, kind, next: editor.doc }
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  function drag(e: React.PointerEvent) {
    const g = gesture.current
    if (!g) return
    let delta = (e.clientX - g.x) / scale
    const original = g.base.clips.find(c => c.id === g.id)!
    if (snapping && !e.shiftKey) {
      const edge = g.kind === 'right' ? op.end(original) : original.start
      delta = op.snap(g.base, g.ids, edge + delta, g.kind === 'move' ? original.duration : 0, time, 8 / scale) - edge
    }
    try {
      let next = g.kind === 'move' ? op.move(g.base, g.ids, delta) : op.trim(g.base, g.id, g.kind, delta)
      try { op.validate(next, assets); setDragNote('') } catch (cause) {
        // Dragging a single picture across another inserts it in that order.
        const pics = g.base.clips.filter(c => g.base.tracks.find(t => t.id === c.track_id)?.kind === 'video').sort((a, b) => a.start - b.start)
        const movingPictures = pics.filter(c => op.members(g.base, g.ids).includes(c.id))
        if (g.kind !== 'move' || movingPictures.length !== 1) throw cause
        next = op.dragReorder(g.base, g.ids, original.start + delta)
        op.validate(next, assets); setDragNote('Đổi thứ tự và nối liền các đoạn hình')
      }
      g.next = next; setPreviewDoc(next)
    } catch (cause) { setDragNote(String(cause)) }
  }
  function finish(e: React.PointerEvent, cancel = false) {
    const g = gesture.current
    if (!g) return
    gesture.current = null; setPreviewDoc(null); setDragNote('')
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
    if (!cancel) commit(g.next)
  }
  function add(a: Asset, at = duration, targetTrack?:string) {
    if (!doc) return
    action(d => op.insert(d, a, at,targetTrack))
  }
  function addOverlay(a: Asset, at = time) {
    if (!editor.doc) return
    try {
      const next = insertOverlay(editor.doc, a, at), created = next.clips.find(c => c.overlay && !editor.doc!.clips.some(old=>old.id===c.id))!
      if (commit(next)) { select(created.id); setTime(created.start) }
    } catch(cause) { editor.setError(String(cause)) }
  }
  function addLayer(kind:'caption'|'text'|'effect',variant='caption') {
    if (!editor.doc) return
    try {
      const next = op.layer(editor.doc, kind, time, variant), created = next.clips.at(-1)!
      if (commit(next)) { select(created.id); setTime(created.start) }
    } catch (cause) { editor.setError(String(cause)) }
  }
  function clearTrack(trackId: string) {
    if (!doc) return
    const track = doc.tracks.find(item => item.id === trackId)
    if (!track || track.locked) return
    const clipIds = doc.clips.filter(item => item.track_id === trackId).map(item => item.id)
    if (!clipIds.length) return
    action(d => op.clearTrack(d, trackId))
    setSelection(ids => ids.filter(id => !clipIds.includes(id)))
  }
  function startMarquee(e: React.PointerEvent<HTMLDivElement>) {
    if (e.button !== 0 || e.target !== e.currentTarget || !canvas.current) return
    e.preventDefault()
    setTransitionId(null)
    root.current?.focus({ preventScroll: true })
    const additive = e.shiftKey
    marqueeGesture.current = { pointerId: e.pointerId, clientX: e.clientX, clientY: e.clientY, additive, baseSelection: additive ? selection : [], moved: false }
    setMarquee(null)
    if (!additive) setSelection([])
    seek((e.clientX - e.currentTarget.getBoundingClientRect().left) / scale)
    e.currentTarget.setPointerCapture(e.pointerId)
  }
  function updateMarquee(e: React.PointerEvent<HTMLDivElement>) {
    const g = marqueeGesture.current
    if (!g || g.pointerId !== e.pointerId || !(e.buttons & 1) || !canvas.current) return
    if (!g.moved && Math.hypot(e.clientX - g.clientX, e.clientY - g.clientY) < 4) return
    g.moved = true
    const left = Math.min(g.clientX, e.clientX), right = Math.max(g.clientX, e.clientX)
    const top = Math.min(g.clientY, e.clientY), bottom = Math.max(g.clientY, e.clientY)
    const bounds = canvas.current.getBoundingClientRect()
    setMarquee({ left: left - bounds.left, top: top - bounds.top, width: right - left, height: bottom - top })
    const hits = [...(board.current?.querySelectorAll<HTMLElement>('.tl-clip[data-clip-id]') ?? [])]
      .filter(element => {
        const rect = element.getBoundingClientRect()
        return rect.right >= left && rect.left <= right && rect.bottom >= top && rect.top <= bottom
      })
      .map(element => element.dataset.clipId!)
    setSelection([...new Set([...g.baseSelection, ...hits])])
  }
  function finishMarquee(e: React.PointerEvent<HTMLDivElement>) {
    const g = marqueeGesture.current
    if (!g || g.pointerId !== e.pointerId) return
    marqueeGesture.current = null
    setMarquee(null)
    if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId)
  }
  async function logo(file:File) {
    try{
      const body=new FormData();body.append('file',file)
      const result=await fetchAPI<{id:string}>(`/api/videos/${videoId}/editing/assets`,{method:'POST',body})
      const current=editor.getDocument()
      if(!current)return
      const next=op.layer(current,'effect',Math.min(time,op.length(current)),'image'),c=next.clips.at(-1)!
      commit(op.patch(next,c.id,{effect:{...c.effect!,image_id:result.id}}));setSelection([c.id])
    }catch(cause){editor.setError(String(cause))}
  }
  async function handoff(id:string){
    setHandoffBusy(true)
    try{setPlaying(false);setFocus(false);const result=await editor.handoff(id);await onHandoff(result.video_id)}catch(cause){editor.setError(String(cause))}finally{setHandoffBusy(false)}
  }
  function patchClip(id:string,changes:Partial<Clip>){action(d=>op.patch(d,id,changes))}
  function panelResize(side: 'library' | 'inspector') {
    return <span className={`tl-panel-resize ${side}`} role="separator" aria-label={`Đổi chiều rộng ${side === 'library' ? 'thư viện' : 'thuộc tính'}`} aria-orientation="vertical"
      onPointerDown={e => { e.preventDefault(); panelGesture.current = { x: e.clientX, width: side === 'library' ? libraryWidth : inspectorWidth, side }; e.currentTarget.setPointerCapture(e.pointerId) }}
      onPointerMove={e => { const g = panelGesture.current; if (!g) return; const width = Math.max(165, Math.min(380, g.width + (e.clientX-g.x) * (g.side === 'library' ? 1 : -1))); if (g.side === 'library') setLibraryWidth(width); else setInspectorWidth(width) }}
      onPointerUp={e => { panelGesture.current = null; e.currentTarget.releasePointerCapture(e.pointerId) }} onPointerCancel={() => { panelGesture.current = null }} />
  }
  function keyboard(e: React.KeyboardEvent) {
    const target = e.target as HTMLElement
    if (target.matches('input, textarea, select') || target.closest('video, audio') || target.isContentEditable) return
    if (e.key === 'Escape') { gesture.current = null; marqueeGesture.current = null; setMarquee(null); setPreviewDoc(null); setDragNote(''); setTransitionId(null); setPlaying(false); setFocus(false); return }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') { e.preventDefault(); setPlaying(false); if (e.shiftKey) editor.redo(); else editor.undo(); return }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') { e.preventDefault(); void editor.save().catch(() => {}); return }
    if (e.key === ' ' && !target.closest('button, a')) { e.preventDefault(); root.current?.querySelector<HTMLButtonElement>('.tl-play')?.click() }
    if (e.key.toLowerCase() === 's' && !e.metaKey && !e.ctrlKey && !transitionId && selection.length) { e.preventDefault(); splitSelected() }
    if ((e.key === 'Delete' || e.key === 'Backspace') && (selectedJoin || !transitionId && selection.length)) { e.preventDefault(); removeSelected(e.shiftKey) }
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'd' && !transitionId && selection.length) { e.preventDefault(); action(d => op.duplicate(d, selection)) }
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); seek(time + (e.key === 'ArrowRight' ? 1 : -1) / (doc?.fps ?? 30)) }
  }
  if (!doc) return <div className="tl-loading">{editor.error || 'Đang nạp bản dựng…'}<button onClick={editor.reload}><RefreshCw size={15} />Nạp lại</button></div>
  const contentWidth = Math.max(viewport, (duration + Math.max(3, viewport / scale * .15)) * scale)
  const tick = scale >= 80 ? 1 : scale >= 20 ? 5 : scale >= 5 ? 10 : 60
  const firstTick = Math.floor(scroll / scale / tick) * tick
  const tickCount = Math.ceil(viewport / scale / tick) + 2
  const visualIds = [...new Set(doc.clips.flatMap(c => c.asset_id?[c.asset_id]:[]))].filter(id => !editor.visuals[id] && !assets.find(a => a.id === id)?.missing)
  const proxyIds=[...new Set(doc.clips.flatMap(c=>c.asset_id?[c.asset_id]:[]))].filter(id=>{const a=assets.find(a=>a.id===id);return a?.kind==='video'&&!a.missing&&!a.proxy_url})
  const jobResult = editor.job?.result
  const result = editor.job?.kind.startsWith('timeline-') && jobResult?.url && (editor.job.kind !== 'timeline-export' || editor.state?.exports?.some(e => e.url === jobResult.url)) ? jobResult : null

  return <div className="tl-editor" data-focus={focus && active} style={{ '--tl-library-width': `${libraryWidth}px`, '--tl-inspector-width': `${inspectorWidth}px`,'--tl-timeline-height':`${timelineHeight}px` } as React.CSSProperties} ref={root} tabIndex={0} onKeyDown={keyboard} onPlayCapture={e => {
    if (e.target instanceof HTMLMediaElement && !e.target.hasAttribute('data-timeline-media')) setPlaying(false)
  }} aria-label="Bàn dựng video">
    <header className="tl-toolbar"><div><span className="tl-label">BÀN DỰNG</span><h3>{title}</h3></div>
      <span className="tl-save-state" role="status">{editor.error && editor.dirty ? 'Chưa lưu · cần kiểm tra' : editor.saving ? 'Đang lưu…' : editor.dirty ? 'Có thay đổi · đang chờ lưu' : <><Check size={13} />Đã lưu · bản {editor.revision}</>}</span>
      <button title="Lưu bản dựng · ⌘S" onClick={() => void editor.save().catch(() => {})} disabled={editor.saving}><Save size={16} />Lưu</button>
      <button title={editor.canUndo ? 'Hoàn tác · ⌘Z' : 'Chưa có thao tác chỉnh sửa để hoàn tác'} disabled={!editor.canUndo} onClick={() => { setPlaying(false); editor.undo() }}><Undo2 size={17} />Hoàn tác</button>
      <button title={editor.canRedo ? 'Làm lại · ⌘⇧Z' : 'Bấm Hoàn tác trước để có bước làm lại'} disabled={!editor.canRedo} onClick={() => { setPlaying(false); editor.redo() }}><Redo2 size={17} />Làm lại</button>
      <button title={focus ? 'Thu gọn bàn dựng' : 'Mở rộng bàn dựng'} aria-pressed={focus} onClick={() => setFocus(!focus)}>{focus ? <Minimize2 size={16} /> : <Maximize2 size={16} />}</button>
      <button aria-pressed={libraryTab === 'text'} onClick={() => { setLibraryTab('text'); setPlaying(false) }}><Type size={16} />Chữ &amp; tiêu đề</button>
      <button aria-pressed={inspector === 'frame'} onClick={() => { setPlaying(false); setInspector('frame') }}>Khung hình</button>
      <button disabled={busy || !doc.clips.length} onClick={() => { setResultOpen(true); void editor.render(true) }}><Play size={14} />Xuất thử 12s</button>
      <button className="tl-primary" disabled={busy || !doc.clips.length} onClick={() => { setResultOpen(true); void editor.render(false) }}><ArrowDownToLine size={15} />Xuất bản dựng</button>
      <select className="tl-ai-select" aria-label="Mở công cụ AI" value="" disabled={busy||handoffBusy} onChange={e => { setPlaying(false);setFocus(false);onOpenAI(e.target.value as AITool) }}><option value="">✦ Công cụ AI</option><option value="speech">Nhận dạng, dịch & lồng tiếng</option><option value="narration">Tạo lời đọc theo cảnh</option><option value="music">Tạo nhạc Suno</option><option value="cover">Tạo ảnh bìa</option><option value="restore">Xóa vật thể bằng AI · ProPainter</option></select>
    </header>
    <p className="tl-autosave-note">Tự lưu sau mỗi chỉnh sửa · {editor.historyNotice || 'Lịch sử hoàn tác được giữ trong trình duyệt này.'}</p>
    {editor.state?.legacy_available && !doc.legacy_imported && <div className="tl-legacy"><span>Có thiết lập hậu kỳ từ giao diện cũ. Nạp khung, âm lượng, phụ đề, giọng và hiệu ứng vào bản dựng.</span><button disabled={busy} onClick={() => void editor.importResults({legacy:true}).catch(() => {})}>Nạp thiết lập cũ</button><small>Có thể hoàn tác sau khi nạp.</small></div>}
    {editor.error && <div className="tl-error" role="alert">{editor.error}{editor.saveBlocked && <><button onClick={() => void editor.save().catch(() => {})}>Thử lưu lại</button><button onClick={editor.reload}>Nạp lại bản đã lưu</button></>}<button onClick={() => editor.setError('')}>Đóng thông báo</button></div>}
    {editor.importNotice && <p className="tl-autosave-note" role="status">{editor.importNotice}</p>}
    {editor.state?.warnings.length ? <details className="tl-warning"><summary>Có {editor.state.warnings.length} mục cần kiểm tra</summary>{editor.state.warnings.map((w, i) => <p key={i}>{w}</p>)}</details> : null}
    {!editor.state?.source_available && <div className="tl-warning">Video nguồn chưa được lưu để dựng timeline. <button disabled={busy} onClick={() => void editor.prepare()}><RefreshCw size={14} />Nạp video nguồn</button></div>}
    <div className="tl-top">
      <aside className="tl-library">
        <div className="tl-library-mode"><button aria-pressed={libraryTab === 'media'} onClick={() => setLibraryTab('media')}>Tư liệu</button><button aria-pressed={libraryTab === 'text'} onClick={() => setLibraryTab('text')}>Chữ &amp; tiêu đề</button></div>
        {libraryTab === 'text' ? <TextLibrary onAdd={id => addLayer('text', id)} /> : <><div className="tl-panel-title"><strong>Thư viện tư liệu</strong><button title="Nạp lại thư viện" onClick={() => void editor.refresh().catch(cause => editor.setError(String(cause)))}><RefreshCw size={14} /></button></div>
        <details className="tl-upload-panel"><summary><Upload size={13}/>Tải video / nhạc / hiệu ứng</summary><div className="tl-upload-panel-body">
        <VideoLibraryUpload key={projectId} projectId={projectId} refresh={editor.refresh}/>
        <div className="tl-upload-buttons">
          <button disabled={upload.uploading} onClick={() => { setUploadKind('music'); if (uploadInput.current) { uploadInput.current.accept = '.mp3,.wav'; uploadInput.current.click() } }}><Upload size={13} />Tải nhạc</button>
          <button disabled={upload.uploading} onClick={() => { setUploadKind('sfx'); if (uploadInput.current) { uploadInput.current.accept = '.mp3,.wav,.m4a,.ogg,.flac,.aac'; uploadInput.current.click() } }}><Upload size={13} />Tải hiệu ứng</button>
        </div>
        <input ref={uploadInput} className="tl-upload-input" type="file" aria-label="Chọn nhạc hoặc hiệu ứng từ máy" onChange={e => { const file = e.target.files?.[0]; e.target.value = ''; if (file) { setFilter(uploadKind); void upload.upload(file, uploadKind) } }} />
        <p className="tl-upload-limits">Nhạc: MP3/WAV · 100 MB<br />Hiệu ứng: tối đa 16 MB / 30 giây</p>
        {upload.message && <div className="tl-upload-state" role="status"><span>{upload.message}</span>{upload.uploading && <><progress value={upload.progress} max={100} aria-label="Tiến trình tải âm thanh" /><span>{upload.progress}%</span>{upload.canCancel && <button onClick={upload.cancel}>Hủy tải</button>}</>}</div>}
        </div></details>
        <input aria-label="Tìm tư liệu" placeholder="Tìm video, nhạc…" value={search} onChange={e => setSearch(e.target.value)} />
        <div className="tl-library-tabs">{[['all', 'Tất cả'], ['video', 'Video'], ['music', 'Nhạc'], ['sfx', 'Hiệu ứng'],['voice','Giọng']].map(([id, name]) => <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}>{name}</button>)}</div>
        <div className="tl-assets">{visibleLibrary.map(a => <article key={a.id} className="tl-asset" draggable={!a.missing} onDragStart={e => { e.dataTransfer.setData('application/x-flowkit-asset', a.id); e.dataTransfer.effectAllowed = 'copy' }}>
          <span className={`tl-asset-icon ${a.kind}`}>{a.kind === 'video' ? <Film size={20} /> : <Music2 size={20} />}</span>
          <div><strong title={a.name}>{a.name}</strong><small>{a.missing ? 'Tư liệu đã thay đổi' : `${clock(a.duration)} · ${a.category === 'export' ? 'Bản xuất' : a.category === 'source' ? 'Video nguồn' : a.category === 'music' ? 'Nhạc nền' : a.category === 'voice' ? 'Giọng đọc' : 'Hiệu ứng'}`}</small></div>
          <button aria-label={`Thêm ${a.name} vào cuối`} disabled={a.missing} onClick={() => add(a)}><Plus size={16} /></button>
          {a.management_id && <MediaLibraryActions projectId={projectId} itemId={a.management_id} name={a.name} disabled={busy} beforeChange={async () => { setPlaying(false); await editor.save() }} onChanged={onLibraryChanged} />}
          {a.kind === 'video' && <button className="tl-insert-pip" disabled={a.missing} aria-label={`Chèn khung: ${a.name}`} onClick={()=>addOverlay(a)}>Chèn khung tại vạch phát</button>}
          {a.kind === 'audio' && !a.missing && <audio controls preload="none" src={resolveMediaUrl(a.url)} />}
        </article>)}</div>
        <details className="tl-library-help"><summary>Cách dùng thư viện</summary><p className="tl-help">Bấm “Chèn khung” để lồng video lên hình chính tại vạch phát. Kéo vào lớp Video chèn để thêm đoạn tiếp theo. Nút + nối video vào cuối. Các bản xuất đã có giọng/chữ giữ nguyên nội dung đó.</p></details></>}{panelResize('library')}
      </aside>
      <TimelinePreview videoId={videoId} doc={doc} assets={assets} visuals={editor.visuals} time={Math.min(time, duration)} onTime={setTime} playing={playing} onPlaying={setPlaying} active={active} selection={selection} onSelect={select} onPatch={patchClip} range={range}/>
      <aside className="tl-inspector"><div className="tl-inspector-tabs"><button aria-pressed={inspector==='clip'} onClick={()=>setInspector('clip')}>Đoạn đã chọn</button><button aria-pressed={inspector==='frame'} onClick={()=>setInspector('frame')}>Khung hình</button></div>
        {inspector==='frame' ? <FrameInspector doc={doc} onChange={frame=>commit({...doc,frame})}/> : clip ? <><h4>{asset?.name||clip.text||'Hiệu ứng hình'}</h4><p className="tl-help">{selected.length > 1 ? `${selected.length} đoạn liên kết / được chọn` : '1 đoạn được chọn'}</p>
          <Field label="Vị trí trên timeline (giây)" value={clip.start} step={1/doc.fps} disabled={doc.tracks.find(t => t.id === clip.track_id)?.locked} commit={n => action(d => op.move(d, [clip.id], n - clip.start))} />
          {asset&&<Field label="Bắt đầu lấy từ tư liệu (giây)" value={clip.source_in} step={1/doc.fps} disabled={doc.tracks.find(t => t.id === clip.track_id)?.locked} commit={n => action(d => op.sourceIn(d, clip.id, n))} />}
          <Field label="Độ dài đoạn (giây)" value={clip.duration} step={1/doc.fps} disabled={doc.tracks.find(t => t.id === clip.track_id)?.locked} commit={n => action(d => op.setDuration(d, clip.id, n, assets))} />
          {asset?.kind === 'video' && <p className="tl-help">Đổi độ dài cảnh sẽ tự dời các cảnh sau. Nếu không đủ nguồn, hình và tiếng liên quan phát chậm, phụ đề căn theo. Kéo mép dừng ở cảnh bên cạnh.</p>}
          {doc.tracks.find(t => t.id === clip.track_id)?.kind === 'audio' && <Field label="Âm lượng (0–200%)" value={clip.gain * 100} step={5} disabled={doc.tracks.find(t => t.id === clip.track_id)?.locked} commit={n => action(d => op.gain(d, clip.id, n / 100))} />}
          {asset?.ref.startsWith('stickman:') && <StickmanActorInspector key={clip.id+asset.id} videoId={videoId} assetId={asset.id} disabled={busy || !!doc.tracks.find(t => t.id === clip.track_id)?.locked} before={editor.save} apply={async picture => {
            const refreshed = await editor.refresh([picture])
            const current = editor.getDocument()
            if (current && refreshed) commit({ ...current, clips: current.clips.map(c => c.id === clip.id ? { ...c, asset_id: picture.id } : c) }, refreshed.assets)
          }} />}
          <fieldset disabled={doc.tracks.find(t=>t.id===clip.track_id)?.locked} className="tl-layer-fields"><VideoLayerInspector doc={doc} clip={clip} review={preset=>action(d=>applyReviewPreset(d,clip.id,preset))} patch={changes=>patchClip(clip.id,changes)} patchAudio={patchClip} solo={id=>commit({...doc,tracks:doc.tracks.map(t=>t.id===id?{...t,solo:!t.solo}:t)})}/><LayerInspector videoId={videoId} clip={clip} doc={doc} fonts={editor.state?.fonts??[]} patch={changes=>patchClip(clip.id,changes)} speed={n=>action(d=>op.speed(d,clip.id,n))} applyCaptionStyle={()=>action(d=>({...d,clips:d.clips.map(c=>c.track_id===clip.track_id&&c.caption?{...c,caption:{...clip.caption!}}:c)}))}/></fieldset>
          {doc.tracks.find(t=>t.id===clip.track_id)?.kind === 'video' && <div className="tl-inspector-actions"><button onClick={() => action(d => op.reorder(d, clip.id, -1))}><ArrowLeft size={14} />Trước</button><button onClick={() => action(d => op.reorder(d, clip.id, 1))}>Sau<ArrowRight size={14} /></button></div>}
          <button onClick={() => action(d => op.link(d, selection, !!clip.group))}>{clip.group ? <Unlink size={14} /> : <Link size={14} />}{clip.group ? 'Tách liên kết hình / tiếng' : 'Liên kết đoạn đã chọn'}</button>
          <p className="tl-help">Kéo hai mép để cắt. Shift + bấm chọn nhiều đoạn. Mốc cắt bám theo khung hình.</p>
        </> : <div className="tl-inspector-empty"><GripVertical size={24} /><p>Chọn một đoạn trên timeline để chỉnh.</p><small>Kéo để di chuyển hoặc đổi thứ tự. Hình và tiếng liên kết đi cùng nhau.</small></div>}
        <div className="tl-shortcuts"><strong>Phím tắt</strong><span>Space <small>Phát / dừng</small></span><span>S <small>Tách tại vạch phát</small></span><span>Delete <small>Xóa, giữ khoảng trống</small></span><span>Shift + Delete <small>Xóa và nối khoảng trống</small></span><span>⌘Z / ⌘⇧Z <small>Hoàn tác / làm lại</small></span></div>{panelResize('inspector')}
      </aside>
    </div>
    <div className="tl-stack-resize" role="separator" aria-label="Đổi chiều cao timeline" aria-orientation="horizontal" onPointerDown={e=>{e.preventDefault();stackGesture.current={y:e.clientY,height:root.current?.querySelector('.tl-timeline')?.getBoundingClientRect().height??timelineHeight};e.currentTarget.setPointerCapture(e.pointerId)}} onPointerMove={e=>{const g=stackGesture.current;if(g)setTimelineHeight(Math.max(180,Math.min(500,g.height+g.y-e.clientY)))}} onPointerUp={e=>{stackGesture.current=null;e.currentTarget.releasePointerCapture(e.pointerId)}} onPointerCancel={()=>{stackGesture.current=null}}/>
    <section className="tl-timeline" aria-label="Timeline nhiều lớp">
      <div className="tl-tools"><strong>Timeline</strong><span className="tl-duration">{clock(duration)}</span>
        <button title="Tách tại vạch phát · S" disabled={!selection.length || !!transitionId} onClick={splitSelected}><Scissors size={16} />Tách</button>
        <button title={selectedJoin ? 'Xóa chuyển cảnh này' : 'Xóa, giữ khoảng trống'} disabled={transitionId ? !selectedJoin?.next.transition : !selection.length} onClick={() => removeSelected()}><Trash2 size={15} /></button>
        <button disabled={!selection.length || !!transitionId} onClick={() => removeSelected(true)}>Xóa & nối</button>
        <button title="Nhân đôi vào cuối · ⌘D" disabled={!selection.length || !!transitionId} onClick={() => action(d => op.duplicate(d, selection))}><Copy size={15} /></button>
        {!selectedJoin && hasTransitions && <button disabled={busy || !!doc.tracks.find(t => t.kind === 'video')?.locked} onClick={() => action(d => setTransition(d, null, null, true))}>Xóa tất cả chuyển cảnh</button>}
        <button onClick={() => action(d => op.pack(d))}>Sắp xếp liền nhau</button>
        <button title="Bám mép đoạn / vạch phát; giữ Shift để bỏ bám khi kéo" aria-pressed={snapping} onClick={() => setSnapping(!snapping)}><Magnet size={15} /></button>
        <button disabled={busy || !visualIds.length} onClick={() => void editor.visualize(visualIds)}><Waves size={15} />Vẽ sóng & ảnh</button>
        <button aria-pressed={selectRange} onClick={()=>{setSelectRange(!selectRange);setPlaying(false)}}>Vùng chọn</button>
        <button aria-pressed={silenceOpen} onClick={()=>{setSilenceOpen(!silenceOpen);setPlaying(false)}}><Scissors size={14} />Cắt khoảng lặng</button>
        <button onClick={() => { setLibraryTab('text'); addLayer('text', 'free') }}>+ Chữ</button>
        <button onClick={()=>addLayer('caption')}>+ Phụ đề</button>
        <select aria-label="Thêm hiệu ứng hình" value="" onChange={e=>{if(e.target.value==='image')logoInput.current?.click();else if(e.target.value)addLayer('effect',e.target.value)}}><option value="">+ Hiệu ứng hình</option><option value="blur">Làm mờ</option><option value="color">Phủ màu</option><option value="delogo">Delogo</option><option value="image">Ảnh / logo</option><option value="colors">Chỉnh màu</option></select>
        <input hidden ref={logoInput} type="file" accept=".png,.jpg,.jpeg,.webp" aria-label="Tải logo cho timeline" onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void logo(file)}}/>
        <button disabled={busy} onClick={()=>void editor.importResults().catch(() => {})}>Nạp kết quả AI đã lưu</button>
        <button disabled={busy||!proxyIds.length} onClick={()=>void editor.proxies(proxyIds)}>Tạo bản xem nhẹ</button>
        <label className="tl-zoom"><span>Thu phóng</span><input aria-label="Thu phóng timeline" type="range" min={2} max={180} value={scale} onChange={e => setScale(Number(e.target.value))} /></label>
        <button onClick={() => { setScale(Math.max(2, Math.min(180, viewport / Math.max(1, duration + 1)))); if (board.current) board.current.scrollLeft = 0 }}>Vừa khung</button>
      </div>
      <DuckingControls doc={doc} commit={commit}/>
      {selectedJoin && <TransitionControls value={selectedJoin.next.transition ?? null} duration={selectedJoin.duration} hasAny={hasTransitions} disabled={busy || !!doc.tracks.find(t => t.kind === 'video')?.locked}
        change={value => action(d => setTransition(d, selectedJoin.next.id, value))}
        applyAll={() => action(d => setTransition(d, selectedJoin.next.id, selectedJoin.next.transition ?? null, true))}
        clearAll={() => action(d => setTransition(d, null, null, true))}
        preview={() => { seek(Math.max(0, selectedJoin.next.start - .5)); setPlaying(true) }} close={() => setTransitionId(null)}/>}
      {range&&range.end>range.start&&<div className="tl-range-tools"><strong>Vùng {clock(range.start)} – {clock(range.end)}</strong><button onClick={()=>{seek(range.start);setPlaying(true)}}>Nghe / xem vùng</button><button onClick={()=>{action(d=>op.keepRange(d,range.start,range.end));setRange(null);seek(0)}}>Chỉ giữ vùng</button><button onClick={()=>{action(d=>op.cutRange(d,range.start,range.end));setRange(null);seek(range.start)}}>Cắt vùng & nối</button><button disabled={busy} onClick={()=>{setResultOpen(true);void editor.render(false,range)}}>Xuất vùng chọn</button><button onClick={()=>setRange(null)}>Bỏ vùng chọn</button></div>}
      {silenceOpen && <SilencePanel doc={doc} assets={assets} visuals={editor.visuals} selectedId={clip?.id} threshold={threshold} minimum={minimum}
        onDetection={(db, seconds) => { setThreshold(db); setMinimum(seconds) }} busy={busy} onAnalyze={ids => void editor.visualize(ids)} onRange={showPause} onCut={cutPauses}
        onClose={() => { setSilenceOpen(false); setRange(null); setPlaying(false) }} />}
      <div className="tl-board"><div className="tl-track-labels"><div className="tl-ruler-label">GIÂY</div>{doc.tracks.map(t => {
        const clearable = t.kind === 'caption' || t.kind === 'text' || t.kind === 'audio' && t.role === 'voice'
        const count = doc.clips.filter(c => c.track_id === t.id).length
        return <div key={t.id} className="tl-track-label"><div className="tl-track-heading"><span className="tl-track-name">{t.kind === 'video' || t.kind === 'overlay' ? <Film size={15} /> : t.kind === 'caption' || t.kind === 'text' ? <Type size={15} /> : <Music2 size={15} />}{t.name}</span>{clearable && <button className="tl-clear-track" title={`Xóa hết ${t.name.toLowerCase()} (${count} đoạn) · Có thể hoàn tác bằng ⌘Z`} aria-label={`Xóa hết ${t.name.toLowerCase()} (${count} đoạn)`} disabled={!count || t.locked || busy} onClick={() => clearTrack(t.id)}><Trash2 size={12} /><span>Xóa</span></button>}</div><div>
        <button title={t.muted ? 'Bật lớp' : 'Tắt lớp'} aria-pressed={t.muted} onClick={() => commit({ ...doc, tracks: doc.tracks.map(item => item.id === t.id ? { ...item, muted: !item.muted } : item) })}>{t.kind === 'audio' ? (t.muted ? <VolumeX size={13} /> : <Volume2 size={13} />) : (t.muted ? <EyeOff size={13}/> : <Eye size={13}/>) }</button>
        <button title={t.locked ? 'Mở khóa lớp' : 'Khóa lớp'} aria-pressed={t.locked} onClick={() => commit({ ...doc, tracks: doc.tracks.map(item => item.id === t.id ? { ...item, locked: !item.locked } : item) })}>{t.locked ? <Lock size={13} /> : <Unlock size={13} />}</button>
        {t.kind==='audio'&&<><button title="Nghe riêng lớp" aria-pressed={!!t.solo} onClick={()=>commit({...doc,tracks:doc.tracks.map(item=>item.id===t.id?{...item,solo:!item.solo}:item)})}>S</button><select aria-label={`Vai trò ${t.name}`} value={t.role??'original'} onChange={e=>commit({...doc,tracks:doc.tracks.map(item=>item.id===t.id?{...item,role:e.target.value as 'original'|'music'|'voice'|'effects'}:item)})}><option value="original">Âm gốc</option><option value="music">Nhạc</option><option value="voice">Giọng</option><option value="effects">SFX</option></select></>}
      </div></div>
      })}</div>
        <div ref={board} className="tl-scroll" onScroll={e => setScroll(e.currentTarget.scrollLeft)}>
          <div ref={canvas} className="tl-canvas" style={{ width: contentWidth }}>
            <div className="tl-ruler" onPointerDown={e => { e.preventDefault();const at=Math.max(0,Math.min(duration,op.frame((e.clientX-e.currentTarget.getBoundingClientRect().left)/scale,doc.fps)));if(selectRange||e.shiftKey){rangeBegin.current=at;setRange({start:at,end:at})}else rangeBegin.current=null;seek(at);e.currentTarget.setPointerCapture(e.pointerId) }}
              onPointerMove={e => { if (e.buttons === 1 && e.currentTarget.hasPointerCapture(e.pointerId)){const at=Math.max(0,Math.min(duration,op.frame((e.clientX-e.currentTarget.getBoundingClientRect().left)/scale,doc.fps)));if(rangeBegin.current!==null)setRange({start:Math.min(at,rangeBegin.current),end:Math.max(at,rangeBegin.current)});else seek(at)} }}
              onPointerUp={e => { rangeBegin.current=null;if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId) }}>
              {Array.from({ length: tickCount }, (_, i) => firstTick + i * tick).map(at => <span key={at} style={{ left: at * scale }}>{clock(at)}</span>)}</div>
            {doc.tracks.map(t => <div key={t.id} className={`tl-row ${t.kind}`} aria-label={`Lớp ${t.name}`} onPointerDown={startMarquee} onPointerMove={updateMarquee} onPointerUp={finishMarquee} onPointerCancel={finishMarquee} onLostPointerCapture={finishMarquee}
              onDragOver={e => { if (e.dataTransfer.types.includes('application/x-flowkit-asset')) { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' } }}
              onDrop={e => { e.preventDefault(); const a = assets.find(a => a.id === e.dataTransfer.getData('application/x-flowkit-asset')); if (a && t.kind === 'overlay') addOverlay(a, Math.max(0, (e.clientX - e.currentTarget.getBoundingClientRect().left) / scale)); else if (a&&(t.kind==='audio'||t.kind==='video')) add(a, Math.max(0, (e.clientX - e.currentTarget.getBoundingClientRect().left) / scale),t.id) }}>
              {doc.clips.filter(c => c.track_id === t.id && op.end(c) * scale >= scroll - 100 && c.start * scale <= scroll + viewport + 100).map(c => {
                const a = assets.find(a => a.id === c.asset_id)
                return <div key={c.id} role="button" tabIndex={0} aria-label={`Đoạn ${a?.name ?? c.text ?? 'Tư liệu'} · ${clock(c.start)} · ${clock(c.duration)}`} aria-pressed={selected.includes(c.id)} data-clip-id={c.id}
                  className={`tl-clip ${t.kind} ${selected.includes(c.id) ? 'selected' : ''} ${t.locked ? 'locked' : ''} ${a?.missing ? 'missing' : ''}`}
                  style={{ left: c.start * scale, width: Math.max(4, c.duration * scale) }} onKeyDown={e => { if (e.key === 'Enter') { e.stopPropagation(); select(c.id, e.shiftKey) } }}
                  onPointerDown={e => begin(e, c, 'move')} onPointerMove={drag} onPointerUp={e => finish(e)} onPointerCancel={e => finish(e, true)} onLostPointerCapture={e => finish(e, true)}>
                  <ClipVisual clip={c} visuals={editor.visuals[c.asset_id??'']} scale={scale} scroll={scroll} viewport={viewport} audio={t.kind === 'audio'} threshold={threshold} minimum={minimum}/>
                  <span className="tl-clip-title">{(c.group||c.anchor) && <Link size={10} />}{a?.name || c.text || c.effect?.kind || (c.colors?'Chỉnh màu':'Tư liệu')}{t.kind === 'audio' && c.gain !== 1 ? ` · ${Math.round(c.gain*100)}%` : ''}</span>
                  {(t.kind==='caption'||t.kind==='text')&&<span className="tl-caption-text">{c.text}</span>}
                  {t.kind==='caption'&&c.text&&c.text.length/c.duration>18&&<button className="tl-cue-warning" title="Phụ đề dài hoặc đọc nhanh; rút gọn hoặc kéo dài" onPointerDown={e=>e.stopPropagation()} onClick={()=>{select(c.id);seek(c.start)}}>!</button>}
                  {c.asset_id&&!editor.visuals[c.asset_id] && <span className="tl-no-visual">{t.kind === 'audio' ? 'Chưa vẽ sóng âm' : 'Chưa tạo dải ảnh'}</span>}
                  {t.kind==='audio'&&<AudioHandles clip={c} scale={scale} disabled={t.locked} onCommit={changes=>patchClip(c.id,changes)}/>}
                  <span className="tl-trim left" role="slider" aria-label="Cắt mép trái" aria-valuenow={c.source_in} tabIndex={-1} onPointerDown={e => begin(e, c, 'left')} onPointerMove={drag} onPointerUp={e => finish(e)} onPointerCancel={e => finish(e, true)} />
                  <span className="tl-trim right" role="slider" aria-label="Cắt mép phải" aria-valuenow={c.source_in+c.duration} tabIndex={-1} onPointerDown={e => begin(e, c, 'right')} onPointerMove={drag} onPointerUp={e => finish(e)} onPointerCancel={e => finish(e, true)} />
                </div>
              })}
              {t.kind === 'video' && joins.filter(j => j.next.start * scale >= scroll - 30 && j.next.start * scale <= scroll + viewport + 30).map(j => <button key={`transition-${j.next.id}`} className={`tl-transition-join ${j.next.transition ? 'enabled' : ''}`} style={{ left: j.next.start * scale }}
                aria-label={`Chuyển cảnh tại ${clock(j.next.start)}`} aria-pressed={j.next.id === transitionId} title={j.next.transition ? `${TRANSITIONS.find(t => t.kind === j.next.transition!.kind)?.name} · ${j.duration.toFixed(2)}s` : 'Thêm chuyển cảnh'}
                onPointerDown={e => e.stopPropagation()} onClick={() => { setSelection([]); setTransitionId(j.next.id); seek(j.next.start + j.duration / 2) }}>◇</button>)}
            </div>)}
            {range&&<div className="tl-range-highlight" style={{left:range.start*scale,width:(range.end-range.start)*scale}}/>}
            {marquee&&<div className="tl-marquee" aria-hidden="true" style={{left:marquee.left,top:marquee.top,width:marquee.width,height:marquee.height}}/>}
            <div className="tl-playhead" style={{ left: time * scale }}><span /></div>
          </div>
        </div>
      </div>
      <footer className="tl-status"><span>{dragNote || (selected.length ? `${selected.length} đoạn được chọn` : 'Kéo trên vùng trống để khoanh chọn · Shift + bấm/kéo để chọn thêm')}</span><span>Video nguồn được giữ nguyên · lưu tự động</span></footer>
    </section>
    {editor.job && (editor.running || editor.pending || editor.job.status === 'FAILED' && editor.job.kind.startsWith('timeline-')) && <div className="tl-job" aria-live="polite"><span>{editor.job.stage} · {editor.job.progress}%</span><progress value={editor.job.progress} max={100} />{editor.running && editor.job.kind.startsWith('timeline-') && <button onClick={() => void editor.cancel()}><Square size={13} />Dừng</button>}{editor.job.error && <p role="alert">{editor.job.error}</p>}</div>}
    {result && <div className="tl-result"><span><Check size={15} />{editor.job?.kind === 'timeline-preview' ? 'Bản xuất thử' : result.range_end!=null?'Bản xuất vùng chọn':'Bản dựng hoàn chỉnh'} · {clock(result.duration ?? 0)}</span><button onClick={() => setResultOpen(!resultOpen)}>{resultOpen ? <Pause size={14} /> : <Play size={14} />}{resultOpen ? 'Đóng xem trước' : 'Xem video'}</button><a href={resolveMediaUrl(result.url)} download><ArrowDownToLine size={14} />Tải xuống</a>{editor.job?.kind==='timeline-export'&&result.export_id&&!result.range_start&&result.range_end==null&&<button disabled={busy||handoffBusy} onClick={()=>void handoff(result.export_id!)}>{handoffBusy?'Đang chuẩn bị…':'Tạo bản phẳng để xử lý AI tiếp'}</button>}{resultOpen && <video data-timeline-result controls preload="metadata" src={resolveMediaUrl(result.url)} onPlay={() => setPlaying(false)} />}</div>}
    {!!editor.state?.exports?.length && <details className="tl-versions"><summary>Lịch sử bản xuất · {editor.state.exports.length} phiên bản</summary><ExportVersionsPanel key={videoId} videoId={videoId} versions={editor.state.exports} disabled={busy || handoffBusy} beforeDelete={editor.save} onRefresh={async () => { await editor.refresh(); await onExport() }} onHandoff={handoff} /></details>}
  </div>
}
