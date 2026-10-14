import { useEffect, useState } from 'react'
import { fetchAPI, resolveMediaUrl } from '../../api/client'
import type { Character } from '../../types'
import { Button } from '../ui/button'
import { StoryStyleEditor } from './StoryStyleEditor'
import { StoryPresetPreviewEditor, type PreviewPresentation } from './StoryPresetPreviewEditor'
import { filterStyles, profileChoice, styleCategories, styleKey, type StyleChoice, type StoryPreset, type StyleProfile } from './storyStyleLibrary'
export type { StoryPreset } from './storyStyleLibrary'

function StyleSwatch({ preset: p }: { preset: StoryPreset }) {
  const full = p.anatomy && p.anatomy !== 'stick', chibi = p.anatomy === 'chibi'
  return <svg viewBox="0 0 240 160" className="h-full w-full" role="img" aria-label={p.name} style={{ background: p.background }}>
    {p.background_type === 'scene' && <g stroke={p.ink} strokeWidth="2" opacity=".3" fill="none"><path d="M140 125V68l32-25 35 25v57M154 125V95h24v30M28 133h190" /><path d="M35 125V82m-16 13 16-32 17 32m-33 13 16-32 17 32" /></g>}
    <g stroke={p.ink} strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
      {full ? <><path d={chibi ? 'M77 82Q61 88 59 112L68 116 76 99 73 129 78 146 87 146 87 128 95 128 95 146 104 146 109 129 106 99 115 116 124 112Q122 88 106 82Z' : 'M77 62Q61 67 57 88L50 110 59 115 70 91 67 109 73 146 84 146 85 116 92 116 94 146 106 146 110 109 106 91 118 112 127 107 118 82Q114 68 101 62Z'} fill={p.ink} fillOpacity=".2" /><circle cx="89" cy={chibi ? 55 : 39} r={chibi ? 27 : 21} fill="#fff" /><path d={chibi ? 'M80 58h1m15 0h1M84 68q6 5 12 0' : 'M81 42h1m13 0h1M84 51q5 4 10 0'} stroke="#222" fill="none" /></> : <><circle cx="85" cy="46" r="18" fill="#fff" /><path d="M85 64v45m0-25L55 98m30-14 33 15M85 109l-24 31m24-31 24 31" fill="none" /><path d="M80 46h1m8 0h1M81 53q4 3 8 0" stroke="#222" fill="none" /></>}
    </g>
  </svg>
}

