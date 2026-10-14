import type { Visuals } from './types.ts'

/** A waveform is immutable for an asset ID. Share requests across editor renders. */
export class VisualsLoader {
  private controller = new AbortController()
  private cache = new Map<string, { value: Promise<Visuals | null>; settled: boolean; empty: boolean }>()
  private queue: (() => Promise<void>)[] = []
  private active = 0
  private fetchVisual: (id: string, signal: AbortSignal) => Promise<Visuals | null>
  private reportError: (cause: unknown) => void

  constructor(fetchVisual: (id: string, signal: AbortSignal) => Promise<Visuals | null>, reportError: (cause: unknown) => void) {
    this.fetchVisual = fetchVisual
    this.reportError = reportError
  }
  private pump() {
    while (this.active < 4 && this.queue.length) {
      const run = this.queue.shift()!
      this.active++
      void run().finally(() => { this.active--; this.pump() })
    }
  }
  private get(id: string, retry: boolean): Promise<Visuals | null> {
    const cached = this.cache.get(id)
    // A manual retry refreshes empty/failed responses, never duplicates an in-flight request.
    if (cached && !(retry && cached.settled && cached.empty)) return cached.value
    let resolve!: (value: Visuals | null) => void
    const entry = { value: new Promise<Visuals | null>(done => { resolve = done }), settled: false, empty: true }
    this.cache.set(id, entry)
    this.queue.push(async () => {
      let value: Visuals | null = null
      try {
        if (!this.controller.signal.aborted) value = await this.fetchVisual(id, this.controller.signal)
      } catch (cause) {
        if (!this.controller.signal.aborted) this.reportError(cause)
      } finally {
        entry.settled = true; entry.empty = !value
        resolve(this.controller.signal.aborted ? null : value)
      }
    })
    this.pump()
    return entry.value
  }
  async load(ids: string[], retry = false): Promise<Record<string, Visuals>> {
    const values = await Promise.all([...new Set(ids)].map(async id => [id, await this.get(id, retry)] as const))
    return Object.fromEntries(values.filter((item): item is readonly [string, Visuals] => item[1] !== null))
  }
  dispose() { this.controller.abort() }
}
