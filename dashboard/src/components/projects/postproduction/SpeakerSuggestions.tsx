import { useState } from 'react'
import { Button } from '../../ui/button'
import { applySpeakers } from './speechSpeakers'
import type { SpeechDocument, SpeechVoice } from './speechTypes'

export interface SpeakerReport {
  created_at: number; revision: number; stale: boolean; speakers: string[]
  source_audio_url?: string
  cues: { id: string; text: string; start: number; end: number; speaker: string; dominance: number; coverage: number; needs_review: boolean }[]
}
interface Props {
  report: SpeakerReport; document: SpeechDocument; voices: SpeechVoice[]; disabled: boolean
  onSeek: (time: number) => void; onPlay: (url: string, offset?: number, end?: number) => void
  onApply: (document: SpeechDocument) => Promise<void>
}

export function SpeakerSuggestions({ report, document, voices, disabled, onSeek, onPlay, onApply }: Props) {
  const [assignments, setAssignments] = useState<Record<string, string>>(() => Object.fromEntries(report.cues.map(cue => [cue.id, cue.needs_review ? '' : cue.speaker])))
  const [names, setNames] = useState<Record<string, string>>({})
  const [chosenVoices, setVoices] = useState<Record<string, string>>({})
  const [page, setPage] = useState(0)
  const [error, setError] = useState('')
  const [applying, setApplying] = useState(false)
  const selected = Object.values(assignments).filter(Boolean).length
  const locked = disabled || applying
  return <div className="pp-analysis-result">
    <p>Phát hiện {report.speakers.length} nhóm giọng · {report.cues.filter(c => c.needs_review).length} câu cần nghe và chọn lại. Nhãn chỉ biểu thị nhóm giọng, chưa xác định tên nhân vật.</p>
    <div className="pp-speakers">{report.speakers.map(label => {
      const voice = voices.find(v => v.id === chosenVoices[label])
      return <div className="pp-speaker" key={label}>
        <label>{label}<input aria-label={`Tên của ${label}`} maxLength={80} value={names[label] ?? label} disabled={locked} onChange={e => setNames({ ...names, [label]: e.target.value })} /></label>
        <label>Giọng đọc<select aria-label={`Giọng gán cho ${label}`} value={chosenVoices[label] ?? ''} disabled={locked} onChange={e => setVoices({ ...chosenVoices, [label]: e.target.value })}>
          <option value="">Chọn sau trong trình sửa</option>{voices.map(v => <option key={v.id} value={v.id}>{v.name}</option>)}
        </select></label>
        {voice?.audio_url && <Button size="sm" variant="outline" onClick={() => onPlay(voice.audio_url!)}>Nghe mẫu giọng</Button>}
      </div>
    })}</div>
    <p>Đặt cùng một tên để gộp các nhóm. Những câu có nhiều giọng hoặc ít lời nói được để trống để bạn kiểm tra.</p>
    <div className="pp-translation-table"><table><thead><tr><th>Mốc / nghe</th><th>Lời thoại</th><th>Người nói</th></tr></thead><tbody>
      {report.cues.slice(page * 20, page * 20 + 20).map(cue => <tr key={cue.id}>
        <td><button type="button" onClick={() => onSeek(cue.start)}>{cue.start.toFixed(1)}–{cue.end.toFixed(1)}s</button>
          {report.source_audio_url && <Button size="sm" variant="outline" onClick={() => onPlay(report.source_audio_url!, cue.start, cue.end)}>Nghe đoạn gốc</Button>}
          {cue.needs_review && <small className="pp-analysis-warning">Cần kiểm tra</small>}</td>
        <td>{cue.text}</td><td><select aria-label={`Người nói lúc ${cue.start.toFixed(1)} giây`} disabled={locked} value={assignments[cue.id] ?? ''} onChange={e => setAssignments({ ...assignments, [cue.id]: e.target.value })}>
          <option value="">Giữ người nói hiện tại</option>{report.speakers.map(label => <option key={label} value={label}>{names[label] || label}</option>)}
        </select><small>Gợi ý: {cue.speaker || 'Chưa rõ'} · {Math.round(cue.dominance * 100)}% phần lời nói trong câu. Phát hiện lời nói ở {Math.round(cue.coverage * 100)}% thời lượng câu. Đây không phải xác suất nhận diện đúng.</small></td>
      </tr>)}
    </tbody></table></div>
    <div className="pp-candidate-toolbar"><Button size="sm" variant="outline" disabled={!page} onClick={() => setPage(page - 1)}>Trước</Button><span>{page + 1}/{Math.max(1, Math.ceil(report.cues.length / 20))}</span><Button size="sm" variant="outline" disabled={(page + 1) * 20 >= report.cues.length} onClick={() => setPage(page + 1)}>Sau</Button>
      <Button disabled={locked || !selected} onClick={async () => {
        setError(''); setApplying(true)
        try { await onApply(applySpeakers(document, assignments, names, chosenVoices)) }
        catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
        finally { setApplying(false) }
      }}>Đưa {selected} câu vào bản nháp</Button>
    </div>
    <p>Kiểm tra bản nháp rồi bấm “Lưu kịch bản & giọng”. Có thể sửa người nói từng câu hoặc hoàn tác.</p>
    {error && <p role="alert">{error}</p>}
  </div>
}
