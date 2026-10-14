import type { Asset, Document, TimelineState } from './types.ts'
import type { RestorationResult } from '../propainter.ts'
import { validate } from './operations.ts'

interface Editor {
  refresh: () => Promise<TimelineState | undefined>
  getDocument: () => Document | null
  apply: (document: Document) => void
  save: () => Promise<number>
}

/** Replace the existing linked AV pair, never append or alter its edit decisions. */
export function replaceRestorations(document: Document, assets: Asset[], results: RestorationResult[], restore = false): Document {
  const changes = new Map<string, { asset_id: string; source_in: number }>()
  for (const result of results) {
    if (!result.full_resolution || !result.targets?.length) throw new Error('Đây là đoạn thử cũ. Xử lý trọn cảnh trước khi thay trên timeline.')
    const repaired = assets.find(a => a.ref === `propainter:${result.trial_id}` && !a.missing)
    if (!repaired) throw new Error('Không đọc được cảnh AI đã lưu. Nạp lại thư viện rồi thử lại.')
    for (const target of result.targets) {
      const clip = document.clips.find(c => c.id === target.clip_id)
      if (!clip) continue
      if (document.tracks.find(t => t.id === clip.track_id)?.kind !== 'video') throw new Error('Cảnh đích không còn trên lớp hình ảnh.')
      if (Math.abs(clip.duration - target.duration) > .00001 || Math.abs((clip.speed ?? 1) - target.speed) > .00001) throw new Error('Cảnh đã được cắt hoặc đổi tốc độ. Xử lý lại cảnh hiện tại để giữ đồng bộ.')
      const original = clip.asset_id === target.asset_id && Math.abs(clip.source_in-target.source_in) < .00001
      const applied = clip.asset_id === repaired.id && clip.source_in === 0
      if (!original && !applied) throw new Error('Tư liệu của cảnh đã thay đổi. Xử lý lại trước khi áp dụng.')
      const assetId = restore ? target.asset_id : repaired.id, sourceIn = restore ? target.source_in : 0
      const asset = assets.find(a => a.id === assetId && !a.missing)
      if (!asset) throw new Error('Không còn tư liệu gốc để khôi phục. Nạp lại thư viện.')
      for (const member of document.clips.filter(c => c.id === clip.id || clip.group && c.group === clip.group)) {
        if (document.tracks.find(t => t.id === member.track_id)?.locked) throw new Error('Mở khóa lớp hình và tiếng liên kết trước khi áp dụng.')
        if (document.tracks.find(t => t.id === member.track_id)?.kind === 'audio' && !asset.has_audio) throw new Error('Kết quả thiếu tiếng gốc; chưa áp dụng để tránh mất âm thanh.')
        const next = { asset_id: assetId, source_in: sourceIn }, previous = changes.get(member.id)
        if (previous && (previous.asset_id !== next.asset_id || previous.source_in !== next.source_in)) throw new Error('Có hai kết quả cho cùng cảnh. Chọn một kết quả để áp dụng.')
        changes.set(member.id, next)
      }
    }
  }
  if (!changes.size) throw new Error('Cảnh đã bị xóa khỏi timeline. Chọn và xử lý lại cảnh cần dùng.')
  const clips = document.clips.map(c => {
    const next = changes.get(c.id)
    return next && (c.asset_id !== next.asset_id || c.source_in !== next.source_in) ? { ...c, ...next } : c
  })
  return clips.every((c, i) => c === document.clips[i]) ? document : validate({ ...document, clips }, assets)
}

/** Already applied results remain idempotent when retrying a failed save. */
export async function applyRestorations(editor: Editor, results: RestorationResult[], restore: boolean,
  onApply: (at: number, clipIds: string[]) => void) {
  const state = await editor.refresh(), current = editor.getDocument()
  if (!current || !state) throw new Error('Chưa nạp được bản dựng. Đóng rồi mở lại công cụ.')
  const next = replaceRestorations(current, state.assets, results, restore)
  if (next !== current) {
    const ids = results.flatMap(r => r.targets?.map(t => t.clip_id) ?? []).filter(id => next.clips.some(c => c.id === id))
    editor.apply(next)
    onApply(Math.min(...next.clips.filter(c => ids.includes(c.id)).map(c => c.start)), ids)
  }
  await editor.save()
}
