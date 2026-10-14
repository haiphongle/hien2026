import { useEffect, useEffectEvent } from 'react'
import { fetchAPI } from './client'

/** Poll after the previous response finishes; cancel requests when the job changes. */
export function usePolling<T>(
  path: string | null,
  intervalMs: number,
  onResult: (result: T) => void,
  onError: (error: unknown) => void,
) {
  const reportResult = useEffectEvent(onResult)
  const reportError = useEffectEvent(onError)

  useEffect(() => {
    if (!path) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    async function poll() {
      try {
        const result = await fetchAPI<T>(path!, { signal: controller.signal })
        if (!controller.signal.aborted) reportResult(result)
      } catch (error) {
        if (!controller.signal.aborted) reportError(error)
      }
      if (!controller.signal.aborted) timer = setTimeout(() => void poll(), intervalMs)
    }
    void poll()
    return () => {
      controller.abort()
      clearTimeout(timer)
    }
  }, [path, intervalMs])
}
