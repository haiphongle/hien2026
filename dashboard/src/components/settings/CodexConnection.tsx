import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, ExternalLink, LoaderCircle, LogIn, RefreshCw } from 'lucide-react'
import { fetchAPI } from '../../api/client'
import { Button } from '../ui/button'

interface CodexAuthState {
  state: 'connected' | 'signed_out' | 'expired' | 'unknown' | 'missing' | 'api_key'
  message: string
  method: string | null
  checked_at: number | null
  login: {
    id: string
    status: 'starting' | 'pending' | 'completed' | 'failed' | 'cancelled' | 'expired'
    auth_url: string | null
    expires_at: number
    message: string
  } | null
}

const titles: Record<CodexAuthState['state'], string> = {
  connected: 'Codex đã kết nối',
  signed_out: 'Codex chưa đăng nhập',
  expired: 'Codex cần đăng nhập lại',
  unknown: 'Chưa xác nhận được kết nối Codex',
  missing: 'Chưa cài Codex CLI',
  api_key: 'Codex đang dùng API key',
}
const headers = { 'X-FlowKit-Local-Auth': '1' }

function errorMessage(error: unknown): string {
  const raw = error instanceof Error ? error.message : 'Không kết nối được với FrameWeaver.'
  try {
    const detail: unknown = JSON.parse(raw.replace(/^API \d+: /, '')).detail
    if (typeof detail === 'string') return detail
  } catch { /* Network errors are plain text. */ }
  return 'Chưa đọc được trạng thái Codex. Kiểm tra FrameWeaver đang chạy rồi thử lại.'
}

function loginUrl(value: string | null | undefined): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && ['auth.openai.com', 'chatgpt.com'].includes(url.hostname)
      && !url.username && !url.password && (!url.port || url.port === '443') ? url.href : null
  } catch { return null }
}

