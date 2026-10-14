import { useState } from 'react'
import { ImageOff, RefreshCw } from 'lucide-react'
import { resolveMediaUrl } from '../../api/client'
import type { Character } from '../../types'
import { Button } from '../ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/dialog'

export interface ReferenceImageFeedback {
  characterId: string | null
  message: string
  error: boolean
}

export function ReferenceImage({ character, className }: { character: Character; className: string }) {
  const [failedSource, setFailedSource] = useState<'remote' | 'all' | null>(null)
  const localUrl = `/api/characters/${character.id}/image`
  const originalUrl = character.reference_image_url
  const src = failedSource === 'remote' ? localUrl : originalUrl

  return (
    <>
      {src && failedSource !== 'all' ? (
        <img
          src={resolveMediaUrl(src)} alt={character.name} className={className}
          onError={() => setFailedSource(failedSource === null && originalUrl !== localUrl ? 'remote' : 'all')}
        />
      ) : (
        <div className={`flex flex-col items-center justify-center gap-2 p-3 text-center text-xs ${className}`} style={{ color: 'var(--muted)' }}>
          <ImageOff className="size-6" />
          <span>{character.media_id ? 'Ảnh chưa tải được. Bấm Nạp lại ảnh.' : 'Chưa có ảnh tham chiếu.'}</span>
        </div>
      )}
      {failedSource === 'remote' && <p className="px-2 py-1 text-[11px]" style={{ color: 'var(--muted)' }}>Đang hiển thị bản đã lưu. Bạn có thể nạp lại từ Flow.</p>}
    </>
  )
}

export default function CharacterReferenceImage({ character, onRefresh, refreshing, disabled, revision, feedback, imageClassName = 'h-48 w-full object-contain' }: {
  character: Character
  onRefresh: (character: Character) => void
  refreshing: boolean
  disabled: boolean
  revision: number
  feedback: ReferenceImageFeedback
  imageClassName?: string
}) {
  const [open, setOpen] = useState(false)
  const imageKey = `${character.reference_image_url ?? ''}:${revision}`
  const reloadButton = (
    <Button
      type="button" size="sm" variant="outline" className="w-full"
      disabled={disabled || !character.media_id} onClick={() => onRefresh(character)}
      aria-label={`Nạp lại ảnh ${character.name}`}
      title={character.media_id ? 'Lấy liên kết mới của ảnh đang có trên Google Flow' : 'Nhân vật chưa có ảnh trên Google Flow'}
    >
      <RefreshCw className={`size-3.5 ${refreshing ? 'animate-spin' : ''}`} />
      {refreshing ? 'Đang nạp ảnh…' : 'Nạp lại ảnh'}
    </Button>
  )

  return (
    <>
      <button type="button" className="block w-full cursor-zoom-in" style={{ background: 'var(--surface)' }} onClick={() => setOpen(true)} aria-label={`Xem ảnh tham chiếu ${character.name}`}>
        <ReferenceImage key={imageKey} character={character} className={imageClassName} />
      </button>
      <div className="px-2 pt-2">{reloadButton}</div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Ảnh tham chiếu · {character.name}</DialogTitle>
            <DialogDescription>Ảnh trên Flow vẫn có thể còn dù liên kết cũ đã hết hạn. Nạp lại để lấy liên kết mới qua extension đang kết nối.</DialogDescription>
          </DialogHeader>
          <ReferenceImage key={imageKey} character={character} className="max-h-[60vh] min-h-48 w-full object-contain" />
          {reloadButton}
          {feedback.message && (feedback.characterId === character.id || feedback.characterId === 'all') && (
            <p role={feedback.error ? 'alert' : 'status'} className="text-xs" style={{ color: feedback.error ? 'var(--red)' : 'var(--green)' }}>{feedback.message}</p>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
