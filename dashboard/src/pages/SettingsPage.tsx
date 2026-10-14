import { useState, useEffect, useCallback } from 'react'
import { RefreshCw } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { fetchAPI, patchAPI } from '../api/client'
import { useTranslation } from '../i18n/useTranslation'
import type { FlowModelsConfig, RoleAssignment, ProvidersResponse, ProviderModelsResponse, ProvidersUpdateResponse } from '../types'
import { Card, CardHeader, CardTitle, CardDescription, CardContent, CardAction } from '../components/ui/card'
import { Button } from '../components/ui/button'
import CodexConnection from '../components/settings/CodexConnection'

const DEFAULT_OPTION = '__default__'
const CUSTOM_OPTION = '__custom__'

const CONTROL_CLASS = 'text-xs px-2 py-1.5 rounded outline-none w-full'
const CONTROL_STYLE = { background: 'var(--card)', color: 'var(--text)', border: '1px solid var(--border)' }

interface SaveState {
  ok: boolean
  text: string
}

// fetchAPI throws `API <status>: <body>` — pull the backend's `detail` back out so a 400 reads verbatim.
function apiDetail(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err)
  const body = raw.replace(/^API \d+: /, '')
  try {
    const parsed = JSON.parse(body) as { detail?: unknown }
    if (typeof parsed.detail === 'string') return parsed.detail
  } catch {
    // not JSON — fall through to the raw message
  }
  return raw
}

function cloneRoles(roles: Record<string, RoleAssignment>): Record<string, RoleAssignment> {
  return Object.fromEntries(Object.entries(roles).map(([id, r]) => [id, { ...r }]))
}

function sameAssignment(a: RoleAssignment, b: RoleAssignment): boolean {
  return a.provider === b.provider && a.model === b.model && a.effort === b.effort
}

function cloneFlowModels(models: FlowModelsConfig): FlowModelsConfig {
  return {
    ...models,
    image_models: { ...models.image_models },
    upscale_models: { ...models.upscale_models },
    omni_flash_models: Object.fromEntries(
      Object.entries(models.omni_flash_models ?? {}).map(([mode, durations]) => [mode, { ...durations }]),
    ),
    video_models: Object.fromEntries(
      Object.entries(models.video_models ?? {}).map(([tier, generations]) => [
        tier,
        Object.fromEntries(Object.entries(generations).map(([generation, aspects]) => [generation, { ...aspects }])),
      ]),
    ),
    batch_video_models: models.batch_video_models ? { ...models.batch_video_models, accepted: [...(models.batch_video_models.accepted ?? [])] } : undefined,
  }
}

const OMNI_DURATIONS = ['4', '6', '8', '10']
const OMNI_MODE_LABELS: Record<string, string> = {
  frame_to_video: 'Frame → video',
  start_end_frame_to_video: 'Start + end frame',
  reference_to_video: 'References → video',
}