export default function CodexConnection({ refreshVersion = 0 }: { refreshVersion?: number }) {
  const [account, setAccount] = useState<CodexAuthState | null>(null)
  const [checking, setChecking] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(false)
  const actionPending = useRef(false)
  const generation = useRef(0)
  const pending = account?.login?.status === 'starting' || account?.login?.status === 'pending'
  const authUrl = loginUrl(account?.login?.auth_url)

  const refresh = useCallback(async (signal?: AbortSignal, force = false) => {
    const current = generation.current
    try {
      const next = await fetchAPI<CodexAuthState>(`/api/providers/codex/auth?refresh=${force}`, { headers, signal })
      if (!signal?.aborted && mounted.current && current === generation.current) { setAccount(next); setError(null) }
    } catch (err) {
      if (!signal?.aborted && mounted.current && current === generation.current) setError(errorMessage(err))
    } finally {
      if (!signal?.aborted && mounted.current) setChecking(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    const controller = new AbortController()
    void refresh(controller.signal, refreshVersion > 0)
    return () => { mounted.current = false; controller.abort() }
  }, [refresh, refreshVersion])

  useEffect(() => {
    if (!pending) return
    const controller = new AbortController()
    let loading = false
    const timer = window.setInterval(async () => {
      if (loading || document.hidden || actionPending.current) return
      loading = true
      await refresh(controller.signal)
      loading = false
    }, 2000)
    return () => { window.clearInterval(timer); controller.abort() }
  }, [pending, refresh])

  useEffect(() => {
    const controller = new AbortController()
    const onFocus = () => { if (!actionPending.current) void refresh(controller.signal) }
    window.addEventListener('focus', onFocus)
    return () => { window.removeEventListener('focus', onFocus); controller.abort() }
  }, [refresh])

  async function startLogin() {
    if (actionPending.current) return
    actionPending.current = true
    generation.current += 1
    setBusy(true); setError(null)
    // Reserve the tab during the user's click so browsers don't block the OAuth popup.
    const popup = window.open('about:blank', '_blank')
    if (popup) popup.opener = null
    try {
      const next = await fetchAPI<CodexAuthState>('/api/providers/codex/login', { method: 'POST', headers, body: '{}' })
      const url = loginUrl(next.login?.auth_url)
      if (mounted.current) setAccount(next)
      if (url && popup && !popup.closed) popup.location.replace(url)
      else popup?.close()
      if (next.login?.status === 'completed') await refresh()
      // A real link remains available below when popups are blocked.
    } catch (err) {
      popup?.close()
      if (mounted.current) setError(errorMessage(err))
    } finally {
      actionPending.current = false
      if (mounted.current) setBusy(false)
    }
  }

  async function cancelLogin() {
    if (!account?.login || actionPending.current) return
    actionPending.current = true
    generation.current += 1
    setBusy(true); setError(null)
    try {
      const next = await fetchAPI<CodexAuthState>('/api/providers/codex/login/cancel', {
        method: 'POST', headers, body: JSON.stringify({ login_id: account.login.id }),
      })
      if (mounted.current) setAccount(next)
    } catch (err) {
      if (mounted.current) setError(errorMessage(err))
    } finally {
      actionPending.current = false
      if (mounted.current) setBusy(false)
    }
  }

  const connected = account?.state === 'connected'
  const title = busy && !pending ? 'Đang mở kết nối Codex…' : pending ? 'Đang chờ bạn đăng nhập ChatGPT'
    : account ? titles[account.state] : checking ? 'Đang kiểm tra tài khoản Codex…' : 'Chưa đọc được trạng thái Codex'

  return (
    <section aria-label="Kết nối tài khoản Codex" className="mt-4 rounded-lg border p-4"
      style={{ borderColor: 'var(--border)', background: 'var(--surface)' }}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div className="mt-0.5" style={{ color: pending || busy ? 'var(--accent)' : connected ? 'var(--green)' : 'var(--muted)' }}>
            {pending || busy || checking ? <LoaderCircle size={19} className="animate-spin" />
              : connected ? <CheckCircle2 size={19} /> : <LogIn size={19} />}
          </div>
          <div className="min-w-0 space-y-1.5" role="status" aria-live="polite">
            <p className="text-sm font-semibold">{title}</p>
            <p className="text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>
              {pending ? account?.login?.message : account?.message}
            </p>
            <p className="text-[11px] leading-relaxed" style={{ color: 'var(--muted)' }}>
              Phiên đăng nhập dùng chung với Codex trên máy này. Bạn đăng nhập trực tiếp trên trang ChatGPT.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {pending ? <>
            {authUrl && <a href={authUrl} target="_blank" rel="noopener noreferrer" referrerPolicy="no-referrer"
              className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-xs"
              style={{ borderColor: 'var(--border)', color: 'var(--accent)' }}>
              <ExternalLink size={14} /> Mở trang đăng nhập ChatGPT
            </a>}
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void cancelLogin()}>Hủy đăng nhập</Button>
          </> : <>
            <Button variant="outline" size="sm" disabled={busy || checking} onClick={() => { setChecking(true); void refresh(undefined, true) }}>
              <RefreshCw size={14} className={checking ? 'animate-spin' : undefined} /> Kiểm tra kết nối
            </Button>
            <Button variant={connected ? 'outline' : 'default'} size="sm" disabled={busy || checking || !account || account.state === 'missing'}
              onClick={() => void startLogin()}>
              <LogIn size={14} /> {account?.state === 'signed_out' ? 'Đăng nhập bằng ChatGPT' : 'Đăng nhập lại bằng ChatGPT'}
            </Button>
          </>}
        </div>
      </div>
      {!pending && account?.login && <p className="mt-3 text-xs" role="status"
        style={{ color: account.login.status === 'completed' ? 'var(--green)' : 'var(--muted)' }}>{account.login.message}</p>}
      {error && <p className="mt-3 text-xs" role="alert" style={{ color: 'var(--red)' }}>{error}</p>}
    </section>
  )
}
