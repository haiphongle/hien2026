import assert from 'node:assert/strict'
import test from 'node:test'
import { initialSpeechJobMonitor, speechJobMonitor } from '../src/components/projects/postproduction/speechJobMonitor.ts'
import type { SpeechJob } from '../src/components/projects/postproduction/speechTypes.ts'

const job = (job_id: string, status = 'PROCESSING', completed = 1): SpeechJob => ({
  job_id, status, completed, total: 4, stage: 'generating', current: null, error: null, results: {},
})

test('lost submission response stays locked through connection failures and recovers progress without resubmitting', () => {
  let state = speechJobMonitor(initialSpeechJobMonitor, { type: 'checked', job: job('old', 'COMPLETED', 4) })
  state = speechJobMonitor(state, { type: 'submission-uncertain', error: 'API 502: Bad Gateway' })
  assert.equal(state.checking, true)
  state = speechJobMonitor(state, { type: 'check-failed', error: 'Network unavailable' })
  assert.equal(state.checking, true)
  assert.equal(state.submissionError, 'API 502: Bad Gateway')
  state = speechJobMonitor(state, { type: 'checked', job: job('new', 'PROCESSING', 2) })
  assert.equal(state.job?.completed, 2)
  assert.equal(state.recovered, true)
  assert.equal(state.submissionError, '')
  assert.equal(state.pollError, '')
  state = speechJobMonitor(state, { type: 'checked', job: job('new', 'COMPLETED', 4) })
  assert.equal(state.job?.status, 'COMPLETED')
  assert.equal(state.checking, false)
})

test('rejected request cannot mistake a previous completed job for a successful new submission', () => {
  const old = job('old', 'COMPLETED', 4)
  let state = speechJobMonitor(initialSpeechJobMonitor, { type: 'checked', job: old })
  state = speechJobMonitor(state, { type: 'submission-uncertain', error: 'Revision changed' })
  state = speechJobMonitor(state, { type: 'checked', job: old })
  assert.equal(state.checking, false)
  assert.equal(state.recovered, false)
  assert.equal(state.submissionError, 'Revision changed')
})

test('first submission rejected before any job exists permits a deliberate retry after status is confirmed', () => {
  let state = speechJobMonitor(initialSpeechJobMonitor, { type: 'submission-uncertain', error: 'No voice selected' })
  state = speechJobMonitor(state, { type: 'checked', job: null })
  assert.equal(state.checking, false)
  assert.equal(state.recovered, false)
  assert.equal(state.submissionError, 'No voice selected')
  state = speechJobMonitor(state, { type: 'queued', job: job('retry', 'QUEUED', 0) })
  assert.equal(state.submissionError, '')
  assert.equal(state.checking, true)
})

test('recovers a fast job that finished before the first status read, including terminal failure', () => {
  for (const status of ['COMPLETED', 'FAILED']) {
    let state = speechJobMonitor(initialSpeechJobMonitor, { type: 'submission-uncertain', error: 'Response lost' })
    state = speechJobMonitor(state, { type: 'checked', job: job('fast', status, 4) })
    assert.equal(state.job?.status, status)
    assert.equal(state.recovered, true)
    assert.equal(state.submissionError, '')
  }
})

test('conflict with an already active job resumes that job even when its id is unchanged', () => {
  let state = speechJobMonitor(initialSpeechJobMonitor, { type: 'checked', job: job('existing') })
  state = speechJobMonitor(state, { type: 'submission-uncertain', error: 'API 409: busy' })
  state = speechJobMonitor(state, { type: 'checked', job: job('existing', 'PROCESSING', 3) })
  assert.equal(state.recovered, true)
  assert.equal(state.job?.completed, 3)
  assert.equal(state.submissionError, '')
})

test('successful submission still checks the final status, and a later polling error keeps controls locked', () => {
  let state = speechJobMonitor(initialSpeechJobMonitor, { type: 'queued', job: job('cached', 'COMPLETED', 4) })
  assert.equal(state.checking, true)
  state = speechJobMonitor(state, { type: 'check-failed', error: 'Offline' })
  assert.equal(state.checking, true)
  state = speechJobMonitor(state, { type: 'checked', job: job('cached', 'COMPLETED', 4) })
  assert.equal(state.checking, false)
  assert.equal(state.pollError, '')
})
