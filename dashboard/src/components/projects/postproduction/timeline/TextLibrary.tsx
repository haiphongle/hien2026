import { Plus, Type } from 'lucide-react'
import { TEXT_TEMPLATES } from './textTitles'

export function TextLibrary({ onAdd }: { onAdd: (template: string) => void }) {
  return <div className="tl-assets tl-text-library">
    <button className="tl-text-add" onClick={() => onAdd('free')}><Plus size={16} />Thêm chữ tự do</button>
    <p className="tl-help">Thêm tại vạch phát · kéo chữ trên video để đặt vị trí, kéo góc để đổi cỡ.</p>
    <strong className="tl-text-heading"><Type size={14} />Mẫu giới thiệu</strong>
    {TEXT_TEMPLATES.map(t => <button key={t.id} className={`tl-text-template ${t.id}`} onClick={() => onAdd(t.id)} aria-label={`Thêm mẫu ${t.name}`}>
      <span className="tl-text-sample" style={{ color: t.style.color, fontFamily: t.style.font_id === 'serif' ? 'Georgia, serif' : undefined }}>{t.sample}</span>
      <span className="tl-text-template-label"><strong>{t.name}</strong><small>{t.note}</small></span>
    </button>)}
  </div>
}
