import type { Clip, Document } from './types';
import { DEFAULT_CAPTION_STYLE } from '../editingTypes'
import { DEFAULT_TITLE_STYLE } from './textTitles'
import { resolveMediaUrl } from '../../../../api/client'
export function LayerInspector({ videoId, clip, doc, fonts, patch, speed, applyCaptionStyle }: {
    videoId: string;
    clip: Clip;
    doc: Document;
    fonts: {
        id: string;
        name: string;
    }[];
    patch: (changes: Partial<Clip>) => void;
    speed: (value: number) => void;
    applyCaptionStyle: () => void;
}) {
    const kind = doc.tracks.find(t => t.id === clip.track_id)?.kind;
    const number = (label: string, value: number, min: number, max: number, onChange: (n: number) => void, step = .01) => <label className="tl-field"><span>{label} · {Number(value.toFixed(2))}</span><input type="range" aria-label={label} value={value} min={min} max={max} step={step} onChange={e => onChange(Number(e.target.value))}/></label>;
    return <>
    {(kind === 'video' || kind === 'overlay' || kind === 'audio') && <label className="tl-field"><span>Tốc độ đoạn (hình và tiếng đi cùng)</span><select aria-label="Tốc độ đoạn" value={clip.speed ?? 1} onChange={e => speed(Number(e.target.value))}>{![.5, .75, 1, 1.25, 1.5, 2].includes(clip.speed ?? 1) && <option value={clip.speed}>{Number((clip.speed ?? 1).toFixed(3))}× · tự căn</option>}{[.5, .75, 1, 1.25, 1.5, 2].map(n => <option key={n} value={n}>{n}×</option>)}</select></label>}
    {kind === 'audio' && <>
      <label><input type="checkbox" checked={!!clip.muted} onChange={e=>patch({muted:e.target.checked})}/> Tắt tiếng đoạn này</label>
      {number('Fade vào (giây)', clip.fade_in ?? 0, 0, Math.max(0, clip.duration - (clip.fade_out ?? 0)), n => patch({ fade_in: n }))}
      {number('Fade ra (giây)', clip.fade_out ?? 0, 0, Math.max(0, clip.duration - (clip.fade_in ?? 0)), n => patch({ fade_out: n }))}
      <p className="tl-help">Kéo đường xanh để chỉnh âm lượng. Kéo chấm ở hai đầu để fade. Bấm đúp trên đường âm lượng để thêm điểm; bấm đúp điểm để xóa.</p>
      {!!clip.envelope?.length && <button onClick={() => patch({ envelope: [] })}>Bỏ các điểm âm lượng</button>}
      <label className="tl-field"><span>Vai trò lớp âm thanh</span><small>{doc.tracks.find(t => t.id === clip.track_id)?.role ?? 'original'} · chọn Giọng đọc để tự giảm nhạc</small></label>
    </>}
    {(kind === 'caption' || kind === 'text') && clip.caption && <>
      <label className="tl-field"><span>{kind === 'text' ? 'Nội dung chữ' : 'Nội dung phụ đề'}</span><textarea aria-label={kind === 'text' ? 'Nội dung chữ' : 'Nội dung phụ đề'} maxLength={1500} rows={3} value={clip.text ?? ''} onChange={e => patch({ text: e.target.value })}/></label>
      {kind === 'text' && <div className="tl-font-samples" aria-label="Mẫu font chữ">{fonts.map(f => <button key={f.id} aria-label={`Chọn font ${f.name}`} aria-pressed={clip.caption!.font_id === f.id} onClick={() => patch({ caption: { ...clip.caption!, font_id: f.id } })}><img src={resolveMediaUrl(`/api/videos/${videoId}/timeline/fonts/${f.id}/preview`)} alt={`Aa · Tiếng Việt — ${f.name}`} /><span>{f.name}</span></button>)}</div>}
      <label className="tl-field"><span>Font chữ</span><select aria-label="Font phụ đề" value={clip.caption.font_id} onChange={e => patch({ caption: { ...clip.caption!, font_id: e.target.value } })}>{fonts.map(f => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
      {number('Cỡ chữ (% chiều cao)', clip.caption.size, 1, 30, n => patch({ caption: { ...clip.caption!, size: n } }), .1)}
      <div className="tl-color-fields"><label>Màu chữ<input aria-label="Màu phụ đề" type="color" value={clip.caption.color} onChange={e => patch({ caption: { ...clip.caption!, color: e.target.value } })}/></label><label>Nền chữ<input aria-label="Nền phụ đề" type="color" value={clip.caption.background_color} onChange={e => patch({ caption: { ...clip.caption!, background_color: e.target.value } })}/></label></div>
      {number('Độ đậm nền chữ', clip.caption.background_opacity, 0, 1, n => patch({ caption: { ...clip.caption!, background_opacity: n } }))}
      <label className="tl-field"><span>Màu viền chữ</span><input aria-label="Màu viền chữ" type="color" value={clip.caption.outline_color} onChange={e => patch({caption:{...clip.caption!,outline_color:e.target.value}})}/></label>
      {number('Độ dày viền chữ', clip.caption.outline, 0, 5, n => patch({caption:{...clip.caption!,outline:n}}), 1)}
      {number('Vị trí ngang', clip.caption.x, 0, 1, n => patch({ caption: { ...clip.caption!, x: n } }))}
      {number('Vị trí dọc', clip.caption.y, 0, 1, n => patch({ caption: { ...clip.caption!, y: n } }))}
      <label className="tl-field"><span>Căn chữ</span><select value={clip.caption.align} onChange={e => patch({ caption: { ...clip.caption!, align: e.target.value as 'left' | 'center' | 'right' } })}><option value="left">Trái</option><option value="center">Giữa</option><option value="right">Phải</option></select></label>
      <p className="tl-help">Bấm chữ trên video để kéo vị trí; kéo góc để đổi cỡ. Enter để xuống dòng. Kéo hai mép đoạn trên timeline để chỉnh thời gian.</p>
      <button onClick={() => patch({caption:kind === 'text' ? {...DEFAULT_TITLE_STYLE} : {...DEFAULT_CAPTION_STYLE,x:.5,y:.85}})}>Chữ mặc định</button>
      <button onClick={applyCaptionStyle}>Áp dụng mẫu chữ cho cả lớp</button>
    </>}
    {clip.colors && <>
      {number('Độ sáng', clip.colors.brightness, -.5, .5, n => patch({ colors: { ...clip.colors!, brightness: n } }))}
      {number('Tương phản', clip.colors.contrast, .25, 2, n => patch({ colors: { ...clip.colors!, contrast: n } }))}
      {number('Bão hòa', clip.colors.saturation, 0, 2, n => patch({ colors: { ...clip.colors!, saturation: n } }))}
      <button onClick={() => patch({ colors: { brightness: 0, contrast: 1, saturation: 1 } })}>Màu mặc định</button>
    </>}
    {clip.effect && <>
      <strong className="tl-help">{clip.effect.kind === 'blur' ? 'Làm mờ' : clip.effect.kind === 'image' ? 'Ảnh / logo' : clip.effect.kind === 'delogo' ? 'Delogo' : 'Phủ màu'} · kéo trực tiếp trên video</strong>
      {number('Vùng: ngang', clip.effect.x, 0, 1 - clip.effect.width, n => patch({ effect: { ...clip.effect!, x: n } }))}
      {number('Vùng: dọc', clip.effect.y, 0, 1 - clip.effect.height, n => patch({ effect: { ...clip.effect!, y: n } }))}
      {number('Vùng: rộng', clip.effect.width, .01, 1 - clip.effect.x, n => patch({ effect: { ...clip.effect!, width: n } }))}
      {number('Vùng: cao', clip.effect.height, .01, 1 - clip.effect.y, n => patch({ effect: { ...clip.effect!, height: n } }))}
      {clip.effect.kind === 'blur' || clip.effect.kind === 'delogo' ? number('Độ mờ', clip.effect.strength, 1, 40, n => patch({ effect: { ...clip.effect!, strength: n } }), 1) : number('Độ đậm vùng', clip.effect.opacity, 0, 1, n => patch({ effect: { ...clip.effect!, opacity: n } }))}
      {clip.effect.kind === 'color' && <label className="tl-field">Màu phủ<input type="color" value={clip.effect.color} onChange={e => patch({ effect: { ...clip.effect!, color: e.target.value } })}/></label>}
    </>}
    {clip.anchor && <button onClick={() => patch({ anchor: null })}>Tách khỏi đoạn hình</button>}
  </>;
}