export function StoryStylePicker({ projectId, presets, value, profileId, characters, hasCharacters, disabled, onSelect, onBusy, onCharacter, onStyleSaved }: {
  projectId: string; presets: StoryPreset[]; value: string; profileId: string; characters: Character[]; hasCharacters: boolean
  disabled: boolean; onSelect: (choice: StyleChoice, policy: 'keep' | 'new') => void
  onBusy: (busy: boolean) => void; onCharacter: (character: Character) => void
  onStyleSaved: () => void
}) {
  const [styles, setStyles] = useState<StyleProfile[]>([]), [error, setError] = useState('')
  const [tab, setTab] = useState<'html' | 'builtin' | 'mine'>('html'), [category, setCategory] = useState(''), [query, setQuery] = useState(''), [onlyFavorites, setOnlyFavorites] = useState(false)
  const favoriteKey = `flowkit.style-favorites.${projectId}`
  const [favorites, setFavorites] = useState<string[]>(() => { try { const data = JSON.parse(localStorage.getItem(favoriteKey) || '[]'); return Array.isArray(data) ? data.filter(v => typeof v === 'string') : [] } catch { return [] } })
  const [editor, setEditor] = useState<{ initial: StyleProfile | null; sourceImage?: File } | null>(null), [pending, setPending] = useState<StyleChoice | null>(null)
  const [previewId, setPreviewId] = useState(''), [illustrations, setIllustrations] = useState<Record<string, PreviewPresentation>>({})
  useEffect(() => {
    const controller = new AbortController()
    fetchAPI<StyleProfile[]>(`/api/projects/${projectId}/stickman/styles`, { signal: controller.signal }).then(setStyles)
      .catch(cause => { if (!controller.signal.aborted) setError(String(cause)) })
    return () => controller.abort()
  }, [projectId])
  useEffect(() => { try { localStorage.setItem(favoriteKey, JSON.stringify(favorites)) } catch { /* Optional preference. */ } }, [favoriteKey, favorites])
  const putStyle = (style: StyleProfile) => setStyles(items => [...items.filter(s => s.id !== style.id), style])
  function choose(choice: StyleChoice) {
    if ((choice.profile?.id || '') === profileId && choice.preset.id === value) return
    if (hasCharacters) setPending(choice)
    else onSelect(choice, 'new')
  }
  function open(initial: StyleProfile | null) { setEditor({ initial }); onBusy(true); setError('') }
  function close() { setEditor(null); setPreviewId(''); onBusy(false) }
  const html = presets.filter(p => p.collection === 'html').map(p => ({ preset: { ...p, ...illustrations[p.id] } }))
  const previewPreset = html.find(c => c.preset.id === previewId)?.preset
  const builtin = presets.filter(p => p.collection !== 'html').map(preset => ({ preset }))
  const mine = styles.filter(s => s.status !== 'DRAFT').map(s => profileChoice(s, presets)).filter((c): c is StyleChoice => Boolean(c))
  const items = filterStyles(tab === 'html' ? html : tab === 'builtin' ? builtin : mine, query, category, favorites, onlyFavorites)
  const selected = styles.find(s => s.id === profileId)
  return <section className="space-y-3" aria-label="Thư viện phong cách">
    <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="text-sm font-medium">Thư viện phong cách</h3><Button type="button" size="sm" disabled={disabled || Boolean(editor) || Boolean(previewId)} onClick={() => open(null)}>+ Thêm mẫu</Button></div>
    {!editor && !previewId && <>
      <div className="flex flex-wrap items-center gap-2">{(['html', 'builtin', 'mine'] as const).map(id => <Button key={id} type="button" size="sm" variant={tab === id ? 'default' : 'outline'} disabled={disabled} onClick={() => setTab(id)}>{id === 'html' ? `Mẫu từ HTML · ${html.length}` : id === 'builtin' ? `Mẫu trước đây · ${builtin.length}` : `Mẫu của tôi · ${mine.length}`}</Button>)}<label className="ml-auto flex items-center gap-2 text-xs"><input type="checkbox" aria-label="Chỉ hiện phong cách yêu thích" checked={onlyFavorites} disabled={disabled} onChange={e => setOnlyFavorites(e.target.checked)} />★ Yêu thích</label></div>
      <div className="flex flex-wrap gap-2"><input aria-label="Tìm phong cách" placeholder="Tìm phong cách…" value={query} disabled={disabled} onChange={e => setQuery(e.target.value)} className="min-w-40 flex-1 rounded-lg border p-2 text-sm" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }} /><select aria-label="Nhóm phong cách" disabled={disabled} value={category} onChange={e => setCategory(e.target.value)} className="rounded-lg border p-2 text-sm" style={{ background: 'var(--surface)', borderColor: 'var(--border)' }}><option value="">Tất cả nhóm</option>{Object.entries(styleCategories).map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></div>
      <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-4">{items.map(choice => {
        const p = choice.preset, own = choice.profile, id = styleKey(choice), favorite = favorites.includes(id)
        const active = own ? own.id === profileId : !profileId && value === p.id
        const name = own?.name || p.name
        return <article key={id} className="relative overflow-hidden rounded-xl border-2" style={{ borderColor: active ? 'var(--accent)' : 'var(--border)', background: 'var(--surface)' }}>
          <button type="button" aria-label={`Yêu thích ${name}`} aria-pressed={favorite} disabled={disabled} onClick={() => setFavorites(values => favorite ? values.filter(v => v !== id) : [...values, id])} className="absolute right-2 top-2 z-10 rounded-full bg-black/60 px-2 py-1 text-sm text-white">{favorite ? '★' : '☆'}</button>
          <button type="button" aria-label={name} aria-pressed={active} disabled={disabled} onClick={() => choose(choice)} className="w-full text-left disabled:opacity-60">
            <div className="aspect-[3/2] overflow-hidden" style={{ background: p.background }}>{own || p.preview ? <img src={resolveMediaUrl(own?.image_url || p.preview)} alt={name} width={480} height={320} loading="lazy" className={`h-full w-full ${p.collection === 'html' ? 'object-contain' : 'object-cover'}`} /> : <StyleSwatch preset={p} />}</div>
            <div className="space-y-1 p-3"><span className="block text-sm font-medium">{active && '✓ '}{name}</span><span className="block text-[10px]" style={{ color: 'var(--accent)' }}>{styleCategories[own?.category || p.category || 'drawing']}</span><span className="block text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>{own?.description || (own ? own.style_instruction.slice(0, 130) : p.description || 'Nét tối giản, biểu cảm rõ và đồ vật minh họa.')}</span>{own?.default_character_id && <span className="block text-[10px]" style={{ color: 'var(--muted)' }}>Có nhân vật mặc định</span>}</div>
          </button>
          {p.collection === 'html' && <div className="space-y-2 px-3 pb-3"><p className="text-[10px]" style={{ color: 'var(--muted)' }}>{p.preview_override ? 'Ảnh minh họa đã lưu' : 'Hình minh họa tạm · có thể thay ảnh'}</p><Button type="button" size="sm" variant="outline" disabled={disabled} aria-label={`Ảnh và prompt ${name}`} onClick={() => { setPreviewId(p.id); setPending(null); onBusy(true) }}>Ảnh & prompt mẫu</Button></div>}
        </article>
      })}</div>
      {!items.length && <p className="rounded-lg border border-dashed p-5 text-center text-sm" style={{ borderColor: 'var(--border)', color: 'var(--muted)' }}>{tab === 'mine' && !mine.length ? 'Chưa có mẫu riêng. Bấm Thêm mẫu để tải ảnh và duyệt phong cách.' : 'Không có phong cách phù hợp bộ lọc.'}</p>}
      {tab === 'mine' && styles.some(s => s.status === 'DRAFT') && <div className="flex flex-wrap items-center gap-2"><span className="text-xs">Bản nháp chưa duyệt:</span>{styles.filter(s => s.status === 'DRAFT').map(s => <Button key={s.id} type="button" variant="outline" size="sm" disabled={disabled} onClick={() => open(s)}>Tiếp tục · {s.name}</Button>)}</div>}
      <p className="text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>Đang dùng: <strong>{selected?.name || presets.find(p => p.id === value)?.name}</strong>. Ảnh trên thẻ minh họa phong cách; cảnh thực tế theo kịch bản và ảnh tham chiếu.</p>
    </>}
    {pending && <div role="dialog" aria-label="Đổi phong cách và nhân vật" className="space-y-3 rounded-lg border p-4" style={{ borderColor: 'var(--accent)' }}><p className="text-sm">Đổi sang <strong>{pending.profile?.name || pending.preset.name}</strong>. Bạn muốn dùng nhân vật nào?</p><div className="flex flex-wrap gap-2"><Button type="button" variant="outline" disabled={disabled} onClick={() => { onSelect(pending, 'keep'); setPending(null) }}>Giữ nhân vật hiện tại</Button><Button type="button" disabled={disabled} onClick={() => { onSelect(pending, 'new'); setPending(null) }}>{pending.profile?.default_character_id ? 'Dùng nhân vật mặc định của mẫu' : 'Tạo nhân vật theo mẫu mới'}</Button><Button type="button" variant="outline" disabled={disabled} onClick={() => setPending(null)}>Hủy</Button></div><p className="text-xs" style={{ color: 'var(--muted)' }}>Giữ nhân vật có thể giữ cả trang phục/nét vẽ cũ. Lời đọc và nội dung cảnh vẫn được giữ.</p></div>}
    {previewPreset && <StoryPresetPreviewEditor projectId={projectId} preset={previewPreset} disabled={disabled} onClose={close}
      onSaved={data => setIllustrations(current => ({ ...current, [previewPreset.id]: data }))}
      onReference={sourceImage => { setPreviewId(''); setEditor({ initial: null, sourceImage }) }} />}
    {editor && <StoryStyleEditor key={editor.initial?.id || 'new'} projectId={projectId} initial={editor.initial} sourceImage={editor.sourceImage} characters={characters} onDraft={putStyle} onSaved={style => { putStyle(style); onStyleSaved(); close(); setTab('mine'); const choice = profileChoice(style, presets); if (choice) choose(choice) }} onClose={close} onCharacter={onCharacter} />}
    {error && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>}
  </section>
}
