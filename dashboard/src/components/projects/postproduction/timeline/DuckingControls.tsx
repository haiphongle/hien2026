import type { Document, Ducking } from './types'

export function DuckingControls({ doc, commit }: { doc: Document; commit: (doc: Document) => unknown }) {
  const value: Ducking = { enabled: false, amount: .25, attack: .2, release: .35, original_amount: 1, detect_pauses: false, ...doc.ducking }
  const patch = (change: Partial<Ducking>) => commit({...doc,ducking:{...value,...change}})
  return <div className="tl-audio-options">
    <label><input type="checkbox" checked={value.enabled} onChange={e=>patch({enabled:e.target.checked})}/>Tự giảm âm khi có lời kể</label>
    {value.enabled&&<>
      <label>Nhạc còn <input aria-label="Mức nhạc khi có giọng" type="range" min={0} max={100} value={value.amount*100} onChange={e=>patch({amount:Number(e.target.value)/100})}/>{Math.round(value.amount*100)}%</label>
      <label>Tiếng chính còn <input aria-label="Mức tiếng chính khi có giọng" type="range" min={0} max={100} value={value.original_amount!*100} onChange={e=>patch({original_amount:Number(e.target.value)/100})}/>{Math.round(value.original_amount!*100)}%</label>
      <label title="Phân tích dạng sóng để nhạc và tiếng chính trở lại khi người kể nghỉ. Khi chưa phân tích xong, áp dụng theo toàn đoạn lời kể."><input type="checkbox" checked={value.detect_pauses} onChange={e=>patch({detect_pauses:e.target.checked})}/>Tăng lại khi người kể nghỉ</label>
    </>}
    <span>S = nghe riêng lớp · M = tắt lớp</span>
  </div>
}
