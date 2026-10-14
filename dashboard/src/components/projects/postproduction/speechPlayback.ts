/** Seek after metadata is ready and stop a review preview at its requested end. */
export function limitSpeechPreview(audio: HTMLAudioElement, start = 0, end?: number) {
  const offset = Number.isFinite(start) ? Math.max(0, start) : 0
  const stop = end !== undefined && Number.isFinite(end) && end > offset ? end : undefined
  function seek() {
    const last = Number.isFinite(audio.duration) ? Math.max(0, audio.duration - .01) : offset
    audio.currentTime = Math.min(offset, last)
  }
  if (audio.readyState >= 1) seek()
  else audio.addEventListener('loadedmetadata', seek, { once: true })
  if (stop !== undefined) audio.addEventListener('timeupdate', () => {
    if (audio.currentTime >= stop) audio.pause()
  })
}
