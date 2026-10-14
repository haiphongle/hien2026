import { useState } from 'react'
import { Loader2, Square } from 'lucide-react'
import type { AudioMixController, AudioMixJob } from '../../../api/useAudioMix'
import { resolveMediaUrl } from '../../../api/client'
import { useTranslation } from '../../../i18n/useTranslation'

export function AudioMixProgress({ mix, kind, showResult = true }: { mix: AudioMixController; kind: AudioMixJob['kind']; showResult?: boolean }) {
  const { t } = useTranslation()
  const [cancelError, setCancelError] = useState('')
  const job = mix.job?.kind === kind ? mix.job : null
  if (!job && mix.pendingKind !== kind && !mix.pollError) return null
  const stage = mix.pendingKind === kind ? 'submitting' : job?.status === 'FAILED' && job.stage !== 'interrupted' ? 'failed' : job?.stage ?? 'waiting'
  const active = mix.pendingKind === kind || job?.status === 'QUEUED' || job?.status === 'PROCESSING' || job?.status === 'CANCELLING'
  const progress = mix.pendingKind === kind ? 0 : job?.progress ?? 0
  return <div className="space-y-2 rounded-md p-3 text-xs" style={{ border: '1px solid var(--border)', background: 'var(--surface)' }}>
    <div role="status" className="flex items-center justify-between gap-2">
      <span className="inline-flex items-center gap-2">{active && <Loader2 className="size-3.5 animate-spin" />}{t(`postProduction.mixStage.${stage}`)}</span>
      {job && <span className="tabular-nums">{progress}% · {t('postProduction.mixElapsed', { n: job.elapsed_seconds })}</span>}
    </div>
    <progress aria-label={t('postProduction.mixProgress')} max={100} value={progress} className="h-2 w-full" style={{ accentColor: 'var(--accent)' }} />
    {(job?.variant_total ?? 1) > 1 && <p>Bản {job?.variant_index} / {job?.variant_total}</p>}
    {active && job && <button disabled={job.status === 'CANCELLING'} className="inline-flex items-center gap-1 underline" onClick={() => { setCancelError(''); void mix.cancel().catch(cause => setCancelError(String(cause))) }}><Square size={12} />{job.status === 'CANCELLING' ? 'Đang dừng và dọn file tạm…' : 'Hủy tác vụ'}</button>}
    {cancelError && <p role="alert">{cancelError}</p>}
    {active && <p className="m-0 text-[10px]" style={{ color: 'var(--muted)' }}>{t('postProduction.mixProgressHelp')}</p>}
    {mix.pollError && <p role="alert" className="m-0" style={{ color: 'var(--red)' }}>{t('postProduction.mixConnectionError')}</p>}
    {job?.error && <p role="alert" className="m-0 whitespace-pre-wrap" style={{ color: 'var(--red)' }}>{job.error}</p>}
    {showResult && job?.status === 'COMPLETED' && job.result?.url && <a href={resolveMediaUrl(job.result.url)} target="_blank" rel="noreferrer" className="underline">{t('postProduction.mixOpenVideo')}</a>}
  </div>
}
