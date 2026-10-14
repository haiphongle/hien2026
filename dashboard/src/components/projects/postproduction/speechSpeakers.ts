import type { SpeechDocument } from './speechTypes'

export function assignNarratorVoice(document: SpeechDocument, voiceId: string): SpeechDocument {
  if (!voiceId) throw new Error('Chọn giọng đọc trước khi áp dụng.')
  return { ...document, cues: document.cues.map(cue => ({ ...cue, voice_id: voiceId })) }
}

export function renameSpeaker(document: SpeechDocument, oldName: string, newName: string): SpeechDocument {
  const name = newName.trim()
  if (!name || name.length > 80) throw new Error('Tên người nói phải có từ 1 đến 80 ký tự.')
  return { ...document, cues: document.cues.map(cue => cue.speaker === oldName ? { ...cue, speaker: name } : cue) }
}

export function applySpeakers(document: SpeechDocument, assignments: Record<string, string>,
  names: Record<string, string>, voices: Record<string, string>): SpeechDocument {
  return { ...document, cues: document.cues.map(cue => {
    const label = assignments[cue.id]
    if (!label) return cue
    const name = (names[label] ?? label).trim()
    if (!name || name.length > 80) throw new Error('Đặt tên cho từng người nói, tối đa 80 ký tự.')
    // Preserve timing and text exactly. A newly assigned person must not silently
    // inherit the previous person's voice; the user chooses a voice explicitly.
    return { ...cue, speaker: name, voice_id: voices[label] || (name === cue.speaker ? cue.voice_id : '') }
  }) }
}
