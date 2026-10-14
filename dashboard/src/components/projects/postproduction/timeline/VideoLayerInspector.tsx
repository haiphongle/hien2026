import type { Clip, Document } from './types'
import { linkedAudio, overlayPreset, type ReviewPreset } from './videoOverlay'

export function VideoLayerInspector({ clip, doc, patch, patchAudio, solo, review }: {
  clip: Clip; doc: Document; patch: (changes: Partial<Clip>) => void;
  patchAudio: (id: string, changes: Partial<Clip>) => void; solo: (trackId: string) => void; review: (preset: ReviewPreset) => void
}) {
  const track = doc.tracks.find(t => t.id === clip.track_id)
  if (track?.kind !== 'video' && track?.kind !== 'overlay') return null
  const audio = linkedAudio(doc, clip), audioTrack = doc.tracks.find(t => t.id === audio?.track_id), value = clip.overlay
  return <section className="tl-video-settings" aria-label="Thuộc tính video và tiếng">
    {value && <>
      <strong>Khung video chèn</strong>
      <div className="tl-review-presets" aria-label="Mẫu video review">
        <button onClick={()=>review('corner')}>Người kể ở góc</button>
        <button onClick={()=>review('split')}>Hai màn hình</button>
        <button disabled={!audio} onClick={()=>review('voice')}>Chỉ nghe lời kể</button>
      </div>
      <p className="tl-help">Mẫu có lời kể sẽ bật tự giảm nhạc và tiếng chính. Có thể chỉnh lại bên dưới timeline hoặc hoàn tác.</p>
      <label className="tl-field"><span>Bố cục nhanh</span><select aria-label="Bố cục video chèn" value={value.layout === 'split' ? 'split' : ''} onChange={e=>patch({overlay:overlayPreset(value,e.target.value)})}>
        <option value="" disabled>Tự đặt vị trí</option><option value="top-left">Góc trên trái</option><option value="top-right">Góc trên phải</option><option value="bottom-left">Góc dưới trái</option><option value="bottom-right">Góc dưới phải</option><option value="split">Hai video cạnh nhau</option>
      </select></label>
      <label className="tl-field"><span>Hình trong khung</span><select aria-label="Cách vừa khung video chèn" value={value.fit} onChange={e=>patch({overlay:{...value,fit:e.target.value as 'contain'|'cover'}})}><option value="cover">Lấp đầy · cắt phần dư</option><option value="contain">Giữ trọn hình · nền đen</option></select></label>
      <label><input type="checkbox" checked={value.flip} onChange={e=>patch({overlay:{...value,flip:e.target.checked}})}/> Lật ngang</label>
      <label><input type="checkbox" checked={value.hidden} onChange={e=>patch({overlay:{...value,hidden:e.target.checked}})}/> Ẩn hình, giữ tiếng</label>
      <label className="tl-field"><span>Kiểu khung</span><select aria-label="Kiểu khung video chèn" disabled={value.layout==='split'} value={value.shape??'rectangle'} onChange={e=>patch({overlay:{...value,shape:e.target.value as 'rectangle'|'rounded'|'circle'}})}><option value="rectangle">Chữ nhật</option><option value="rounded">Bo góc</option><option value="circle">Tròn</option></select></label>
      {value.shape==='rounded'&&value.layout!=='split'&&<label className="tl-field"><span>Độ bo góc · {value.radius??12}%</span><input aria-label="Độ bo góc video chèn" type="range" min={0} max={50} step={1} value={value.radius??12} onChange={e=>patch({overlay:{...value,radius:Number(e.target.value)}})}/></label>}
      <label className="tl-field"><span>Hiện dần · {(value.entrance??0).toFixed(1)} giây</span><input aria-label="Thời gian hiện dần video chèn" type="range" min={0} max={2} step={.1} value={value.entrance??0} onChange={e=>patch({overlay:{...value,entrance:Number(e.target.value),entrance_offset:0}})}/></label>
      <label className="tl-field"><span>Viền khung · {value.border.toFixed(1)}%</span><input aria-label="Độ dày viền video chèn" type="range" min={0} max={2} step={.1} value={value.border} onChange={e=>patch({overlay:{...value,border:Number(e.target.value)}})}/></label>
      <label className="tl-field"><span>Màu viền</span><input type="color" aria-label="Màu viền video chèn" value={value.border_color} onChange={e=>patch({overlay:{...value,border_color:e.target.value}})}/></label>
      <p className="tl-help">Kéo khung trên màn hình để đặt vị trí. Kéo một trong bốn góc để đổi cỡ. Chọn một góc có sẵn để rời bố cục chia đôi.</p>
    </>}
    <strong>{value ? 'Tiếng video chèn' : 'Tiếng video chính'}</strong>
    {audio ? <fieldset className="tl-layer-fields" disabled={audioTrack?.locked}>
      <label className="tl-field"><span>Âm lượng · {Math.round(audio.gain*100)}%</span><input aria-label={value ? 'Âm lượng video chèn' : 'Âm lượng video chính'} type="range" min={0} max={200} step={5} value={Math.round(audio.gain*100)} onChange={e=>patchAudio(audio.id,{gain:Number(e.target.value)/100})}/></label>
      <label><input type="checkbox" checked={!!audio.muted} onChange={e=>patchAudio(audio.id,{muted:e.target.checked})}/> Tắt tiếng đoạn này</label>
      <button aria-pressed={!!audioTrack?.solo} onClick={()=>solo(audio.track_id)}>{audioTrack?.solo ? 'Bỏ nghe riêng lớp tiếng' : 'Nghe riêng lớp tiếng này'}</button>
      {audioTrack?.muted && <p className="tl-warning">Lớp tiếng đang tắt (M). Bật lại trên timeline để nghe.</p>}
    </fieldset> : <p className="tl-help">Đoạn này không có tiếng liên kết. Nếu đã tách liên kết, chọn đoạn trên lớp âm thanh để chỉnh.</p>}
  </section>
}
