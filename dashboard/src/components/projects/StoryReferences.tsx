import { useRef, useState } from 'react'
import { fetchAPI } from '../../api/client'
import type { Character } from '../../types'
import { Button } from '../ui/button'
import { imageBase64 } from './storyStyleLibrary'
import { ReferenceImage } from './CharacterReferenceImage'
import { characterRoles, type CharacterRole, type StoryCharacter, type StoryAsset } from './stickmanOptions'

const control = { background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)' }

export function StoryReferences({ projectId, disabled, characters, designs, selected, onSelect, onDesign, onCharacter, onBusy }: {
  projectId: string; disabled: boolean; onCharacter: (character: Character, role: CharacterRole) => void; onBusy: (busy: boolean) => void
  characters: Character[]; designs: StoryCharacter[]; selected: Partial<Record<CharacterRole, string>>
  onSelect: (role: CharacterRole, id: string) => void; onDesign: (design: StoryCharacter) => void
}) {
  const [saving, setSaving] = useState(false), [error, setError] = useState('')
  const [urls, setUrls] = useState<Partial<Record<CharacterRole, string>>>({})
  const file = useRef<HTMLInputElement>(null), role = useRef<CharacterRole>('A')
  const roles = characterRoles.filter(target => designs.some(c => c.role === target) || selected[target])
  const nextRole = characterRoles.find(target => !roles.includes(target))
  async function upload(image?: File) {
    if (!image || saving) return
    const target = role.current
    setSaving(true); onBusy(true); setError('')
    try {
      const image_base64 = await imageBase64(image)
      const name = `Nhân vật ${target} · ${image.name.replace(/\.[^.]+$/, '')}`.slice(0, 80)
      onCharacter(await fetchAPI<Character>(`/api/projects/${projectId}/characters/upload`, { method: 'POST', body: JSON.stringify({ name, image_base64 }) }), target)
    } catch (cause) { setError(String(cause)) }
    finally { setSaving(false); onBusy(false); if (file.current) file.current.value = '' }
  }
  async function importURL(target: CharacterRole) {
    const url = urls[target]?.trim()
    if (saving || !url) return
    setSaving(true); onBusy(true); setError('')
    try {
      const character = await fetchAPI<Character>(`/api/projects/${projectId}/characters/import-url`, { method: 'POST',
        body: JSON.stringify({ name: `Nhân vật ${target} · URL ${Date.now().toString(36)}`, url }) })
      onCharacter(character, target); setUrls(current => ({ ...current, [target]: '' }))
    } catch (cause) { setError(String(cause)) }
    finally { setSaving(false); onBusy(false) }
  }
  return <fieldset disabled={disabled || saving} className="space-y-3">
    <legend className="mb-2 text-sm font-medium">Nhân vật & ảnh tham chiếu</legend>
    <input ref={file} type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" onChange={e => void upload(e.target.files?.[0])} />
    <p className="text-xs" style={{ color: 'var(--muted)' }}>AI tự phân vai từ toàn bộ nội dung, tối đa 6 nhân vật. Không cần tải ảnh: ứng dụng tự tạo tham chiếu cho nhân vật xuất hiện. Người kể có thể chỉ có giọng đọc.</p>
    <div className="grid gap-3 sm:grid-cols-2">{(roles.length ? roles : ['A'] as CharacterRole[]).map(target => {
      const design = designs.find(c => c.role === target)
      const reference = characters.find(c => c.id === selected[target])
      return <section key={target} aria-label={`Hồ sơ nhân vật ${target}`} className="space-y-3 rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
        <h4 className="text-sm font-semibold">{target} · {design?.name || 'Nhân vật tùy chọn'}</h4>
        <label className="block text-xs">Ảnh tham chiếu<select aria-label={`Ảnh nhân vật ${target}`} value={selected[target] || ''} onChange={e => onSelect(target, e.target.value)} className="mt-1 w-full rounded-lg p-2" style={control}>
          <option value="">Tự tạo theo kịch bản / hồ sơ</option>
          {selected[target] && !reference && <option value={selected[target]}>Ảnh đã chọn không còn trong thư viện</option>}
          {characters.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select></label>
        {reference && <div className="overflow-hidden rounded-lg border" style={{ borderColor: 'var(--border)' }}><ReferenceImage key={`${reference.id}:${reference.reference_image_url}`} character={reference} className="h-36 w-full object-contain" /></div>}
        <Button type="button" size="sm" variant="outline" disabled={disabled || saving} onClick={() => { role.current = target; file.current?.click() }}>Tải ảnh nhân vật {target}</Button>
        <div className="flex gap-2"><input aria-label={`URL ảnh nhân vật ${target}`} type="url" maxLength={4096} placeholder="https://…/anh.png" value={urls[target] || ''} onChange={e => setUrls(current => ({ ...current, [target]: e.target.value }))} className="min-w-0 flex-1 rounded-lg p-2 text-xs" style={control} />
          <Button type="button" size="sm" variant="outline" disabled={disabled || saving || !urls[target]?.trim()} onClick={() => void importURL(target)}>Tải từ URL {target}</Button></div>
        <p className="text-xs" style={{ color: 'var(--muted)' }}>Link ảnh công khai PNG/JPEG/WebP · tối đa 10 MB. Ảnh được lưu vào thư viện để dùng lại.</p>
        {reference && <p className="text-xs" style={{ color: 'var(--muted)' }}>Dùng tạo hình từ ảnh {reference.name}. Hồ sơ dưới đây là vai trong câu chuyện; chọn “Tự tạo” nếu ảnh không phù hợp vai.</p>}
        {design ? <details className="space-y-2">
          <summary className="cursor-pointer text-xs font-medium">Hồ sơ nhân vật · {design.name}</summary>
          <label className="block text-xs">Tên / vai trò<input aria-label={`Tên nhân vật ${target}`} maxLength={60} value={design.name} onChange={e => onDesign({ ...design, name: e.target.value })} className="mt-1 w-full rounded-lg p-2" style={control} /></label>
          <div className="grid gap-2 sm:grid-cols-2"><label className="block text-xs">Vai trong câu chuyện<input aria-label={`Vai nhân vật ${target}`} maxLength={100} value={design.story_role || ''} onChange={e => onDesign({ ...design, story_role: e.target.value })} className="mt-1 w-full rounded-lg p-2" style={control} /></label>
            <label className="block text-xs">Quan hệ với nhân vật khác<input aria-label={`Quan hệ nhân vật ${target}`} maxLength={300} value={design.relationship || ''} onChange={e => onDesign({ ...design, relationship: e.target.value })} className="mt-1 w-full rounded-lg p-2" style={control} /></label></div>
          {reference && design.reference_note && <p className="text-xs" style={{ color: 'var(--muted)' }}>AI kiểm tra tham chiếu: {design.reference_note}</p>}
          <div className="grid grid-cols-2 gap-2">
            <label className="block text-xs">Giới tính<select aria-label={`Giới tính nhân vật ${target}`} value={design.gender || 'unknown'} onChange={e => onDesign({ ...design, gender: e.target.value as StoryCharacter['gender'] })} className="mt-1 w-full rounded-lg p-2" style={control}><option value="unknown">Chưa xác định</option><option value="male">Nam</option><option value="female">Nữ</option></select></label>
            <label className="block text-xs">Nhóm tuổi<select aria-label={`Nhóm tuổi nhân vật ${target}`} value={design.age_group || 'unknown'} onChange={e => onDesign({ ...design, age_group: e.target.value as StoryCharacter['age_group'] })} className="mt-1 w-full rounded-lg p-2" style={control}><option value="unknown">Chưa xác định</option><option value="child">Trẻ em</option><option value="teen">Thiếu niên</option><option value="adult">Người lớn</option><option value="elder">Người cao tuổi</option></select></label>
          </div>
          <label className="block text-xs">Trang phục cố định<input aria-label={`Trang phục nhân vật ${target}`} maxLength={350} value={design.clothing || ''} onChange={e => onDesign({ ...design, clothing: e.target.value })} className="mt-1 w-full rounded-lg p-2" style={control} /></label>
          <label className="block text-xs">Mô tả tạo hình<textarea aria-label={`Mô tả nhân vật ${target}`} rows={3} maxLength={700} value={design.description} onChange={e => onDesign({ ...design, description: e.target.value })} className="mt-1 w-full rounded-lg p-2" style={control} /></label>
        </details> : <div className="space-y-2"><p className="text-xs" style={{ color: 'var(--muted)' }}>Chưa có hồ sơ. AI sẽ nhận diện nhân vật khi lên kịch bản.</p><Button type="button" size="sm" variant="outline" disabled={disabled || saving} onClick={() => onDesign({ role: target, name: `Nhân vật ${target}`, description: 'Story character with a consistent appearance.' })}>Tự mô tả nhân vật {target}</Button></div>}
      </section>
    })}</div>
    {nextRole && <Button type="button" size="sm" variant="outline" disabled={disabled || saving} onClick={() => onDesign({ role: nextRole, name: `Nhân vật ${nextRole}`, description: 'Story character with a consistent appearance.' })}>Thêm nhân vật</Button>}
    <p className="text-xs" style={{ color: 'var(--muted)' }}>{saving ? 'Đang tải ảnh nhân vật…' : 'Bạn chỉ cần chỉnh hồ sơ hoặc chọn ảnh riêng khi muốn thay tạo hình AI đề xuất.'}</p>
    {error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
  </fieldset>
}

export function StoryWorldReferences({ assets, library, disabled, onChange }: {
  assets: StoryAsset[]; library: Character[]; disabled: boolean; onChange: (assets: StoryAsset[]) => void
}) {
  if (!assets.length) return null
  return <details className="rounded-xl border p-3" style={{ borderColor: 'var(--border)' }}>
    <summary className="cursor-pointer text-sm font-medium">Bối cảnh & đạo cụ dùng lại · {assets.length}</summary>
    <p className="my-3 text-xs" style={{ color: 'var(--muted)' }}>AI tự tạo ảnh cho các mục cần đồng nhất. Đồ vật thông thường được mô tả trong cảnh, không cần thêm ảnh tham chiếu.</p>
    <fieldset disabled={disabled} className="grid gap-3 sm:grid-cols-2">{assets.map((asset, index) => {
      const patch = (change: Partial<StoryAsset>) => onChange(assets.map((a, i) => i === index ? { ...a, ...change } : a))
      const entityType = asset.kind === 'location' ? 'location' : 'visual_asset'
      const reference = library.find(c => c.id === asset.reference_id)
      return <div key={asset.key} className="space-y-2 rounded-lg border p-3" style={{ borderColor: 'var(--border)' }}>
        <strong className="text-xs">{asset.key} · {asset.name} · {asset.kind === 'location' ? 'Địa điểm' : 'Đạo cụ'}</strong>
        <textarea aria-label={`Mô tả ${asset.name}`} rows={3} maxLength={700} value={asset.description} onChange={e => patch({ description: e.target.value })} className="w-full rounded p-2 text-xs" style={control} />
        <select aria-label={`Tham chiếu ${asset.name}`} value={asset.reference_id || ''} onChange={e => patch({ reference_id: e.target.value || null })} className="w-full rounded p-2 text-xs" style={control}>
          <option value="">Tự tạo theo mô tả</option>
          {asset.reference_id && !reference && <option value={asset.reference_id}>Ảnh không còn trong thư viện</option>}
          {library.filter(c => c.media_id && c.entity_type === entityType).map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>
        {reference && <ReferenceImage character={reference} className="h-28 w-full object-contain" />}
      </div>
    })}</fieldset>
  </details>
}
