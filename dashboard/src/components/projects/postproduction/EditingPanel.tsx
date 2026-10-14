import { useEffect, useEffectEvent, useRef, useState } from 'react'
import { Download, Image, Loader2, Redo2, RotateCcw, Save, Undo2 } from 'lucide-react'
import { API_BASE_URL, resolveMediaUrl } from '../../../api/client'
import type { AudioMixController } from '../../../api/useAudioMix'
import { Button } from '../../ui/button'
import type { CaptionStyle, EditingResult, EditingSettings, FrameSettings } from './editingTypes'
import { DEFAULT_CAPTION_STYLE, dragFrame, outputSize } from './editingTypes'
import type { useEditingSettings } from './useEditingSettings'
import { AudioMixProgress } from './AudioMixProgress'
import { ColorRegionPanel } from './ColorRegionPanel'
import { EditingClipPreview } from './EditingClipPreview'
import { ExportVersionsPanel } from './ExportVersionsPanel'
import { ProPainterTrialPanel } from './ProPainterTrialPanel'

type Editor = ReturnType<typeof useEditingSettings>
interface Props { videoId: string; editor: Editor; mix: AudioMixController; previewTime: number; previewText: string; onPreviewTextChange: (text: string) => void; showSample: boolean; onLivePreview: () => void; selectedRegion: string | null; onSelectRegion: (id: string | null) => void }
const modes = [
  ['original', 'Giữ âm gốc', 'Không thêm giọng đọc hoặc phụ đề'],
  ['voice_subtitles', 'Giọng đọc + phụ đề', 'Có lời đọc và chữ trên màn hình'],
  ['voice', 'Chỉ giọng đọc', 'Có lời đọc, không thêm chữ'],
  ['subtitles', 'Chỉ phụ đề', 'Thêm chữ, không cần tạo giọng'],
] as const
const captionPresets: Record<string, Partial<CaptionStyle>> = {
  simple: { color: '#ffffff', background_color: '#000000', background_opacity: .7, outline: 1, position: 'bottom', align: 'center', size: 3.5 },
  bright: { color: '#ffe88b', background_opacity: 0, outline: 2, outline_color: '#000000', position: 'bottom', align: 'center', size: 4 },
  title: { color: '#ffffff', background_color: '#15385b', background_opacity: .85, outline: 0, position: 'middle', align: 'center', size: 5 },
}

function Slider({ label, value, min = 0, max = 1, step = .01, onChange, display }: {
  label: string; value: number; min?: number; max?: number; step?: number; onChange: (value: number) => void; display?: string
}) {
  return <label className="pp-edit-slider"><span>{label}<output>{display ?? `${Math.round(value * 100)}%`}</output></span>
    <input type="range" min={min} max={max} step={step} value={value} onChange={e => onChange(Number(e.target.value))} /></label>
}

