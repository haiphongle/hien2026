import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { fetchAPI } from '../api/client'
import { useTranslation } from '../i18n/useTranslation'
import type { FlowModelsConfig } from '../types'
import { Button } from '../components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card'

const UPSTREAM_MODELS_URL = import.meta.env.VITE_MODEL_CATALOG_URL || 'https://raw.githubusercontent.com/phucnguyentvp1/frameweaver/main/agent/models.json'
const GOOGLE_MODELS_URL = 'https://support.google.com/flow/answer/16352836?hl=vi'

type Difference = { section: string; name: string; current: string; upstream: string }

function differences(local: FlowModelsConfig, upstream: FlowModelsConfig): Difference[] {
  const rows: Difference[] = []
  for (const [name, wireId] of Object.entries(upstream.image_models ?? {})) {
    const current = local.image_models?.[name]
    if (current !== wireId) rows.push({ section: 'image', name, current: current ?? '—', upstream: wireId })
  }
  for (const [mode, durations] of Object.entries(upstream.omni_flash_models ?? {})) {
    for (const [duration, wireId] of Object.entries(durations)) {
      const current = local.omni_flash_models?.[mode]?.[duration]
      if (current !== wireId) rows.push({ section: 'video', name: `${mode} · ${duration}s`, current: current ?? '—', upstream: wireId })
    }
  }
  for (const wireId of upstream.batch_video_models?.accepted ?? []) {
    if (!(local.batch_video_models?.accepted ?? []).includes(wireId)) {
      rows.push({ section: 'veo', name: 'Veo', current: '—', upstream: wireId })
    }
  }
  return rows
}

function modelIds(config: FlowModelsConfig): Set<string> {
  return new Set([
    ...Object.values(config.image_models ?? {}),
    ...Object.values(config.omni_flash_models ?? {}).flatMap(durations => Object.values(durations)),
    ...(config.batch_video_models?.accepted ?? []),
  ])
}

