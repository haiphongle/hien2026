import assert from 'node:assert/strict'
import test from 'node:test'
import { limitSpeechPreview } from '../src/components/projects/postproduction/speechPlayback.ts'

class Preview extends EventTarget {
  readyState = 0
  duration = Number.NaN
  currentTime = 0
  paused = false
  pause() { this.paused = true }
  metadata(duration: number) {
    this.duration = duration; this.readyState = 1
    this.dispatchEvent(new Event('loadedmetadata'))
  }
}

test('source preview seeks after metadata and stops at the selected cue end', () => {
  const audio = new Preview()
  limitSpeechPreview(audio as unknown as HTMLAudioElement, 8.5, 15.5)
  assert.equal(audio.currentTime, 0)
  audio.metadata(16)
  assert.equal(audio.currentTime, 8.5)
  audio.currentTime = 15
  audio.dispatchEvent(new Event('timeupdate'))
  assert.equal(audio.paused, false)
  audio.currentTime = 15.5
  audio.dispatchEvent(new Event('timeupdate'))
  assert.equal(audio.paused, true)
})

test('full voice demos remain playable and out-of-range starts are clamped', () => {
  const demo = new Preview()
  demo.metadata(2)
  limitSpeechPreview(demo as unknown as HTMLAudioElement)
  demo.currentTime = 1.5
  demo.dispatchEvent(new Event('timeupdate'))
  assert.equal(demo.paused, false)
  const short = new Preview()
  limitSpeechPreview(short as unknown as HTMLAudioElement, 10)
  short.metadata(2)
  assert.equal(short.currentTime, 1.99)
})