export function EditingPanel({ videoId, editor, mix, previewTime, previewText: text, onPreviewTextChange, showSample, onLivePreview, selectedRegion, onSelectRegion }: Props) {
  const { settings, state } = editor
  const [previewUrl, setPreviewUrl] = useState('')
  const [previewSnapshot, setPreviewSnapshot] = useState('')
  const [previewBusy, setPreviewBusy] = useState(false)
  const [error, setError] = useState('')
  const [templateNotice, setTemplateNotice] = useState('')
  const [extraPresets, setExtraPresets] = useState<FrameSettings['preset'][]>([])
  const previewController = useRef<AbortController | null>(null)
  const dragging = useRef<{ x: number; y: number; settings: EditingSettings; width: number; height: number; latest: EditingSettings } | null>(null)
  useEffect(() => () => previewController.current?.abort(), [])
  useEffect(() => () => { if (previewUrl) URL.revokeObjectURL(previewUrl) }, [previewUrl])
  const refresh = useEffectEvent(() => editor.refreshSources().catch(cause => setError(String(cause))))
  useEffect(() => { if (['COMPLETED', 'CANCELLED', 'FAILED'].includes(mix.job?.status ?? '') && mix.job?.kind === 'edit') void refresh() }, [mix.job?.job_id, mix.job?.status, mix.job?.kind])
  if (!settings || !state) return <div className="pp-edit-card">{editor.error ? <p role="alert">{editor.error}</p> : <p><Loader2 size={16} /> Đang nạp thiết lập…</p>}<Button onClick={editor.reload} variant="outline">Nạp lại</Button></div>

  const frame = settings.frame, captions = settings.captions
  const size = outputSize(frame, state.source)
  const setFrame = (patch: Partial<FrameSettings>) => { onLivePreview(); editor.apply({ ...settings, frame: { ...frame, ...patch } }) }
  const setCaption = (patch: Partial<CaptionStyle>) => { onLivePreview(); editor.apply({ ...settings, captions: { ...captions, ...patch } }) }
  const hasText = settings.voice_source === 'speech' ? state.speech_cues > 0 : state.scene_texts > 0
  const completed = mix.job?.kind === 'edit' && mix.job.status === 'COMPLETED' ? mix.job.result as EditingResult : null
  const output = completed && state.exports?.some(e => e.url === completed.url) ? completed : state.last_export
  const changedPreview = previewSnapshot !== JSON.stringify([settings, text, showSample])
  const needsText = settings.mode !== 'original'
  const exportedCurrent = !editor.dirty && (output?.settings ? JSON.stringify(output.settings) === JSON.stringify(settings) : output?.revision === state.revision)
  const exportPresets = [frame.preset, ...extraPresets.filter(preset => preset !== frame.preset)]

  async function preview(draft = settings!) {
    previewController.current?.abort()
    const controller = new AbortController()
    previewController.current = controller
    setPreviewBusy(true); setError('')
    try {
      const response = await fetch(`${API_BASE_URL}/api/videos/${videoId}/editing/preview`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal,
        body: JSON.stringify({ settings: { ...draft, mode: showSample ? 'subtitles' : 'original' }, time: previewTime, text }),
      })
      if (!response.ok) throw new Error(`API ${response.status}: ${await response.text()}`)
      const blob = await response.blob()
      if (!controller.signal.aborted) { setPreviewUrl(URL.createObjectURL(blob)); setPreviewSnapshot(JSON.stringify([draft, text, showSample])) }
    } catch (cause) { if (!controller.signal.aborted) setError(String(cause)) }
    finally { if (!controller.signal.aborted) setPreviewBusy(false) }
  }

  async function exportVideo() {
    setError('')
    try { const revision = await editor.save(); await mix.start('edit', { revision, presets: exportPresets }) }
    catch (cause) { setError(String(cause)) }
  }

  return <div className="pp-edit-stack">
    <header className="pp-edit-card pp-edit-toolbar">
      <div><h3>Khung hình & chữ</h3><p>{editor.saving ? 'Đang lưu…' : editor.dirty ? 'Có thay đổi chưa lưu' : 'Đã lưu thiết lập cho video này'} · Bản nguồn được giữ riêng</p></div>
      <div className="pp-edit-actions">
        <Button variant="outline" disabled={!editor.canUndo} onClick={editor.undo} aria-label="Hoàn tác"><Undo2 size={15} /></Button>
        <Button variant="outline" disabled={!editor.canRedo} onClick={editor.redo} aria-label="Làm lại"><Redo2 size={15} /></Button>
        <Button variant="outline" disabled={editor.saving || !editor.dirty} onClick={() => void editor.save().catch(() => {})}><Save size={15} />Lưu</Button>
      </div>
    </header>
    {editor.error && <div className="pp-edit-alert" role="alert">{editor.error}<Button disabled={editor.saving} variant="outline" onClick={editor.reload}>Nạp lại thiết lập đã lưu</Button></div>}

    <section className="pp-edit-card">
      <h3>1. Khung hình đầu ra</h3><p>Đổi tỷ lệ, nền, zoom hoặc vị trí để xem ngay bên phải. Kéo trực tiếp trên khung xem trước để đặt lại hình.</p>
      <div className="pp-edit-presets" role="group" aria-label="Tỷ lệ khung hình">
        {(['source', '9:16', '16:9', '1:1', '4:5', 'custom'] as const).map(preset => <button key={preset} aria-pressed={frame.preset === preset} onClick={() => setFrame({ preset })}>
          {preset === 'source' ? 'Khung gốc' : preset === 'custom' ? 'Tùy chỉnh' : preset}<small>{({ source: 'Giữ kích thước', '9:16': 'Điện thoại', '16:9': 'YouTube', '1:1': 'Vuông', '4:5': 'Bài đăng', custom: 'Nhập kích thước' })[preset]}</small></button>)}
      </div>
      {state.source && <p className="pp-edit-meta">Nguồn: {state.source.width} × {state.source.height} · {state.source.duration.toFixed(1)} giây</p>}
      {size && <p className="pp-edit-meta"><strong>Bản xuất: {size[0]} × {size[1]} pixel</strong></p>}
      <div className="pp-edit-grid">
        {frame.preset === 'custom' && <><label>Rộng (pixel)<input type="number" min={240} max={3840} step={2} value={frame.width} onChange={e => setFrame({ width: Number(e.target.value) })} /></label>
          <label>Cao (pixel)<input type="number" min={240} max={3840} step={2} value={frame.height} onChange={e => setFrame({ height: Number(e.target.value) })} /></label></>}
        <label>Cách đặt hình<select value={frame.fit} onChange={e => setFrame({ fit: e.target.value as FrameSettings['fit'] })}><option value="contain">Giữ toàn hình</option><option value="cover">Lấp đầy bằng crop</option></select></label>
        <label>Nền khoảng trống<select value={frame.background} onChange={e => setFrame({ background: e.target.value as FrameSettings['background'] })}><option value="color">Màu nền</option><option value="blur">Nền video làm mờ</option></select></label>
        {frame.background === 'color' && <label>Màu nền<input type="color" value={frame.color} onInput={e => setFrame({ color: e.currentTarget.value })} onChange={() => { /* Input previews each color-picker movement. */ }} /></label>}
        <Slider label="Phóng hình" value={frame.zoom} min={1} max={3} step={.05} display={`${frame.zoom.toFixed(2)}×`} onChange={zoom => setFrame({ zoom })} />
        <Slider label="Vị trí ngang" value={frame.x} onChange={x => setFrame({ x })} />
        <Slider label="Vị trí dọc" value={frame.y} onChange={y => setFrame({ y })} />
      </div>
      <Button variant="outline" onClick={() => setFrame({ zoom: 1, x: .5, y: .5 })}><RotateCcw size={14} />Căn giữa / bỏ zoom</Button>
    </section>

    <section className="pp-edit-card">
      <h3>2. Lời đọc và phụ đề</h3>
      <div className="pp-edit-modes">{modes.map(([mode, title, help]) => <button key={mode} aria-pressed={settings.mode === mode} onClick={() => editor.apply({ ...settings, mode })}><strong>{title}</strong><small>{help}</small></button>)}</div>
      {needsText && <label>Nguồn lời đọc / phụ đề<select value={settings.voice_source} onChange={e => editor.apply({ ...settings, voice_source: e.target.value as EditingSettings['voice_source'] })}>
        <option value="speech">Kịch bản Lời thoại / SRT đã lưu ({state.speech_cues} câu)</option><option value="scenes">Lời thuyết minh theo cảnh ({state.scene_texts} cảnh có lời)</option></select></label>}
      {needsText && <p>Chọn giọng và tạo âm thanh ở mục <strong>Giọng đọc</strong> trước khi xuất có lời đọc. Chế độ chỉ phụ đề dùng văn bản đã lưu, không cần tạo âm thanh.</p>}
      {needsText && !hasText && <p className="pp-edit-warning">Nguồn đang chọn chưa có lời. Lưu kịch bản ở mục Giọng đọc, rồi ấn “Cập nhật nguồn lời”.</p>}
      <div className="pp-edit-grid">
        <Slider label="Âm lượng gốc" value={settings.source_volume} max={2} onChange={source_volume => editor.apply({ ...settings, source_volume })} />
        {(settings.mode === 'voice' || settings.mode === 'voice_subtitles') && <Slider label="Âm lượng giọng đọc" value={settings.narration_volume} max={2} onChange={narration_volume => editor.apply({ ...settings, narration_volume })} />}
      </div>
      <label className="pp-edit-checkbox"><input type="checkbox" checked={settings.use_music} onChange={e => editor.apply({ ...settings, use_music: e.target.checked })} />Thêm bản nhạc đã ghép ở mục Nhạc nền</label>
      <p>{state.music ? `Nhạc đã chọn: ${state.music.filename} · ${Math.round(state.music.volume * 100)}%` : 'Chưa có bản nhạc đã ghép. Bật tùy chọn này hiện chưa thêm nhạc.'}</p>
      <Button variant="outline" disabled={mix.busy} onClick={() => void editor.refreshSources().catch(cause => setError(String(cause)))}>Cập nhật nguồn lời / nhạc</Button>
    </section>

    <section className="pp-edit-card">
      <h3>3. Mẫu chữ</h3><p>Font, màu và bố cục cập nhật trên chữ mẫu bên phải. Bản xuất có chữ khi chọn chế độ có phụ đề.</p>
      <label>Chữ mẫu để xem bố cục<textarea value={text} maxLength={300} rows={2} onChange={e => { onLivePreview(); onPreviewTextChange(e.target.value) }} /></label>
      <div className="pp-edit-actions">{Object.entries(captionPresets).map(([id, style]) => <Button key={id} variant="outline" onClick={() => setCaption(style)}>{({ simple: 'Dễ đọc', bright: 'Chữ vàng', title: 'Tiêu đề' })[id]}</Button>)}</div>
      <div className="pp-edit-grid">
        <label>Font<select value={captions.font_id} onChange={e => setCaption({ font_id: e.target.value })}>{state.fonts.map(font => <option key={font.id} value={font.id}>{font.name}</option>)}</select></label>
        <Slider label="Cỡ chữ theo chiều cao" value={captions.size} min={1} max={10} step={.1} display={`${captions.size.toFixed(1)}%`} onChange={size => setCaption({ size })} />
        <label>Căn chữ<select value={captions.align} onChange={e => setCaption({ align: e.target.value as CaptionStyle['align'] })}><option value="left">Trái</option><option value="center">Giữa</option><option value="right">Phải</option></select></label>
        <label>Vị trí<select value={captions.position} onChange={e => setCaption({ position: e.target.value as CaptionStyle['position'] })}><option value="top">Trên</option><option value="middle">Giữa màn hình</option><option value="bottom">Dưới</option></select></label>
        <label>Màu chữ<input type="color" value={captions.color} onInput={e => setCaption({ color: e.currentTarget.value })} onChange={() => { /* Preview while picking. */ }} /></label>
        <label>Màu viền<input type="color" value={captions.outline_color} onInput={e => setCaption({ outline_color: e.currentTarget.value })} onChange={() => { /* Preview while picking. */ }} /></label>
        <Slider label="Độ dày viền" value={captions.outline} max={8} step={1} display={`${captions.outline}px`} onChange={outline => setCaption({ outline })} />
        <label>Màu nền chữ<input type="color" value={captions.background_color} onInput={e => setCaption({ background_color: e.currentTarget.value })} onChange={() => { /* Preview while picking. */ }} /></label>
        <Slider label="Độ đậm nền chữ" value={captions.background_opacity} onChange={background_opacity => setCaption({ background_opacity })} />
        <Slider label="Lề trên / dưới" value={captions.margin} min={2} max={20} step={.5} display={`${captions.margin}%`} onChange={margin => setCaption({ margin })} />
      </div>
      <div className="pp-edit-actions">
        <Button variant="outline" title="Khôi phục font, màu, cỡ chữ, vị trí và lề về mặc định" onClick={() => {
          setCaption({ ...DEFAULT_CAPTION_STYLE })
          setTemplateNotice('Đã khôi phục mẫu chữ mặc định. Bạn có thể Hoàn tác để quay lại mẫu trước.')
        }}><RotateCcw size={14} />Khôi phục mẫu chữ mặc định</Button>
        <Button variant="outline" onClick={() => { try { localStorage.setItem('flowkit:caption-template', JSON.stringify(captions)); setTemplateNotice('Đã lưu mẫu chữ trên trình duyệt này.') } catch { setTemplateNotice('Không lưu được mẫu trên trình duyệt.') } }}>Lưu mẫu chữ của tôi</Button>
        <Button variant="outline" onClick={() => { try { const value = localStorage.getItem('flowkit:caption-template'); if (!value) { setTemplateNotice('Chưa có mẫu đã lưu.'); return } setCaption(JSON.parse(value) as CaptionStyle); setTemplateNotice('Đã áp dụng mẫu đã lưu.') } catch { setTemplateNotice('Không đọc được mẫu chữ.') } }}>Dùng mẫu đã lưu</Button>
      </div>{templateNotice && <p role="status">{templateNotice}</p>}
    </section>

    <ColorRegionPanel videoId={videoId} settings={settings} duration={state.source?.duration ?? 0} selectedRegion={selectedRegion} onSelectRegion={id => { onLivePreview(); onSelectRegion(id) }} onChange={draft => { onLivePreview(); editor.apply(draft) }} />
    <ProPainterTrialPanel videoId={videoId} blocked={mix.busy || !state.preview_available} />

    <section className="pp-edit-card">
      <h3>6. Kiểm tra & quản lý bản xuất</h3><p>Khung bên phải cập nhật trực tiếp. Bạn có thể kiểm tra thêm một ảnh bằng bộ dựng của bản xuất. Chữ mẫu chỉ để xem bố cục; lời thật lấy từ nguồn đã chọn.</p>
      <EditingClipPreview videoId={videoId} settings={settings} time={previewTime} blocked={mix.busy || !state.preview_available} />
      <div className="pp-edit-actions"><Button variant="outline" disabled={!state.preview_available || previewBusy} onClick={() => void preview()}>{previewBusy ? <Loader2 className="animate-spin" size={15} /> : <Image size={15} />}Dựng ảnh kiểm tra tại {previewTime.toFixed(1)}s</Button></div>
      {!state.preview_available && <p>Ghép video nguồn trước để có ảnh xem thử. Bạn vẫn có thể xuất từ các cảnh đã hoàn thành.</p>}
      {previewUrl && <div className="pp-edit-preview"><img src={previewUrl} alt="Bố cục khung hình và chữ mẫu" draggable={false}
        onPointerDown={e => { if (previewBusy) return; e.currentTarget.setPointerCapture(e.pointerId); const box = e.currentTarget.getBoundingClientRect(); dragging.current = { x: e.clientX, y: e.clientY, settings, width: box.width, height: box.height, latest: settings }; editor.apply(settings) }}
        onPointerMove={e => { const drag = dragging.current; if (!drag) return; drag.latest = { ...drag.settings, frame: dragFrame(drag.settings.frame, state.source, (e.clientX - drag.x) / drag.width, (e.clientY - drag.y) / drag.height) }; editor.apply(drag.latest, false) }}
        onPointerUp={() => { const drag = dragging.current; dragging.current = null; if (drag) void preview(drag.latest) }} onPointerCancel={() => { dragging.current = null }} />
        <p>{changedPreview ? 'Ảnh kiểm tra này dùng thiết lập trước. Khung bên phải đang hiện thay đổi mới.' : 'Kéo trên ảnh để đổi vị trí; thả chuột để cập nhật ảnh.'}</p></div>}
      {(error || editor.error) && <p className="pp-edit-warning" role="alert">{error || editor.error}</p>}
      <fieldset className="pp-export-presets"><legend>Xuất thêm tỷ lệ (chạy lần lượt)</legend>{(['9:16', '16:9', '1:1', '4:5'] as const).map(preset => <label key={preset}><input type="checkbox" checked={preset === frame.preset || extraPresets.includes(preset)} disabled={preset === frame.preset} onChange={e => setExtraPresets(e.target.checked ? [...extraPresets, preset] : extraPresets.filter(p => p !== preset))} />{preset}{preset === frame.preset ? ' · đang chỉnh' : ''}</label>)}<p>Khung đang chỉnh luôn được xuất. Mỗi bản lưu riêng và giữ cấu hình tại lúc bắt đầu.</p></fieldset>
      <div className="pp-edit-export"><div><strong>Xuất bản mới</strong><small>Lưu riêng, giữ nguyên video nguồn và các cảnh.</small></div>
        <Button disabled={mix.busy || editor.saving || Boolean(editor.error) || (needsText && !hasText)} onClick={() => void exportVideo()}>{mix.busy ? <Loader2 size={15} className="animate-spin" /> : <Download size={15} />}{exportPresets.length > 1 ? `Tạo ${exportPresets.length} bản xuất` : 'Tạo bản xuất'}</Button></div>
      <AudioMixProgress mix={mix} kind="edit" showResult={!!completed && !!state.exports?.some(e => e.url === completed.url)} />
      {output && <div className="pp-edit-result"><strong>{exportedCurrent ? 'Bản xuất theo thiết lập đang lưu' : 'Thiết lập của bản xuất đã lưu'}</strong><p>{output.width} × {output.height} · {output.duration?.toFixed(1)} giây</p>
        <Button asChild variant="outline"><a href={resolveMediaUrl(output.url)} target="_blank" rel="noreferrer">Xem video đã xuất</a></Button>
        <Button asChild variant="outline"><a href={resolveMediaUrl(`${output.url}?download=true`)} download>Tải MP4</a></Button></div>}
      <ExportVersionsPanel key={videoId} videoId={videoId} versions={state.exports ?? []} disabled={mix.busy || editor.saving} beforeDelete={editor.save} onRefresh={editor.refreshSources} onApply={draft => { onLivePreview(); editor.apply(draft) }} />
    </section>
  </div>
}
