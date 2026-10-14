import { useMemo, useState } from 'react'
import { Button } from '../../ui/button'
import { applyShortening, restoreShortening, shorteningConflicts, type ShorteningCandidate } from './speechRepair'
import { readShorteningReview, serializeShorteningReview, type ShorteningReviewDraft } from './shorteningReviewDraft'
import { speechError, type SpeechDocument } from './speechTypes'

interface Props {
  videoId: string; candidate: ShorteningCandidate; document: SpeechDocument; disabled: boolean
  onDraft: (document: SpeechDocument) => void; onSeek: (time: number) => void
}

export function SpeechShorteningReview({ videoId, candidate, document, disabled, onDraft, onSeek }: Props) {
  const originals = useMemo(() => new Map(candidate.original.cues.map(c => [c.id, c])), [candidate])
  const rows = useMemo(() => candidate.document.cues.filter(c => Object.hasOwn(candidate.selection, c.id)), [candidate])
  const storageKey = `flowkit:shortening-review:${videoId}`
  const [review, setReview] = useState(() => {
    try { return readShorteningReview(localStorage.getItem(storageKey), candidate) }
    catch { return readShorteningReview(null, candidate) }
  })
  const { texts, accepted, applied } = review
  const conflicts = useMemo(() => shorteningConflicts(document, candidate, applied), [document, candidate, applied])
  const current = useMemo(() => new Map(document.cues.map(c => [c.id, c.text])), [document.cues])
  const changes = new Map([...texts].filter(([id, text]) => accepted.has(id) && current.get(id) !== text.trim()))
  const selectedConflict = [...changes.keys()].some(id => conflicts.has(id))
  const [page, setPage] = useState(0)
  const [error, setError] = useState('')
  const [storageError, setStorageError] = useState('')
  const restore = restoreShortening(document, candidate, applied)
  function updateReview(next: ShorteningReviewDraft) {
    setReview(next)
    try { localStorage.setItem(storageKey, serializeShorteningReview(candidate, next)); setStorageError('') }
    catch { setStorageError('Không lưu được phần duyệt trong trình duyệt. Giữ trang mở và áp dụng các câu đã sửa trước khi rời trang.') }
  }
  return <div className="pp-candidate">
    <h4>Kiểm tra bản rút gọn · {accepted.size}/{rows.length} câu được chọn</h4>
    <p>Áp dụng sẽ đổi cả lời đọc và phụ đề trong bản nháp. Giữ nguyên giọng, người nói và mốc. Thời lượng dự kiến cần kiểm tra lại sau khi tạo giọng.</p>
    <p>Chữ sửa tay và lựa chọn được tự lưu trong trình duyệt này. Có thể áp dụng từng nhóm rồi quay lại duyệt tiếp.</p>
    <div className="pp-candidate-toolbar">
      <Button size="sm" variant="outline" disabled={disabled} onClick={() => updateReview({ ...review, accepted: new Set(rows.filter(c => !conflicts.has(c.id) && current.get(c.id) !== texts.get(c.id)?.trim()).map(c => c.id)) })}>Chọn câu có thể áp dụng</Button>
      <Button size="sm" variant="outline" disabled={disabled} onClick={() => updateReview({ ...review, accepted: new Set() })}>Bỏ chọn</Button>
    </div>
    <div className="pp-translation-table"><table><thead><tr><th>Chọn / mốc</th><th>Bản gốc</th><th>Lời rút gọn · có thể sửa</th></tr></thead><tbody>
      {rows.slice(page * 10, page * 10 + 10).map(cue => {
        const text = texts.get(cue.id) ?? ''
        const budget = candidate.selection[cue.id]
        return <tr key={cue.id}>
          <td><input aria-label={`Áp dụng câu của ${cue.speaker} ở ${cue.start}s`} type="checkbox" checked={accepted.has(cue.id)} disabled={disabled} onChange={e => {
            const next = new Set(accepted); if (e.target.checked) next.add(cue.id); else next.delete(cue.id)
            updateReview({ ...review, accepted: next })
          }} /><button type="button" onClick={() => onSeek(cue.start)}>{cue.start.toFixed(2)}–{cue.end.toFixed(2)}s</button><small className="block">{cue.speaker} · Khung {(cue.end - cue.start).toFixed(2)}s</small></td>
          <td>{originals.get(cue.id)?.text}</td>
          <td><textarea className="w-full" aria-label={`Lời rút gọn ở ${cue.start}s`} rows={3} maxLength={1500} value={text} disabled={disabled} onChange={e => updateReview({ ...review, texts: new Map(texts).set(cue.id, e.target.value) })} />
            {conflicts.has(cue.id) ? <small className="block text-amber-300">Câu đã đổi hoặc bị xóa trong kịch bản. Bỏ chọn hoặc rút gọn lại.</small> : current.get(cue.id) === text.trim() && <small className="block">Nội dung đã có trong kịch bản.</small>}
            <small className={text.trim().length > budget ? 'block text-amber-300' : 'block'}>{text.trim().length}/{budget} ký tự mục tiêu{ text.trim().length > budget ? ' · Vẫn dài: sửa lời hoặc tăng khung.' : ' · Cần đo lại giọng.' }</small></td>
        </tr>
      })}
    </tbody></table></div>
    <div className="pp-candidate-toolbar">
      <Button size="sm" variant="outline" disabled={page === 0} onClick={() => setPage(page - 1)}>Trước</Button>
      <span>{page + 1}/{Math.max(1, Math.ceil(rows.length / 10))}</span>
      <Button size="sm" variant="outline" disabled={(page + 1) * 10 >= rows.length} onClick={() => setPage(page + 1)}>Sau</Button>
      <Button disabled={disabled || !changes.size || selectedConflict} onClick={() => {
        try {
          onDraft(applyShortening(document, candidate, changes, applied))
          updateReview({ ...review, applied: new Map([...applied, ...[...changes].map(([id, text]) => [id, text.trim()] as const)]) })
          setError('')
        }
        catch (cause) { setError(speechError(cause)) }
      }}>Áp dụng {changes.size} câu vào bản nháp</Button>
      <Button variant="outline" disabled={disabled || !restore.count} onClick={() => onDraft(restore.document)}>Khôi phục lời trước rút gọn · {restore.count} câu</Button>
    </div>
    {selectedConflict && <p role="alert">Một số câu đã đổi. Bấm “Chọn câu có thể áp dụng” để tiếp tục với các câu còn phù hợp.</p>}
    {storageError && <p role="alert">{storageError}</p>}
    {error && <p role="alert">{error}</p>}
  </div>
}
