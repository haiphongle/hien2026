import type { SpeechJob } from './speechTypes'

export interface SpeechJobMonitor {
  job: SpeechJob | null
  checking: boolean
  pollError: string
  submissionError: string
  previousJobId: string | null
  recovered: boolean
}

export const initialSpeechJobMonitor: SpeechJobMonitor = {
  job: null, checking: false, pollError: '', submissionError: '', previousJobId: null, recovered: false,
}

type Event =
  | { type: 'queued'; job: SpeechJob }
  | { type: 'submission-uncertain'; error: string }
  | { type: 'check-failed'; error: string }
  | { type: 'checked'; job: SpeechJob | null }

/** A failed POST response does not tell us whether the server started a job.
 * Keep generation locked until a successful status read resolves that uncertainty. */
export function speechJobMonitor(state: SpeechJobMonitor, event: Event): SpeechJobMonitor {
  switch (event.type) {
    case 'queued':
      return { ...initialSpeechJobMonitor, job: event.job, checking: true }
    case 'submission-uncertain':
      return { ...state, checking: true, pollError: '', submissionError: event.error,
        previousJobId: state.job?.job_id ?? null, recovered: false }
    case 'check-failed':
      return { ...state, checking: true, pollError: event.error }
    case 'checked': {
      const foundJob = Boolean(state.submissionError && event.job &&
        (event.job.job_id !== state.previousJobId || ['QUEUED', 'PROCESSING'].includes(event.job.status)))
      return { ...state, job: event.job, checking: false, pollError: '',
        submissionError: foundJob ? '' : state.submissionError, recovered: foundJob || state.recovered }
    }
  }
}
