import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { fetchAPI } from '../api/client'
import { useTranslation } from '../i18n/useTranslation'
import type { FlowModelsConfig } from '../types'

export default function FlowModelSummary() {
  const { t } = useTranslation()
  const [models, setModels] = useState<FlowModelsConfig | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    fetchAPI<FlowModelsConfig>('/api/models').then(setModels).catch(() => setError(true))
  }, [])

  const imageAlias = models?.default_image_model ?? '—'
  const imageWire = models?.image_models?.[imageAlias] ?? imageAlias
  const videoModel = models?.omni_flash_models?.frame_to_video?.['8'] ?? '—'

  return (
    <div className="rounded-md border px-3 py-2.5 text-[11px]" style={{ borderColor: 'var(--border)', background: 'color-mix(in srgb, var(--surface) 72%, transparent)' }}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold tracking-wide">{t('modelSummary.title')}</span>
        <span className="rounded px-1.5 py-0.5 text-[9px] tracking-widest" style={{ background: 'var(--accent)', color: 'var(--bg)' }}>OMNI FLASH</span>
        <Link to="/settings" className="ml-auto text-[10px]" style={{ color: 'var(--accent)' }}>{t('modelSummary.settings')}</Link>
        <Link to="/models?check=1" className="text-[10px]" style={{ color: 'var(--accent)' }}>{t('modelCatalog.checkButton')}</Link>
      </div>
      {error ? (
        <div className="pt-1" style={{ color: 'var(--red)' }}>{t('modelSummary.error')}</div>
      ) : !models ? (
        <div className="pt-1" style={{ color: 'var(--muted)' }}>{t('modelSummary.loading')}</div>
      ) : (
        <div className="grid gap-x-4 gap-y-1 pt-2 sm:grid-cols-2" style={{ color: 'var(--muted)' }}>
          <div><span>{t('modelSummary.image')}: </span><code style={{ color: 'var(--text)' }}>{imageAlias}</code> <span>→</span> <code style={{ color: 'var(--text)' }}>{imageWire}</code></div>
          <div><span>{t('modelSummary.video')}: </span><code style={{ color: 'var(--text)' }}>frame-to-video · 8s · {videoModel}</code></div>
        </div>
      )}
    </div>
  )
}
