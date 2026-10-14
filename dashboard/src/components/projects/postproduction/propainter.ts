export interface MaskBox { x: number; y: number; width: number; height: number }
export interface TrialSource { width: number; height: number; duration: number; fps_number: number; fingerprint: string }
export interface TrialDraft { start: number; duration: number; dilation: number; maxSide: 192 | 320; box: MaskBox | null }
export interface RestorationResult {
  trial_id: string; url: string; before_url?: string; start: number; created_at: number
  width: number; height: number; duration: number; inference_seconds: number; device: string
  peak_mps_driver_bytes: number; peak_rss_bytes: number; full_resolution?: boolean
  targets?: { clip_id: string; asset_id: string; source_in: number; duration: number; speed: number }[]
}

export function trialLimit(source: TrialSource) {
  return Math.max(0, Math.floor(Math.min(3, 60 / source.fps_number, source.duration) * 1000) / 1000)
}
export function maskFromDrag(a: { x: number; y: number }, b: { x: number; y: number }): MaskBox {
  const clamp = (n: number) => Math.max(0, Math.min(1, n))
  const x = clamp(a.x), y = clamp(a.y), right = clamp(b.x), bottom = clamp(b.y)
  return { x: Math.min(x, right), y: Math.min(y, bottom), width: Math.abs(right - x), height: Math.abs(bottom - y) }
}
export function maskProblem(box: MaskBox | null) {
  if (!box) return 'Kéo chuột trên ảnh để khoanh vùng muốn xóa.'
  if (![box.x, box.y, box.width, box.height].every(Number.isFinite) || box.x < 0 || box.y < 0 || box.width < .01 || box.height < .01 || box.x + box.width > 1.000001 || box.y + box.height > 1.000001) return 'Vùng quá nhỏ hoặc nằm ngoài ảnh. Hãy khoanh lại.'
  return box.width * box.height > .35 ? 'Vùng đang lớn hơn 35% khung hình. Khoanh sát vật thể nhỏ hơn để xử lý.' : ''
}
export function trialDraft(source: TrialSource, saved: unknown): TrialDraft {
  const value = saved && typeof saved === 'object' ? saved as Partial<TrialDraft> : {}
  const duration = Math.max(1, Math.min(trialLimit(source), Number.isFinite(value.duration) ? value.duration! : 1))
  return {
    start: Math.max(0, Math.min(source.duration - duration, Number.isFinite(value.start) ? value.start! : 0)), duration,
    dilation: Math.max(1, Math.min(8, Math.round(Number.isFinite(value.dilation) ? value.dilation! : 4))),
    maxSide: value.maxSide === 320 ? 320 : 192,
    box: value.box && !maskProblem(value.box) ? value.box : null,
  }
}
