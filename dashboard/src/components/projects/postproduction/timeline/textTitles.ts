import { DEFAULT_CAPTION_STYLE } from '../editingTypes.ts'
import type { Caption } from './types.ts'

export const DEFAULT_TITLE_STYLE: Caption = {
  ...DEFAULT_CAPTION_STYLE, font_id: 'sans-bold', size: 7, background_opacity: 0,
  position: 'middle', x: .5, y: .5,
}

export const TEXT_TEMPLATES = [
  { id: 'film', name: 'Tên phim', sample: 'MỘT NGÀY BÌNH YÊN', note: 'Tiêu đề lớn ở giữa', style: { font_id: 'serif', size: 9, color: '#fff3d6', y: .45 } },
  { id: 'author', name: 'Tác giả', sample: 'MỘT BỘ PHIM CỦA\nNguyễn Văn Phúc', note: 'Tên người thực hiện', style: { font_id: 'serif', size: 5, y: .65 } },
  { id: 'intro', name: 'Giới thiệu', sample: 'NGUYỄN VĂN PHÚC\nNgười dẫn chuyện', note: 'Tên và vai trò nhân vật', style: { size: 4.5, align: 'left', y: .78, background_opacity: .75, background_color: '#132538' } },
  { id: 'advert', name: 'Quảng cáo', sample: 'ƯU ĐÃI HÔM NAY\nGiảm đến 30%', note: 'Thông điệp nổi bật', style: { size: 6, color: '#fff8d0', background_color: '#9f2936', background_opacity: .95 } },
] satisfies { id: string; name: string; sample: string; note: string; style: Partial<Caption> }[]

export function titleTemplate(id: string) {
  const template = TEXT_TEMPLATES.find(t => t.id === id)
  return { text: template?.sample ?? 'Nhập chữ của bạn', caption: { ...DEFAULT_TITLE_STYLE, ...template?.style } }
}

export interface TextBounds { x: number; y: number; width: number; height: number }
export function moveText(bounds: TextBounds, dx: number, dy: number) {
  return {
    x: Math.max(bounds.width / 2, Math.min(1 - bounds.width / 2, bounds.x + bounds.width / 2 + dx)),
    y: Math.max(bounds.height / 2, Math.min(1 - bounds.height / 2, bounds.y + bounds.height / 2 + dy)),
  }
}
export function resizeText(size: number, width: number, height: number, dx: number, dy: number, maximum: number) {
  // Resize about the centre. Project pointer movement onto the corner diagonal.
  const diagonal = Math.max(1, width * width + height * height)
  const scale = 1 + 2 * (dx * width + dy * height) / diagonal
  return Math.round(Math.max(1, Math.min(maximum, size * scale)) * 10) / 10
}
