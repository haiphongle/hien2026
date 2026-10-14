export const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

/** Turn API media paths (for example /api/videos/:id/file) into URLs that
 * still point at the local FastAPI server when the dashboard is hosted on
 * Firebase. Absolute Google Flow URLs are returned unchanged. */
export function resolveMediaUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined
  if (/^(?:[a-z][a-z\d+.-]*:)?\/\//i.test(url) || url.startsWith('data:') || url.startsWith('blob:')) return url
  return `${API_BASE_URL}${url.startsWith('/') ? url : `/${url}`}`
}

export async function fetchAPI<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = new Headers(options?.headers)
  if (!(options?.body instanceof FormData) && !(options?.body instanceof Blob) && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
  const res = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
  })
  if (!res.ok) {
    const err = await res.text().catch(() => res.statusText)
    throw new Error(`API ${res.status}: ${err}`)
  }
  return res.json()
}

export async function patchAPI<T>(path: string, body: Record<string, unknown>): Promise<T> {
  return fetchAPI<T>(path, { method: 'PATCH', body: JSON.stringify(body) })
}
