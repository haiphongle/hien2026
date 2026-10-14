import { useState } from 'react'
import { Button } from '../ui/button'
import { applyShotDirection, cameraDirection, cameraPresets, cameraStrengths, emotions, gestures, type CameraStrength, type Motion, type ShotDirection, type StickmanScene, type StoryCharacter, type StoryAsset, type CharacterRole } from './stickmanOptions'

const control = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

export function StickmanSceneCard({ scene, index, disabled, dialogue, motion, context, characters, assets, versions, canDelete, onChange, onDelete, onSuggest }: {
  scene: StickmanScene; index: number; disabled: boolean; dialogue: boolean; motion: Motion; context: string
  versions: ShotDirection[]; canDelete: boolean
  characters: StoryCharacter[]; assets: StoryAsset[]
  onChange: (change: Partial<StickmanScene>, remember?: boolean) => void; onDelete: () => void
  onSuggest: () => Promise<StickmanScene>
}) {
  const [proposed, setProposed] = useState<{ key: string; scene: StickmanScene } | null>(null)
  const [thinking, setThinking] = useState(false), [error, setError] = useState('')
  const key = JSON.stringify([scene, context])
  const fresh = proposed?.key === key
  const number = index + 1
  const preset = cameraPresets.find(p => scene.camera?.startsWith(p.prompt))
  const strength: CameraStrength = scene.camera?.includes('Pronounced') ? 'strong' : scene.camera?.includes('Moderate') ? 'normal' : 'gentle'
  async function suggest() {
    setThinking(true); setError(''); setProposed(null)
    try { setProposed({ key, scene: await onSuggest() }) }
    catch (cause) { if (!(cause instanceof DOMException && cause.name === 'AbortError')) setError(String(cause)) }
    finally { setThinking(false) }
  }
  return <section className="space-y-3 rounded-xl border p-4" style={{ borderColor: 'var(--border)', background: 'var(--surface)' }} aria-label={`Kịch bản cảnh ${number}`}>
    <div className="flex flex-wrap items-center gap-2">
      <strong className="text-sm">Cảnh {number}</strong>
      {scene.cue_start && <span className="text-xs" style={{ color: 'var(--muted)' }}>SRT {scene.cue_start}–{scene.cue_end}</span>}
      <span className="rounded-full border px-2 py-1 text-[11px]" style={{ borderColor: 'var(--border)', color: 'var(--muted)' }}>{motion === 'ai' ? 'Clip hoạt hình AI' : motion === 'talk' ? 'Vector thử nghiệm' : 'Ảnh chuyển động'}</span>
      <Button type="button" variant="outline" size="sm" className="ml-auto" disabled={disabled || scene.action.trim().length < 3 || scene.narration.trim().length < 3} onClick={() => void suggest()}>{thinking ? 'AI đang cải thiện…' : 'AI cải thiện cảnh này'}</Button>
      <Button type="button" variant="ghost" size="sm" disabled={disabled || !canDelete} onClick={onDelete}>Xóa cảnh</Button>
    </div>
    <div className="grid gap-3 md:grid-cols-2">
      <label className="text-xs font-medium">Lời đọc<textarea aria-label={`Lời đọc cảnh ${number}`} disabled={disabled} rows={3} maxLength={450} value={scene.narration} onChange={e => onChange({ narration: e.target.value })} className="mt-1 block w-full rounded-lg p-3 text-sm font-normal" style={control} /></label>
      <label className="text-xs font-medium">Hình ảnh dự kiến<textarea aria-label={`Hành động cảnh ${number}`} disabled={disabled} rows={3} maxLength={1000} value={scene.action} onChange={e => onChange({ action: e.target.value })} className="mt-1 block w-full rounded-lg p-3 text-sm font-normal" style={control} /></label>
    </div>
    <div className="flex flex-wrap items-center gap-3 text-xs">
      <label>Loại cảnh<select aria-label={`Loại cảnh ${number}`} disabled={disabled} value={scene.shot_kind || 'story'} onChange={e => onChange({ shot_kind: e.target.value as StickmanScene['shot_kind'], ...(['environment', 'detail'].includes(e.target.value) ? { cast: [], extras: '' } : {}) })} className="ml-2 rounded p-2" style={control}><option value="story">Câu chuyện / hành động</option><option value="environment">Toàn cảnh bối cảnh</option><option value="detail">Cận cảnh đồ vật</option><option value="metaphor">Hình ảnh ẩn dụ</option></select></label>
      <span>Nhân vật trong hình:</span>
      {(characters.length ? characters : [{ role: 'A', name: 'A' }, { role: 'B', name: 'B' }]).map(c => {
        const role = c.role as CharacterRole
        const cast = scene.cast ?? (dialogue ? ['A', 'B'] : ['A'])
        return <label key={role} className="flex items-center gap-1"><input type="checkbox" aria-label={`Nhân vật ${role} trong cảnh ${number}`} disabled={disabled} checked={cast.includes(role)} onChange={e => onChange({ cast: e.target.checked ? [...cast as CharacterRole[], role] : cast.filter(r => r !== role) as CharacterRole[], ...(e.target.checked && ['environment', 'detail'].includes(scene.shot_kind || '') ? { shot_kind: 'story' as const } : {}) })} />{role} · {c.name}</label>
      })}
      {scene.cast?.length === 0 && <span style={{ color: 'var(--muted)' }}>Không có nhân vật chính</span>}
      {dialogue && <label>Người nói<select aria-label={`Người nói cảnh ${number}`} disabled={disabled} value={scene.speaker} onChange={e => onChange({ speaker: e.target.value as 'A' | 'B' })} className="ml-2 rounded p-2" style={control}><option>A</option><option>B</option></select></label>}
      <span style={{ color: 'var(--muted)' }}>Thời lượng căn theo lời đọc thực tế</span>
    </div>
    {error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
    {proposed && <div className="space-y-3 rounded-lg border p-3" style={{ borderColor: 'var(--accent)' }}>
      <p className="text-xs font-medium">So sánh gợi ý AI · Lời đọc và nhân vật được giữ nguyên</p>
      <div className="grid gap-3 md:grid-cols-2 text-xs leading-relaxed">
        {[{ title: 'Đang dùng', shot: scene }, { title: 'AI đề xuất', shot: proposed.scene }].map(({ title, shot }) => <div key={title} className="space-y-2"><strong>{title}</strong><p className="whitespace-pre-wrap">{shot.action}</p><p style={{ color: 'var(--muted)' }}>{shot.camera}</p><ol className="list-inside list-decimal space-y-1">{shot.motion_beats?.map((beat, i) => <li key={i}>{beat}</li>)}</ol></div>)}
      </div>
      {!fresh && <p role="status" className="text-xs">Cảnh hoặc tham chiếu đã thay đổi. Bấm AI cải thiện để lấy gợi ý mới.</p>}
      <div className="flex gap-2"><Button type="button" size="sm" disabled={disabled || !fresh} onClick={() => { onChange(applyShotDirection(scene, proposed.scene), true); setProposed(null) }}>Áp dụng gợi ý</Button><Button type="button" variant="ghost" size="sm" onClick={() => setProposed(null)}>Bỏ gợi ý</Button></div>
    </div>}
    <details className="rounded-lg border p-3 text-xs" style={{ borderColor: 'var(--border)' }}>
      <summary className="cursor-pointer font-medium">Bối cảnh, camera và chuyển động{scene.motion_beats?.length ? ' · 3 nhịp' : ''}</summary>
      <div className="mt-3 space-y-3">
        <label className="block">Người phụ / đám đông<textarea aria-label={`Người phụ cảnh ${number}`} disabled={disabled} rows={2} maxLength={200} value={scene.extras || ''} onChange={e => onChange({ extras: e.target.value, ...(e.target.value && ['environment', 'detail'].includes(scene.shot_kind || '') ? { shot_kind: 'story' as const } : {}) })} className="mt-1 w-full rounded p-2" style={control} /></label>
        {!!assets.length && <div className="flex flex-wrap gap-3">{assets.map(asset => <label key={asset.key} className="flex items-center gap-1"><input type="checkbox" disabled={disabled || !scene.assets?.includes(asset.key) && (scene.assets?.length || 0) >= 2} checked={scene.assets?.includes(asset.key) || false} onChange={e => onChange({ assets: e.target.checked ? [...scene.assets || [], asset.key] : scene.assets?.filter(key => key !== asset.key) })} />{asset.name}</label>)}</div>}
        {([['environment', 'Địa điểm / thời gian', 400], ['staging', 'Bố trí và đồ vật', 400], ['lighting', 'Ánh sáng', 200], ['continuity', 'Liên tục với cảnh trước', 400]] as const).map(([field, label, limit]) => <label key={field} className="block">{label}<textarea aria-label={`${label} cảnh ${number}`} disabled={disabled} rows={2} maxLength={limit} value={scene[field] || ''} onChange={e => onChange({ [field]: e.target.value })} className="mt-1 w-full rounded p-2" style={control} /></label>)}
        <div className="flex flex-wrap gap-2">{cameraPresets.map(p => <button key={p.id} type="button" disabled={disabled} aria-label={`Camera cảnh ${number}: ${p.name}`} aria-pressed={preset?.id === p.id} onClick={() => onChange({ camera: cameraDirection(p.id, strength) })} className="rounded-lg border px-3 py-2 disabled:opacity-50" style={{ borderColor: preset?.id === p.id ? 'var(--accent)' : 'var(--border)', color: preset?.id === p.id ? 'var(--accent)' : 'var(--text)' }}>{p.name}</button>)}</div>
        {preset && <div className="flex items-center gap-4 rounded-lg p-3" style={{ background: 'var(--bg)' }}>
          <svg viewBox="0 0 100 60" width="100" height="60" role="img" aria-label={`Minh họa ${preset.name}`} className="shrink-0" style={{ color: 'var(--accent)' }}><rect x="2" y="2" width="96" height="56" rx="4" fill="none" stroke="currentColor" strokeDasharray="3 3" /><rect x={preset.id === 'close' ? 30 : 18} y={preset.id === 'close' ? 8 : 12} width={preset.id === 'close' ? 40 : 64} height={preset.id === 'close' ? 44 : 36} rx="4" fill="currentColor" opacity=".12" /><circle cx="50" cy="23" r="5" fill="none" stroke="currentColor" /><path d="M50 28v15m0-10-9 4m9-4 9 4m-9 6-7 8m7-8 7 8" fill="none" stroke="currentColor" />{['push', 'pull', 'pan'].includes(preset.id) && <text x="80" y="34" fill="currentColor" fontSize="20">{preset.id === 'push' ? '↘' : preset.id === 'pull' ? '↖' : '→'}</text>}</svg>
          <div className="space-y-2"><p>{preset.name} · Minh họa hướng máy</p>{['push', 'pull', 'pan'].includes(preset.id) && <label>Cường độ<select aria-label={`Cường độ camera cảnh ${number}`} disabled={disabled} value={strength} onChange={e => onChange({ camera: cameraDirection(preset.id, e.target.value as CameraStrength) })} className="ml-2 rounded p-2" style={control}>{Object.entries(cameraStrengths).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>}</div>
        </div>}
        <label className="block">Prompt camera<input aria-label={`Camera cảnh ${number}`} disabled={disabled} maxLength={180} value={scene.camera || ''} onChange={e => onChange({ camera: e.target.value })} className="mt-1 w-full rounded p-2" style={control} /></label>
        <p style={{ color: 'var(--muted)' }}>{motion === 'ai' ? 'Camera và ba nhịp được gửi trong prompt clip. AI quyết định chuyển động thực tế.' : 'Camera giúp bố cục ảnh; chuyển động khi xuất theo chế độ ảnh/vector đã chọn.'}</p>
        {[0, 1, 2].map(part => <label key={part} className="block">{['0–3s · Bắt đầu', '3–6s · Phát triển', '6–8s · Kết thúc'][part]}<textarea aria-label={`Chuyển động cảnh ${number} nhịp ${part+1}`} disabled={disabled} rows={2} maxLength={450} value={scene.motion_beats?.[part] || ''} onChange={e => onChange({ motion_beats: [0, 1, 2].map(i => i === part ? e.target.value : scene.motion_beats?.[i] || '') })} className="mt-1 block w-full rounded p-2" style={control} /></label>)}
        {['ai', 'talk'].includes(motion) && <div className="flex flex-wrap gap-3"><label>Biểu cảm<select aria-label={`Biểu cảm cảnh ${number}`} disabled={disabled} value={scene.emotion} onChange={e => onChange({ emotion: e.target.value as StickmanScene['emotion'] })} className="ml-2 rounded p-2" style={control}>{Object.entries(emotions).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label><label>Cử chỉ<select aria-label={`Cử chỉ cảnh ${number}`} disabled={disabled} value={scene.gesture} onChange={e => onChange({ gesture: e.target.value as StickmanScene['gesture'] })} className="ml-2 rounded p-2" style={control}>{Object.entries(gestures).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label></div>}
      </div>
    </details>
    {!!versions.length && <label className="block text-xs">Gợi ý đã lưu trên trình duyệt<select aria-label={`Phiên bản prompt cảnh ${number}`} disabled={disabled} value="" onChange={e => { if (!e.target.value) return; const previous = versions[Number(e.target.value)]; if (previous) onChange(applyShotDirection(scene, previous), true) }} className="ml-2 max-w-full rounded p-2" style={control}><option value="">Chọn lại phiên bản…</option>{versions.map((v, i) => <option key={i} value={i}>Bản {i+1} · {(v.camera || v.action).slice(0, 65)}</option>)}</select></label>}
  </section>
}