export default function ModelCatalogPage() {
  const { lang, t } = useTranslation()
  const [searchParams] = useSearchParams()
  const [local, setLocal] = useState<FlowModelsConfig | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const [checking, setChecking] = useState(false)
  const [checkError, setCheckError] = useState<string | null>(null)
  const [checkedAt, setCheckedAt] = useState<string | null>(null)
  const [upstream, setUpstream] = useState<FlowModelsConfig | null>(null)

  const checkUpstream = useCallback(async () => {
    setChecking(true)
    setCheckError(null)
    try {
      const response = await fetch(`${UPSTREAM_MODELS_URL}?t=${Date.now()}`, { cache: 'no-store' })
      if (!response.ok) {
        if (response.status === 404) {
          throw new Error(lang === 'vi'
            ? 'Repo FrameWeaver chưa công khai hoặc chưa có danh mục model.'
            : 'The FrameWeaver repository is not public yet or its model catalog is missing.')
        }
        throw new Error(`HTTP ${response.status}`)
      }
      const data = await response.json() as FlowModelsConfig
      if (!data || typeof data.image_models !== 'object' || typeof data.omni_flash_models !== 'object') {
        throw new Error('Invalid model catalog')
      }
      setUpstream(data)
      setCheckedAt(new Date().toLocaleString())
    } catch (error) {
      setCheckError(error instanceof Error ? error.message : String(error))
    } finally {
      setChecking(false)
    }
  }, [lang])

  useEffect(() => {
    fetchAPI<FlowModelsConfig>('/api/models').then(setLocal).catch(error => setLocalError(String(error)))
  }, [])

  useEffect(() => {
    if (searchParams.get('check') === '1') void checkUpstream()
  }, [searchParams, checkUpstream])

  const vi = lang === 'vi'
  const changed = local && upstream ? differences(local, upstream) : []
  const installedIds = local ? modelIds(local) : new Set<string>()
  const newModels = changed.filter(row => !installedIds.has(row.upstream))
  const mappingChanges = changed.filter(row => installedIds.has(row.upstream))
  const imageOptions = [
    { alias: 'NANO_BANANA_2_LITE', name: 'Nano Banana 2 Lite', description: vi ? 'Nhanh, phù hợp tạo ảnh nháp và tiết kiệm.' : 'Fast, suitable for drafts.' },
    { alias: 'NANO_BANANA_2', name: 'Nano Banana 2', description: vi ? 'Cân bằng tốc độ và chất lượng; đang dùng mặc định.' : 'Balanced speed and quality; current default.' },
    { alias: 'NANO_BANANA_PRO', name: 'Nano Banana Pro', description: vi ? 'Hợp với cảnh nhiều chi tiết và yêu cầu kiểm soát cao.' : 'For detailed scenes and finer control.' },
  ]

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="m-0 text-lg font-semibold">{t('modelCatalog.title')}</h1>
        <p className="mt-1 text-xs" style={{ color: 'var(--muted)' }}>{t('modelCatalog.intro')}</p>
      </div>

      <Card className="py-4">
        <CardHeader>
          <CardTitle className="text-xs tracking-widest uppercase">{t('modelCatalog.checkTitle')}</CardTitle>
          <CardDescription className="text-[11px]">{t('modelCatalog.checkHelp')}</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm" disabled={checking} onClick={() => void checkUpstream()}>{checking ? t('modelCatalog.checking') : t('modelCatalog.checkButton')}</Button>
            <a href={UPSTREAM_MODELS_URL} target="_blank" rel="noreferrer" className="text-xs" style={{ color: 'var(--accent)' }}>{t('modelCatalog.sourceRepo')}</a>
            <a href={GOOGLE_MODELS_URL} target="_blank" rel="noreferrer" className="text-xs" style={{ color: 'var(--accent)' }}>{t('modelCatalog.sourceGoogle')}</a>
          </div>
          {checkError && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{t('modelCatalog.checkFailed')}: {checkError}</p>}
          {checkedAt && <p className="text-xs" style={{ color: 'var(--muted)' }}>{t('modelCatalog.checkedAt', { time: checkedAt })}</p>}
          {upstream && local && (
            <div className="flex flex-col gap-2">
              <p className="text-xs font-semibold" style={{ color: newModels.length ? 'var(--text)' : 'var(--green)' }}>
                {newModels.length ? t('modelCatalog.changes', { n: newModels.length }) : t('modelCatalog.noChanges')}
              </p>
              {newModels.map(row => (
                <div key={`${row.section}-${row.name}`} className="rounded-md border p-2.5 text-xs" style={{ borderColor: 'var(--border)' }}>
                  <div className="font-semibold">{row.name}</div>
                  <div className="break-all" style={{ color: 'var(--muted)' }}>{t('modelCatalog.installed')}: <code>{row.current}</code></div>
                  <div className="break-all">{t('modelCatalog.upstream')}: <code>{row.upstream}</code></div>
                </div>
              ))}
              {mappingChanges.length > 0 && (
                <details className="text-xs">
                  <summary className="cursor-pointer" style={{ color: 'var(--muted)' }}>{t('modelCatalog.mappingChanges', { n: mappingChanges.length })}</summary>
                  <div className="mt-2 flex flex-col gap-2">
                    {mappingChanges.map(row => (
                      <div key={`${row.section}-${row.name}`} className="rounded-md border p-2.5" style={{ borderColor: 'var(--border)' }}>
                        <div className="font-semibold">{row.name}</div>
                        <div className="break-all" style={{ color: 'var(--muted)' }}>{t('modelCatalog.installed')}: <code>{row.current}</code></div>
                        <div className="break-all">{t('modelCatalog.upstream')}: <code>{row.upstream}</code></div>
                      </div>
                    ))}
                  </div>
                </details>
              )}
              {newModels.length > 0 && <p className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('modelCatalog.reviewHint')}</p>}
            </div>
          )}
        </CardContent>
      </Card>

      {localError && <p role="alert" className="text-xs" style={{ color: 'var(--red)' }}>{localError}</p>}
      <Card className="py-4">
        <CardHeader>
          <CardTitle className="text-xs tracking-widest uppercase">{t('modelCatalog.imageTitle')}</CardTitle>
          <CardDescription className="text-[11px]">{t('modelCatalog.imageHelp')}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-3">
          {imageOptions.map(option => (
            <div key={option.alias} className="rounded-md border p-3" style={{ borderColor: option.alias === local?.default_image_model ? 'var(--accent)' : 'var(--border)' }}>
              <div className="flex flex-wrap items-center gap-2 text-xs font-semibold">
                {option.name}
                {option.alias === local?.default_image_model && <span className="text-[9px]" style={{ color: 'var(--accent)' }}>{t('modelCatalog.active')}</span>}
              </div>
              <p className="mt-1 text-[11px]" style={{ color: 'var(--muted)' }}>{option.description}</p>
              <code className="text-[10px]">{local?.image_models?.[option.alias] ?? '—'}</code>
            </div>
          ))}
        </CardContent>
      </Card>

      <Card className="py-4">
        <CardHeader>
          <CardTitle className="text-xs tracking-widest uppercase">{t('modelCatalog.videoTitle')}</CardTitle>
          <CardDescription className="text-[11px]">{t('modelCatalog.videoHelp')}</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 sm:grid-cols-2">
          <div className="rounded-md border p-3" style={{ borderColor: 'var(--accent)' }}>
            <div className="text-xs font-semibold">Gemini Omni Flash 1.1 · {t('modelCatalog.active')}</div>
            <p className="mt-1 text-[11px]" style={{ color: 'var(--muted)' }}>{t('modelCatalog.omniHelp')}</p>
            <code className="text-[10px]">{local?.omni_flash_models?.frame_to_video?.['8'] ?? '—'}</code>
          </div>
          <div className="rounded-md border p-3" style={{ borderColor: 'var(--border)' }}>
            <div className="text-xs font-semibold">Veo 3.1 · Lite / Fast / Quality</div>
            <p className="mt-1 text-[11px]" style={{ color: 'var(--muted)' }}>{t('modelCatalog.veoHelp')}</p>
            <div className="text-[10px]" style={{ color: 'var(--muted)' }}>{t('modelCatalog.veoStatus')}</div>
          </div>
        </CardContent>
      </Card>

      <Link to="/settings" className="self-start text-xs" style={{ color: 'var(--accent)' }}>{t('modelCatalog.settingsLink')}</Link>
    </div>
  )
}
