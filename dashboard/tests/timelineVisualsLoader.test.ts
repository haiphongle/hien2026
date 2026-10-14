import assert from 'node:assert/strict'
import test from 'node:test'
import { VisualsLoader } from '../src/components/projects/postproduction/timeline/visualsLoader.ts'
import type { Visuals } from '../src/components/projects/postproduction/timeline/types.ts'

const visual: Visuals = { peaks: [.1, .2], interval: .032, duration: .064, thumbnail_count: 0 }
const flush = async () => { for (let i = 0; i < 10; i++) await Promise.resolve() }

test('119 voices use at most four concurrent requests and share pending loads across edits', async () => {
  const pending: (() => void)[] = [], calls: string[] = []
  let active = 0, maximum = 0
  const loader = new VisualsLoader(async id => {
    calls.push(id); active++; maximum = Math.max(maximum, active)
    await new Promise<void>(done => pending.push(done))
    active--; return visual
  }, cause => { throw cause })
  const ids = Array.from({ length: 119 }, (_, i) => `voice-${i}`)
  const first = loader.load(ids), repeated = loader.load(ids), manual = loader.load(ids, true)
  assert.equal(calls.length, 4)
  while (pending.length) { pending.splice(0).forEach(done => done()); await flush() }
  const results = await Promise.all([first, repeated, manual])
  assert.equal(maximum, 4)
  assert.equal(calls.length, 119)
  assert.equal(Object.keys(results[0]).length, 119)
  assert.deepEqual(results[0], results[1])
  loader.dispose()
})

test('empty or failed summaries do not refetch on every render; explicit retry can recover', async () => {
  let calls = 0, available = false
  const errors: unknown[] = []
  const loader = new VisualsLoader(async id => {
    calls++
    if (available) return visual
    if (id === 'bad') throw new Error('network')
    return null
  }, cause => errors.push(cause))
  assert.deepEqual(await loader.load(['bad', 'empty']), {})
  for (let i = 0; i < 10; i++) assert.deepEqual(await loader.load(['bad', 'empty']), {})
  assert.equal(calls, 2)
  assert.equal(errors.length, 1)
  available = true
  assert.equal(Object.keys(await loader.load(['bad', 'empty'], true)).length, 2)
  assert.equal(calls, 4)
  loader.dispose()
})

test('leaving the editor aborts active loads and never starts queued network requests', async () => {
  let calls = 0
  const errors: unknown[] = []
  const loader = new VisualsLoader(async (_id, signal) => {
    calls++
    await new Promise<void>((_done, reject) => signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }))
    return visual
  }, cause => errors.push(cause))
  const request = loader.load(Array.from({ length: 40 }, (_, i) => String(i)))
  loader.dispose()
  assert.deepEqual(await request, {})
  assert.equal(calls, 4)
  assert.deepEqual(errors, [])
})