export default function SettingsPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [data, setData] = useState<ProvidersResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [drafts, setDrafts] = useState<Record<string, RoleAssignment>>({})
  const [models, setModels] = useState<Record<string, ProviderModelsResponse>>({})
  const [modelsLoading, setModelsLoading] = useState<Record<string, boolean>>({})
  const [customModel, setCustomModel] = useState<Record<string, boolean>>({})
  const [saving, setSaving] = useState<Record<string, boolean>>({})
  const [saveState, setSaveState] = useState<Record<string, SaveState>>({})
  const [testing, setTesting] = useState(false)
  const [testError, setTestError] = useState<string | null>(null)
  const [roleTesting, setRoleTesting] = useState<string | null>(null)
  const [roleTests, setRoleTests] = useState<Record<string, SaveState>>({})
  const [codexAuthRefresh, setCodexAuthRefresh] = useState(0)
  const [flowModels, setFlowModels] = useState<FlowModelsConfig | null>(null)
  const [flowDraft, setFlowDraft] = useState<FlowModelsConfig | null>(null)
  const [flowModelsLoading, setFlowModelsLoading] = useState(true)
  const [flowModelsSaving, setFlowModelsSaving] = useState(false)
  const [flowModelsState, setFlowModelsState] = useState<SaveState | null>(null)

  const loadModels = useCallback(async (provider: string, refresh: boolean) => {
    setModelsLoading(prev => ({ ...prev, [provider]: true }))
    try {
      const res = await fetchAPI<ProviderModelsResponse>(
        `/api/providers/models?provider=${encodeURIComponent(provider)}&refresh=${refresh}`
      )
      setModels(prev => ({ ...prev, [provider]: res }))
    } catch {
      // an unreachable catalog is not fatal — render it as "no catalog available"
      setModels(prev => ({ ...prev, [provider]: { provider, models: [], authoritative: false } }))
    } finally {
      setModelsLoading(prev => ({ ...prev, [provider]: false }))
    }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const res = await fetchAPI<ProvidersResponse>('/api/providers?live=false')
      setData(res)
      setDrafts(cloneRoles(res.roles))
      setCustomModel({})
      setSaveState({})
      Array.from(new Set(Object.values(res.roles).map(r => r.provider))).forEach(p => { loadModels(p, false) })
    } catch (err) {
      setData(null)
      setLoadError(apiDetail(err))
    } finally {
      setLoading(false)
    }
  }, [loadModels])

  useEffect(() => { Promise.resolve().then(load) }, [load])

  const loadFlowModels = useCallback(async () => {
    setFlowModelsLoading(true)
    try {
      const res = await fetchAPI<FlowModelsConfig>('/api/models')
      setFlowModels(res)
      setFlowDraft(cloneFlowModels(res))
      setFlowModelsState(null)
    } catch (err) {
      setFlowModelsState({ ok: false, text: apiDetail(err) })
    } finally {
      setFlowModelsLoading(false)
    }
  }, [])

  useEffect(() => { Promise.resolve().then(loadFlowModels) }, [loadFlowModels])

  function updateDraft(roleId: string, patch: Partial<RoleAssignment>) {
    setRoleTests(prev => { const next = { ...prev }; delete next[roleId]; return next })
    setDrafts(prev => ({ ...prev, [roleId]: { ...prev[roleId], ...patch } }))
    setSaveState(prev => {
      if (!(roleId in prev)) return prev
      const next = { ...prev }
      delete next[roleId]
      return next
    })
  }

  function updateFlowImage(alias: string, value: string) {
    setFlowDraft(prev => prev ? { ...prev, image_models: { ...prev.image_models, [alias]: value } } : prev)
    setFlowModelsState(null)
  }

  function updateFlowOmni(mode: string, duration: string, value: string) {
    setFlowDraft(prev => prev ? {
      ...prev,
      omni_flash_models: {
        ...prev.omni_flash_models,
        [mode]: { ...(prev.omni_flash_models[mode] ?? {}), [duration]: value },
      },
    } : prev)
    setFlowModelsState(null)
  }

  function updateFlowLegacy(tier: string, generation: string, aspect: string, value: string) {
    setFlowDraft(prev => prev ? {
      ...prev,
      video_models: {
        ...prev.video_models,
        [tier]: {
          ...(prev.video_models[tier] ?? {}),
          [generation]: {
            ...(prev.video_models[tier]?.[generation] ?? {}),
            [aspect]: value,
          },
        },
      },
    } : prev)
    setFlowModelsState(null)
  }

  async function saveFlowModels() {
    if (!flowDraft) return
    setFlowModelsSaving(true)
    setFlowModelsState(null)
    try {
      const res = await patchAPI<{ models: FlowModelsConfig }>('/api/models', {
        default_image_model: flowDraft.default_image_model,
        image_models: flowDraft.image_models,
        omni_flash_models: flowDraft.omni_flash_models,
        video_models: flowDraft.video_models,
      })
      const next = cloneFlowModels(res.models)
      setFlowModels(next)
      setFlowDraft(next)
      setFlowModelsState({ ok: true, text: t('settings.flowModels.saved') })
    } catch (err) {
      setFlowModelsState({ ok: false, text: apiDetail(err) })
    } finally {
      setFlowModelsSaving(false)
    }
  }

  function changeProvider(roleId: string, provider: string) {
    // a model slug from one CLI means nothing to another, and effort ladders differ per provider
    const efforts = data?.providers[provider]?.efforts ?? []
    const current = drafts[roleId]
    const effort = current.effort !== null && efforts.includes(current.effort) ? current.effort : null
    updateDraft(roleId, { provider, model: null, effort })
    setCustomModel(prev => ({ ...prev, [roleId]: false }))
    if (!models[provider] && !modelsLoading[provider]) loadModels(provider, false)
  }

  function setModel(roleId: string, model: string | null) {
    // a provider whose slugs name their own effort rejects --model and --effort together
    const encodesEffort = data?.providers[drafts[roleId].provider]?.model_encodes_effort ?? false
    updateDraft(roleId, model !== null && encodesEffort ? { model, effort: null } : { model })
  }

  function selectModel(roleId: string, value: string) {
    if (value === CUSTOM_OPTION) {
      setCustomModel(prev => ({ ...prev, [roleId]: true }))
      setModel(roleId, null)
      return
    }
    setModel(roleId, value === DEFAULT_OPTION ? null : value)
  }

  async function save(roleId: string) {
    const draft = drafts[roleId]
    const model = draft.model !== null && draft.model.trim() !== '' ? draft.model.trim() : null
    const encodesEffort = data?.providers[draft.provider]?.model_encodes_effort ?? false
    const effort = model !== null && encodesEffort ? null : draft.effort
    setSaving(prev => ({ ...prev, [roleId]: true }))
    try {
      const res = await patchAPI<ProvidersUpdateResponse>('/api/providers', {
        roles: { [roleId]: { provider: draft.provider, model, effort } },
      })
      setData(prev => (prev ? { ...prev, active: res.active,
        roles: Object.fromEntries(Object.keys(prev.roles).map(id => [id, res.roles[id] ?? prev.roles[id]])) } : prev))
      setDrafts(prev => ({ ...prev, [roleId]: { ...(res.roles[roleId] ?? prev[roleId]) } }))
      setSaveState(prev => ({ ...prev, [roleId]: { ok: true, text: t('settings.saved') } }))
    } catch (err) {
      setSaveState(prev => ({ ...prev, [roleId]: { ok: false, text: apiDetail(err) } }))
    } finally {
      setSaving(prev => ({ ...prev, [roleId]: false }))
    }
  }

  async function runTest() {
    setTesting(true)
    setTestError(null)
    try {
      const res = await fetchAPI<ProvidersResponse>('/api/providers?live=true')
      setData(prev => (prev ? { ...prev, providers: res.providers } : res))
    } catch (err) {
      setTestError(apiDetail(err))
    } finally {
      setTesting(false)
    }
  }

  async function testRole(roleId: string) {
    setRoleTesting(roleId)
    setRoleTests(prev => { const next = { ...prev }; delete next[roleId]; return next })
    try {
      const result = await fetchAPI<{ seconds: number; message: string }>('/api/providers/test', {
        method: 'POST', body: JSON.stringify({ role: roleId, assignment: drafts[roleId] }),
      })
      setRoleTests(prev => ({ ...prev, [roleId]: { ok: true, text: `${result.message} (${result.seconds}s)` } }))
    } catch (error) {
      setRoleTests(prev => ({ ...prev, [roleId]: { ok: false, text: apiDetail(error) } }))
    } finally {
      setRoleTesting(null)
      if (drafts[roleId].provider === 'codex') setCodexAuthRefresh(value => value + 1)
    }
  }

  if (loading) {
    return <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('settings.loading')}</div>
  }

  if (!data) {
    return (
      <div className="flex flex-col items-start gap-2.5">
        <span className="text-xs" style={{ color: 'var(--red)' }}>{loadError ?? t('settings.loadFailed')}</span>
        <Button variant="outline" size="sm" onClick={() => { Promise.resolve().then(load) }}>{t('settings.retry')}</Button>
      </div>
    )
  }

  const providers = data.providers
  const roleIds = Object.keys(data.roles)
  const flowDirty = Boolean(flowModels && flowDraft && JSON.stringify(flowModels) !== JSON.stringify(flowDraft))
  const flowImageAliases = Object.keys(flowDraft?.image_models ?? {})
  const flowOmniModes = Object.keys(flowDraft?.omni_flash_models ?? {})
  const flowLegacyTiers = Object.keys(flowDraft?.video_models ?? {})

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1.5">
        <h1 className="m-0 text-lg font-semibold" style={{ color: 'var(--text)' }}>{t('settings.title')}</h1>
        <span className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('settings.desc')}</span>
      </div>

      <Card className="py-4">
        <CardHeader>
          <CardTitle className="text-xs tracking-widest uppercase">{t('settings.providers.title')}</CardTitle>
          <CardDescription className="text-[11px]">Kiểm tra cài đặt CLI trên máy. Để thử tài khoản và model, dùng nút Kiểm tra AI ở từng tác vụ bên dưới.</CardDescription>
          <CardAction>
            <Button variant="outline" size="sm" disabled={testing} onClick={runTest}>
              {testing ? t('settings.testing') : t('settings.test')}
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          {testError && <div className="text-[10px] pb-2" style={{ color: 'var(--red)' }}>{testError}</div>}
          <div className="flex flex-col gap-1.5">
            {Object.entries(providers).map(([name, info]) => (
              <div key={name} className="flex items-center gap-3 text-[11px]">
                <span className="w-1.5 h-1.5 rounded-full flex-shrink-0" style={{ background: info.installed ? 'var(--green)' : 'var(--red)' }} />
                <span style={{ width: 90 }}>{info.binary}</span>
                <span style={{ color: 'var(--muted)', width: 100 }}>
                  {info.installed ? t('settings.providers.installed') : t('settings.providers.missing')}
                </span>
                <span style={{ width: 100, color: info.tested === null ? 'var(--muted)' : info.tested ? 'var(--green)' : 'var(--red)' }}>
                  {info.tested === null ? t('settings.providers.untested') : info.tested ? t('settings.providers.ok') : t('settings.providers.failed')}
                </span>
                {info.error && <span className="truncate" style={{ color: 'var(--muted)' }}>{info.error}</span>}
                {name === data.active && (
                  <span className="ml-auto text-[9px] tracking-widest flex-shrink-0" style={{ color: 'var(--accent)' }}>{t('settings.activeProvider')}</span>
                )}
              </div>
            ))}
          </div>
          <CodexConnection refreshVersion={codexAuthRefresh} />
        </CardContent>
      </Card>

      <Card className="py-4">
        <CardHeader>
          <CardTitle className="text-xs tracking-widest uppercase">{t('settings.flowModels.title')}</CardTitle>
          <CardDescription className="text-[11px]">{t('settings.flowModels.desc')}</CardDescription>
          <CardAction>
            <span className="text-[9px] tracking-widest" style={{ color: flowDirty ? 'var(--yellow)' : 'var(--muted)' }}>
              {flowDirty ? t('settings.unsaved') : t('settings.inSync')}
            </span>
          </CardAction>
        </CardHeader>
        <CardContent>
          {flowModelsLoading ? (
            <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('settings.flowModels.loading')}</div>
          ) : !flowDraft ? (
            <div className="flex flex-col items-start gap-2">
              <span className="text-xs" style={{ color: 'var(--red)' }}>{flowModelsState?.text ?? t('settings.flowModels.loadFailed')}</span>
              <Button variant="outline" size="sm" onClick={() => { Promise.resolve().then(loadFlowModels) }}>{t('settings.retry')}</Button>
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              <div className="rounded-md border p-3 text-[11px]" style={{ borderColor: 'var(--border)', background: 'color-mix(in srgb, var(--surface) 72%, transparent)' }}>
                <div className="font-semibold">{t('settings.flowModels.activePath')}</div>
                <div className="grid gap-x-5 gap-y-1 pt-2 sm:grid-cols-2" style={{ color: 'var(--muted)' }}>
                  <div>{t('settings.flowModels.imagePath')}: <code style={{ color: 'var(--text)' }}>{flowDraft.default_image_model} → {flowDraft.image_models[flowDraft.default_image_model] ?? flowDraft.default_image_model}</code></div>
                  <div>{t('settings.flowModels.videoPath')}: <code style={{ color: 'var(--text)' }}>frame_to_video / 8s → {flowDraft.omni_flash_models?.frame_to_video?.['8'] ?? '—'}</code></div>
                </div>
              </div>

              <div className="grid items-end gap-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
                <label className="flex flex-col gap-1 text-[11px]">
                  {t('settings.flowModels.chooseImage')}
                  <select
                    value={flowDraft.default_image_model}
                    onChange={e => { setFlowDraft(prev => prev ? { ...prev, default_image_model: e.target.value } : prev); setFlowModelsState(null) }}
                    className={CONTROL_CLASS}
                    style={CONTROL_STYLE}
                  >
                    {flowImageAliases.map(alias => (
                      <option key={alias} value={alias}>{alias === 'NANO_BANANA_PRO' ? 'Nano Banana Pro' : alias === 'NANO_BANANA_2_LITE' ? 'Nano Banana 2 Lite' : alias === 'NANO_BANANA_2' ? 'Nano Banana 2' : alias} · {flowDraft.image_models[alias]}</option>
                    ))}
                  </select>
                </label>
                <div className="flex flex-col gap-1 text-[11px]">
                  <span>{t('settings.flowModels.chooseVideo')}</span>
                  <div className="rounded px-2 py-1.5" style={CONTROL_STYLE}>Gemini Omni Flash 1.1 · 8s</div>
                </div>
                <Button variant="outline" size="sm" onClick={() => navigate('/models?check=1')}>{t('modelCatalog.checkButton')}</Button>
              </div>
              <p className="text-[11px]" style={{ color: 'var(--muted)' }}>{t('settings.flowModels.chooseHint')}</p>

              <details className="rounded-md border p-3" style={{ borderColor: 'var(--border)' }}>
                <summary className="cursor-pointer text-[10px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('settings.flowModels.advanced')}</summary>
                <div className="mt-3 flex flex-col gap-4">
              <div>
                <div className="mb-2 text-[10px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('settings.flowModels.imageTitle')}</div>
                <div className="grid gap-2 sm:grid-cols-2">
                  {flowImageAliases.map(alias => (
                    <label key={alias} className="flex flex-col gap-1 text-[10px]" style={{ color: 'var(--muted)' }}>
                      {alias}
                      <input value={flowDraft.image_models[alias] ?? ''} onChange={e => updateFlowImage(alias, e.target.value)} className={CONTROL_CLASS} style={CONTROL_STYLE} />
                    </label>
                  ))}
                </div>
              </div>

              <div>
                <div className="mb-2 text-[10px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('settings.flowModels.omniTitle')}</div>
                <div className="flex flex-col gap-2">
                  {flowOmniModes.map(mode => (
                    <div key={mode} className="rounded-md border p-2.5" style={{ borderColor: 'var(--border)' }}>
                      <div className="mb-2 text-[10px] font-semibold">{OMNI_MODE_LABELS[mode] ?? mode}</div>
                      <div className="grid gap-2 sm:grid-cols-4">
                        {OMNI_DURATIONS.map(duration => (
                          <label key={duration} className="flex flex-col gap-1 text-[9px]" style={{ color: 'var(--muted)' }}>
                            {duration}s
                            <input value={flowDraft.omni_flash_models[mode]?.[duration] ?? ''} onChange={e => updateFlowOmni(mode, duration, e.target.value)} className={CONTROL_CLASS} style={CONTROL_STYLE} />
                          </label>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <details>
                <summary className="cursor-pointer text-[10px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('settings.flowModels.legacyTitle')}</summary>
                <div className="mt-2 flex flex-col gap-3">
                  {flowLegacyTiers.map(tier => (
                    <div key={tier} className="rounded-md border p-2.5" style={{ borderColor: 'var(--border)' }}>
                      <div className="mb-2 text-[10px] font-semibold">{tier}</div>
                      {Object.entries(flowDraft.video_models[tier] ?? {}).map(([generation, aspects]) => (
                        <div key={generation} className="mb-2 last:mb-0">
                          <div className="mb-1 text-[9px]" style={{ color: 'var(--muted)' }}>{generation}</div>
                          <div className="grid gap-2 sm:grid-cols-2">
                            {Object.entries(aspects).map(([aspect, value]) => (
                              <label key={aspect} className="flex flex-col gap-1 text-[9px]" style={{ color: 'var(--muted)' }}>
                                {aspect.replace('VIDEO_ASPECT_RATIO_', '')}
                                <input value={value} onChange={e => updateFlowLegacy(tier, generation, aspect, e.target.value)} className={CONTROL_CLASS} style={CONTROL_STYLE} />
                              </label>
                            ))}
                          </div>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </details>
                </div>
              </details>

              <div className="flex flex-wrap items-center gap-3">
                <Button size="sm" disabled={!flowDirty || flowModelsSaving} onClick={saveFlowModels}>
                  {flowModelsSaving ? t('settings.saving') : t('settings.flowModels.save')}
                </Button>
                {flowModelsState && <span className="text-[10px]" style={{ color: flowModelsState.ok ? 'var(--green)' : 'var(--red)' }}>{flowModelsState.text}</span>}
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {roleIds.length === 0 ? (
        <div className="text-xs" style={{ color: 'var(--muted)' }}>{t('settings.empty')}</div>
      ) : roleIds.map(roleId => {
        const draft = drafts[roleId]
        const meta = data.role_meta[roleId]
        const info = providers[draft.provider]
        const efforts = info?.efforts ?? []
        const catalog = models[draft.provider]
        const modelList = catalog?.models ?? []
        const authoritative = catalog?.authoritative ?? false
        const modelBusy = modelsLoading[draft.provider] ?? false
        const custom = customModel[roleId] ?? false
        const knownModel = draft.model !== null && modelList.some(m => m.id === draft.model)
        const effortLocked = (info?.model_encodes_effort ?? false) && draft.model !== null
        const dirty = !sameAssignment(draft, data.roles[roleId])
        const busy = saving[roleId] ?? false
        const state = saveState[roleId]

        return (
          <Card key={roleId} className="py-4">
            <CardHeader>
              <CardTitle className="text-xs tracking-widest uppercase">{meta?.label ?? roleId}</CardTitle>
              <CardDescription className="text-[11px]">{meta?.description ?? roleId}</CardDescription>
              <CardAction>
                <span className="text-[9px] tracking-widest" style={{ color: dirty ? 'var(--yellow)' : 'var(--muted)' }}>
                  {dirty ? t('settings.unsaved') : t('settings.inSync')}
                </span>
              </CardAction>
            </CardHeader>
            <CardContent>
              <fieldset disabled={roleTesting === roleId || busy} className="grid gap-3.5 items-start sm:grid-cols-[1fr_1.6fr_1fr]">
                <div className="flex flex-col gap-1">
                  <span className="text-[9px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('settings.field.agent')}</span>
                  <select
                    value={draft.provider}
                    onChange={e => changeProvider(roleId, e.target.value)}
                    className={CONTROL_CLASS}
                    style={CONTROL_STYLE}
                  >
                    {Object.entries(providers).map(([name, p]) => (
                      <option key={name} value={name} disabled={!p.installed}>
                        {p.installed ? p.binary : `${p.binary} · ${t('settings.notInstalled')}`}
                      </option>
                    ))}
                  </select>
                  {info && !info.installed && (
                    <span className="text-[10px] leading-snug" style={{ color: 'var(--red)' }}>
                      {t('settings.installHint', { binary: info.binary })}
                    </span>
                  )}
                </div>

                <div className="flex flex-col gap-1">
                  <span className="text-[9px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('settings.field.model')}</span>
                  <div className="flex items-center gap-1.5">
                    {custom ? (
                      <input
                        value={draft.model ?? ''}
                        onChange={e => setModel(roleId, e.target.value === '' ? null : e.target.value)}
                        placeholder={t('settings.modelCustomPlaceholder')}
                        className={CONTROL_CLASS}
                        style={CONTROL_STYLE}
                      />
                    ) : (
                      <select
                        value={draft.model ?? DEFAULT_OPTION}
                        onChange={e => selectModel(roleId, e.target.value)}
                        className={CONTROL_CLASS}
                        style={CONTROL_STYLE}
                      >
                        <option value={DEFAULT_OPTION}>{t('settings.optionDefault')}</option>
                        {modelList.map(m => <option key={m.id} value={m.id}>{m.label}</option>)}
                        {draft.model !== null && !knownModel && <option value={draft.model}>{draft.model}</option>}
                        {!authoritative && <option value={CUSTOM_OPTION}>{t('settings.optionCustom')}</option>}
                      </select>
                    )}
                    <Button
                      variant="outline"
                      size="icon-xs"
                      aria-label={t('settings.refreshModels')}
                      title={t('settings.refreshModels')}
                      disabled={modelBusy}
                      onClick={() => { loadModels(draft.provider, true) }}
                    >
                      <RefreshCw className={modelBusy ? 'animate-spin' : undefined} />
                    </Button>
                  </div>
                  <span className="text-[10px] leading-snug" style={{ color: 'var(--muted)' }}>
                    {modelBusy
                      ? t('settings.modelsLoading')
                      : modelList.length === 0
                        ? t('settings.modelsEmpty')
                        : !authoritative
                          ? t('settings.modelsFreeText')
                          : ''}
                  </span>
                  {custom && (
                    <button
                      type="button"
                      className="text-[10px] text-left cursor-pointer bg-transparent border-0 p-0"
                      style={{ color: 'var(--accent)' }}
                      onClick={() => {
                        setCustomModel(prev => ({ ...prev, [roleId]: false }))
                        setModel(roleId, null)
                      }}
                    >
                      {t('settings.modelBackToList')}
                    </button>
                  )}
                </div>

                <div className="flex flex-col gap-1">
                  <span className="text-[9px] tracking-widest" style={{ color: 'var(--muted)' }}>{t('settings.field.effort')}</span>
                  <select
                    value={effortLocked ? DEFAULT_OPTION : draft.effort ?? DEFAULT_OPTION}
                    disabled={effortLocked}
                    onChange={e => updateDraft(roleId, { effort: e.target.value === DEFAULT_OPTION ? null : e.target.value })}
                    className={CONTROL_CLASS}
                    style={{ ...CONTROL_STYLE, opacity: effortLocked ? 0.5 : 1 }}
                  >
                    <option value={DEFAULT_OPTION}>{t('settings.optionDefault')}</option>
                    {efforts.map(x => <option key={x} value={x}>{x}</option>)}
                  </select>
                  {effortLocked && (
                    <span className="text-[10px] leading-snug" style={{ color: 'var(--muted)' }}>
                      {t('settings.effortLockedHint', { binary: info.binary })}
                    </span>
                  )}
                </div>
              </fieldset>

              <div className="flex items-center gap-3 pt-3.5">
                <Button size="sm" disabled={!dirty || busy || roleTesting === roleId} onClick={() => save(roleId)}>
                  {busy ? t('settings.saving') : t('settings.save')}
                </Button>
                <Button variant="outline" size="sm" disabled={roleTesting !== null || busy || !info?.installed} onClick={() => testRole(roleId)}>
                  {roleTesting === roleId ? 'Đang chờ AI phản hồi…' : 'Kiểm tra AI'}
                </Button>
                {state && (
                  <span className="text-[10px] leading-snug" style={{ color: state.ok ? 'var(--green)' : 'var(--red)' }}>{state.text}</span>
                )}
              </div>
              <p className="mt-2 text-[11px]" style={{ color: 'var(--muted)' }}>Kiểm tra dùng cấu hình đang chọn, gửi một yêu cầu nhỏ qua tài khoản CLI và có thể tính vào hạn mức. Bấm Lưu để dùng cho tác vụ sau.</p>
              {roleTests[roleId] && <p role="status" className="mt-2 text-xs" style={{ color: roleTests[roleId].ok ? 'var(--green)' : 'var(--red)' }}>{roleTests[roleId].text}</p>}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}
