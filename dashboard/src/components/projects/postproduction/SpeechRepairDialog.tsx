import { useState } from 'react'
import { Button } from '../../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../../ui/dialog'
import type { SpeechDocument, SpeechState } from './speechTypes'
import { fitTiming, matchingAudio, shorterBudget, splitCue, suggestedSplit, validateRepair } from './speechRepair'

export interface RepairSelection { mode: 'fit' | 'shorten' | 'split'; id?: string }
interface Props {
  selection: RepairSelection; document: SpeechDocument; state: SpeechState; busy: boolean
  onClose: () => void; onApply: (doc: SpeechDocument, generateIds?: string[]) => void
}
const time = (value: number) => value.toFixed(2)

export function SpeechRepairDialog({ selection, document, state, busy, onClose, onApply }: Props) {
  const cue = document.cues.find(item => item.id === selection.id)
  const [ripple, setRipple] = useState(false)
  const [shortened, setShortened] = useState(cue?.text ?? '')
  const [parts, setParts] = useState<[string, string]>(() => suggestedSplit(cue?.text ?? ''))
  const [splitIds] = useState(() => [crypto.randomUUID(), crypto.randomUUID()])
  const durations = new Map(document.cues.flatMap(item => {
    const audio = matchingAudio(item, state)
    return audio?.duration ? [[item.id, audio.duration] as const] : []
  }))
  const plan = fitTiming(document, durations, state.duration, ripple, selection.id)
  const audioDuration = cue ? durations.get(cue.id) : undefined
  const budget = cue ? shorterBudget(cue, audioDuration) : 0
  let replacement = document
  let error = ''
  if (selection.mode === 'fit') { replacement = plan.document; error = plan.error }
  else if (!cue) error = 'Không tìm thấy câu đang sửa.'
  else if (selection.mode === 'split') {
    let index = 0
    try { replacement = splitCue(document, cue.id, parts, () => splitIds[index++]) } catch (cause) { error = (cause as Error).message }
  } else {
    if (!shortened.trim() || shortened.trim().length > 1500) error = 'Lời rút gọn cần có từ 1 đến 1.500 ký tự.'
    replacement = { ...document, cues: document.cues.map(item => item.id === cue.id ? { ...item, text: shortened.trim() } : item) }
  }
  error ||= validateRepair(replacement, state.duration)
  const changed = selection.mode === 'fit' ? plan.changes.length > 0 : selection.mode === 'split' || shortened.trim() !== cue?.text.trim()
  const generatedIds = selection.mode === 'split'
    ? replacement.cues.filter(item => !document.cues.some(old => old.id === item.id)).map(item => item.id)
    : cue ? [cue.id] : []
  const splitParts = selection.mode === 'split' ? replacement.cues.filter(item => generatedIds.includes(item.id)) : []
  const title = selection.mode === 'fit' ? 'Tự căn thời lượng' : selection.mode === 'split' ? 'Tách thành hai câu' : 'Rút gọn lời'
  return <Dialog open onOpenChange={open => { if (!open && !busy) onClose() }}>
    <DialogContent className="post-production pp-repair-dialog sm:max-w-2xl" showCloseButton={!busy}>
      <DialogHeader><DialogTitle>{title}{cue && ` · Câu ${document.cues.indexOf(cue) + 1}`}</DialogTitle>
        <DialogDescription>Xem trước thay đổi rồi áp dụng vào bản nháp. Bạn có thể hoàn tác trước khi lưu.</DialogDescription>
      </DialogHeader>
      {selection.mode === 'fit' ? <>
        <p>Dùng thời lượng âm thanh đã tạo. Đổi mốc sẽ tái sử dụng giọng đọc. {durations.size}/{document.cues.length} câu có âm thanh phù hợp với nội dung hiện tại.</p>
        <label className="pp-repair-option"><input type="checkbox" checked={ripple} onChange={e => setRipple(e.target.checked)} disabled={busy} />Cho phép dời các câu phía sau</label>
        <p className="pp-workbench-notice">{ripple ? 'Các câu bị vướng sẽ lùi đến khi hết chồng lấn; có thể lệch lời với hình trong video gốc. Hãy xem lại các mốc thay đổi.' : 'Giữ mốc bắt đầu các câu. Chỉ dùng khoảng trống trước câu kế tiếp.'}</p>
        <p>{plan.changes.length} câu thay đổi · Cuối lời đọc: {time(plan.document.cues.at(-1)?.end ?? 0)}s / video {time(state.duration)}s.</p>
        {plan.changes.length > 0 && <div className="pp-repair-table"><table><thead><tr><th>Câu</th><th>Trước</th><th>Sau</th></tr></thead><tbody>
          {plan.changes.map(change => <tr key={change.before.id}><td>{change.index + 1} · {change.before.speaker}</td><td>{time(change.before.start)}–{time(change.before.end)}s</td><td>{time(change.after.start)}–{time(change.after.end)}s</td></tr>)}
        </tbody></table></div>}
        {!changed && !error && <p>Chưa có câu cần kéo dài. Tạo lời đọc trước nếu câu chưa có âm thanh.</p>}
      </> : cue && <>
        <p>Khung hiện tại {time(cue.end - cue.start)}s{audioDuration ? ` · Lời đọc ${time(audioDuration)}s` : ''}.</p>
        {selection.mode === 'shorten' ? <>
          <blockquote className="pp-repair-original">{cue.text}</blockquote>
          <label>Lời rút gọn<textarea aria-label="Lời rút gọn" value={shortened} onChange={e => setShortened(e.target.value)} rows={4} maxLength={1500} disabled={busy} /></label>
          <p>{shortened.trim().length} ký tự · Mục tiêu khoảng {budget} ký tự để vừa khung. Giữ ý chính và cách xưng hô khi sửa.</p>
          <p className="pp-workbench-notice">Đây là ước lượng theo độ dài lời và tốc độ đọc. Tạo lại câu để kiểm tra thời lượng thực tế.</p>
        </> : <>
          {parts.map((part, index) => <label key={index}>Phần {index + 1}<textarea aria-label={`Nội dung phần ${index + 1}`} rows={3} maxLength={1500} value={part} disabled={busy} onChange={e => setParts(current => index === 0 ? [e.target.value, current[1]] : [current[0], e.target.value])} /></label>)}
          {splitParts.map((part, index) => <p key={part.id}>Phần {index + 1}: {time(part.start)}–{time(part.end)}s · {Math.round(part.text.length / (part.end - part.start))} ký tự/giây.</p>)}
          <p className="pp-workbench-notice">Chia khung theo độ dài hai phần, giữ người nói và giọng. Tách câu không tăng tổng thời gian; nếu lời đọc vẫn dài, cần rút gọn hoặc căn lại mốc sau khi tạo giọng.</p>
        </>}
      </>}
      {error && <p role="alert">{error}</p>}
      <div className="pp-repair-footer">
        <Button variant="ghost" disabled={busy} onClick={onClose}>Đóng</Button>
        <Button variant="outline" disabled={busy || !!error || !changed} onClick={() => onApply(replacement)}>Áp dụng vào bản nháp</Button>
        {selection.mode !== 'fit' && <Button disabled={busy || !!error || !changed || !cue?.voice_id} onClick={() => onApply(replacement, generatedIds)}>{selection.mode === 'split' ? 'Lưu & tạo hai câu' : 'Lưu & tạo lại câu'}</Button>}
      </div>
      {selection.mode !== 'fit' && <p>Nút “Lưu & tạo” lưu toàn bộ bản nháp hiện tại và chỉ tạo giọng cho câu vừa sửa. {!cue?.voice_id && 'Chọn giọng để dùng nút này.'}</p>}
    </DialogContent>
  </Dialog>
}
