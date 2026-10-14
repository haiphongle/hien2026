import { useMemo, useState } from 'react'
import { Check, Play, Scissors, Waves, X } from 'lucide-react'
import type { Asset, Document, Visuals } from './types'
import { clock, length } from './operations'
import { planSilenceCuts, SILENCE_PRESETS } from './silence'
import type { SilenceSource, TimeRange } from './silence'

export function SilencePanel({ doc, assets, visuals, selectedId, threshold, minimum, onDetection, busy, onAnalyze, onRange, onCut, onClose }: {
  doc: Document; assets: Asset[]; visuals: Record<string, Visuals>; selectedId?: string; threshold: number; minimum: number
  onDetection: (threshold: number, minimum: number) => void; busy: boolean; onAnalyze: (ids: string[]) => void
  onRange: (range: TimeRange, play: boolean) => void; onCut: (ranges: TimeRange[]) => boolean; onClose: () => void
}) {
  const [choice, setChoice] = useState<SilenceSource | null>(null), [padding, setPadding] = useState(.1), [trimEdges, setTrimEdges] = useState(false)
  const [selection, setSelection] = useState<{ plan: unknown; excluded: Set<string> }>({ plan: null, excluded: new Set() })
  const [message, setMessage] = useState(''), [page, setPage] = useState(0)
  const [selectNewPlan, setSelectNewPlan] = useState(true), [messageDuration, setMessageDuration] = useState<number | null>(null)
  const hasVoice = doc.clips.some(c => c.gain > 0 && doc.tracks.some(t => t.id === c.track_id && t.kind === 'audio' && t.role === 'voice' && !t.muted))
  const source = choice ?? (hasVoice ? 'voices' : 'original')
  const analysisId = source === 'selection' ? selectedId : undefined
  const plan = useMemo(() => planSilenceCuts(doc, assets, visuals, { source, selectedId: analysisId, threshold, minimum, padding, trimEdges }),
    [doc, assets, visuals, source, analysisId, threshold, minimum, padding, trimEdges])
  const excluded = selection.plan === plan ? selection.excluded : new Set(selectNewPlan ? [] : plan.ranges.map(r => r.id))
  const selected = plan.ranges.filter(r => !excluded.has(r.id)), total = length(doc)
  const removed = selected.reduce((sum, r) => sum + r.end - r.start, 0)
  const locked = doc.tracks.filter(t => t.locked && doc.clips.some(c => c.track_id === t.id && c.start + c.duration > (selected[0]?.start ?? Infinity)))
  const pageCount = Math.ceil(plan.ranges.length / 25), currentPage = Math.min(page, Math.max(0, pageCount - 1))
  const shown = plan.ranges.slice(currentPage * 25, (currentPage + 1) * 25)
  const hasSelectedAudio = doc.clips.some(c => c.id === selectedId && assets.some(a => a.id === c.asset_id && a.has_audio))
  const selectedPreset = SILENCE_PRESETS.find(p => p.threshold === threshold && p.minimum === minimum && p.padding === padding)
  function toggle(id: string) {
    const next = new Set(excluded)
    if (next.has(id)) next.delete(id); else next.add(id)
    setSelection({ plan, excluded: next }); setMessage('')
  }
  function apply() {
    if (!selected.length || locked.length || busy) return
    if (onCut(selected)) {
      // A second click after a successful cut must not remove all remaining pauses.
      setSelectNewPlan(false)
      setMessage(`Đã cắt ${selected.length} khoảng, rút ngắn ${removed.toFixed(2)} giây. Bấm Hoàn tác hoặc ⌘Z để khôi phục.`)
      setMessageDuration(total - removed); setPage(0)
    }
  }
  return <section className="tl-silence-panel" aria-label="Cắt khoảng lặng">
    <header><div><strong><Scissors size={16} /> Cắt khoảng lặng</strong><p>Chọn nhịp → nghe thử → cắt và nối toàn bộ hình, tiếng, phụ đề.</p></div><button aria-label="Đóng cắt khoảng lặng" onClick={onClose}><X size={15} /></button></header>
    <div className="tl-silence-controls">
      <label>Dò theo<select aria-label="Nguồn dò khoảng lặng" value={source} onChange={e => { setChoice(e.target.value as SilenceSource); setMessage(''); setPage(0) }}>
        <option value="voices" disabled={!plan.hasVoices}>Giữa các câu giọng đọc</option>
        <option value="original">Âm gốc · toàn bản dựng</option>
        <option value="selection" disabled={!hasSelectedAudio}>Đoạn đang chọn</option>
      </select></label>
      <div className="tl-silence-presets" role="group" aria-label="Nhịp cắt khoảng lặng">{SILENCE_PRESETS.map(p => <button key={p.id} aria-pressed={selectedPreset?.id === p.id} onClick={() => { onDetection(p.threshold, p.minimum); setPadding(p.padding); setMessage(''); setPage(0) }}>{selectedPreset?.id === p.id && <Check size={12} />}{p.name}</button>)}</div>
      <label className="tl-silence-check"><input type="checkbox" checked={trimEdges} onChange={e => { setTrimEdges(e.target.checked); setMessage('') }} />Cắt cả khoảng trống đầu/cuối</label>
    </div>
    <p className="tl-silence-explain">{source === 'voices' ? 'Giữ nguyên từng câu đọc, chỉ rút ngắn khoảng giữa các câu. Nhạc nền không ảnh hưởng kết quả.' : 'Dò mức âm thanh trong file nguồn, không phụ thuộc thanh âm lượng. Tự giữ chỗ có thuyết minh; nhạc lẫn trong âm gốc có thể làm khó phát hiện khoảng nghỉ.'}</p>
    <details className="tl-silence-advanced"><summary>Tinh chỉnh nhịp cắt{!selectedPreset ? ' · Tùy chỉnh' : ''}</summary><div>
      {source !== 'voices' && <label>Ngưỡng im lặng<input aria-label="Ngưỡng khoảng lặng" type="range" min={-60} max={-20} value={threshold} onChange={e => onDetection(Number(e.target.value), minimum)} /><span>{threshold} dB</span></label>}
      <label>Nghỉ ít nhất<input type="number" aria-label="Khoảng nghỉ tối thiểu" min={.1} max={5} step={.05} value={minimum} onChange={e => { if (e.target.value && Number.isFinite(e.target.valueAsNumber)) onDetection(threshold, Math.max(.1, Math.min(5, e.target.valueAsNumber))) }} />giây</label>
      <label>Giữ đệm mỗi đầu<input type="number" aria-label="Đệm bảo vệ đầu cuối câu" min={0} max={.5} step={.02} value={padding} onChange={e => { if (e.target.value && Number.isFinite(e.target.valueAsNumber)) setPadding(Math.max(0, Math.min(.5, e.target.valueAsNumber))) }} />giây</label>
    </div></details>
    {plan.error ? <p role="alert">{plan.error}</p> : !plan.sourceCount ? <p className="tl-silence-empty">{source === 'voices' ? 'Chưa có giọng đọc đang bật. Chọn Âm gốc hoặc nạp giọng vào timeline.' : source === 'selection' ? 'Chọn một đoạn hình hoặc âm thanh trên timeline để phân tích.' : 'Chưa có lớp âm gốc đang bật. Chọn Giọng đọc hoặc một đoạn trên timeline.'}</p> : plan.missingIds.length ? <div className="tl-silence-empty"><span>Cần đọc dạng sóng của {plan.missingIds.length} tư liệu. Phân tích một lần, dùng lại khi cắt.</span><button disabled={busy} onClick={() => onAnalyze(plan.missingIds)}><Waves size={14} />{busy ? 'Đang xử lý…' : 'Phân tích âm thanh'}</button></div> : <>
      <div className="tl-silence-summary"><div><strong>{selected.length} / {plan.ranges.length} khoảng được chọn</strong><span>{clock(total)} → {clock(Math.max(0, total - removed))} · Giảm {removed.toFixed(2)} giây</span></div><button className="tl-primary" disabled={busy || !selected.length || !!locked.length} onClick={apply}><Scissors size={14} />Cắt {selected.length} khoảng đã chọn</button></div>
      {locked.length > 0 && <p role="alert">Mở khóa lớp {locked.map(t => t.name).join(', ')} trước để các lớp nối lại đồng bộ.</p>}
      {plan.ranges.length ? <>
        <div className="tl-silence-list-actions"><button onClick={() => setSelection({ plan, excluded: new Set() })}>Chọn tất cả</button><button onClick={() => setSelection({ plan, excluded: new Set(plan.ranges.map(r => r.id)) })}>Giữ tất cả</button><span>Bỏ dấu chọn ở khoảng muốn giữ. Nghe thử gồm cả phần trước và sau khoảng nghỉ.</span></div>
        <div className="tl-silence-list">{shown.map(r => <div key={r.id} data-kept={excluded.has(r.id)}>
          <input type="checkbox" aria-label={`Cắt khoảng ${clock(r.start)} đến ${clock(r.end)}`} checked={!excluded.has(r.id)} onChange={() => toggle(r.id)} />
          <button className="tl-silence-time" onClick={() => onRange(r, false)} title="Hiện khoảng này trên timeline">{clock(r.start)} — {clock(r.end)}</button><span>−{(r.end - r.start).toFixed(2)}s</span>
          <button aria-label={`Nghe thử khoảng ${clock(r.start)}`} onClick={() => onRange({ start: Math.max(0, r.start - .6), end: Math.min(total, r.end + .6) }, true)}><Play size={12} />Nghe thử</button>
        </div>)}</div>
        {pageCount > 1 && <div className="tl-silence-pagination"><button disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>Trước</button><span>{currentPage + 1} / {pageCount}</span><button disabled={currentPage + 1 >= pageCount} onClick={() => setPage(currentPage + 1)}>Tiếp</button></div>}
      </> : <p className="tl-silence-empty">Không còn khoảng nghỉ phù hợp với mức này. Thử “Rất gọn”, giảm thời gian nghỉ tối thiểu hoặc đổi nguồn dò.</p>}
    </>}
    {message && messageDuration !== null && Math.abs(total - messageDuration) < 1e-6 && <p className="tl-silence-success" role="status">{message}</p>}
  </section>
}
