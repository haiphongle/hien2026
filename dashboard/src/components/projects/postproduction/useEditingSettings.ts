import { useEffect, useRef, useState } from 'react'
import { fetchAPI } from '../../../api/client'
import type { EditingSettings, EditingState } from './editingTypes'

export function useEditingSettings(videoId: string) {
  const [state, setState] = useState<EditingState | null>(null)
  const [settings, setSettings] = useState<EditingSettings | null>(null)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [historyVersion, setHistoryVersion] = useState(0)
  const [reload, setReload] = useState(0)
  const current = useRef<EditingSettings | null>(null)
  const revision = useRef(0)
  const saved = useRef('')
  const undo = useRef<EditingSettings[]>([])
  const redo = useRef<EditingSettings[]>([])
  const request = useRef<Promise<number> | null>(null)
  const alive = useRef(true)

  useEffect(() => {
    if (!videoId) return
    alive.current = true
    const controller = new AbortController()
    void fetchAPI<EditingState>(`/api/videos/${videoId}/editing`, { signal: controller.signal }).then(data => {
      if (controller.signal.aborted) return
      revision.current = data.revision
      saved.current = JSON.stringify(data.settings)
      current.current = data.settings
      undo.current = []; redo.current = []
      setState(data); setSettings(data.settings); setDirty(false); setError(''); setHistoryVersion(v => v + 1)
    }).catch(cause => { if (!controller.signal.aborted) setError(String(cause)) })
    return () => { alive.current = false; controller.abort() }
  }, [videoId, reload])

  async function save(): Promise<number> {
    while (request.current) await request.current
    const snapshot = current.current
    if (!snapshot || saved.current === JSON.stringify(snapshot)) return revision.current
    setSaving(true)
    const pending = fetchAPI<EditingState>(`/api/videos/${videoId}/editing`, {
      method: 'PUT', body: JSON.stringify({ revision: revision.current, settings: snapshot }),
    }).then(data => {
      revision.current = data.revision
      saved.current = JSON.stringify(snapshot)
      if (alive.current) {
        setState(previous => previous ? { ...previous, revision: data.revision, last_export: data.last_export, exports: data.exports } : previous)
        setDirty(JSON.stringify(current.current) !== saved.current)
        setError('')
      }
      return data.revision
    }).catch(cause => {
      if (alive.current) setError(String(cause))
      throw cause
    }).finally(() => {
      if (request.current === pending) request.current = null
      if (alive.current) setSaving(false)
    })
    request.current = pending
    return pending
  }

  useEffect(() => {
    if (!settings || !dirty || error) return
    const timer = window.setTimeout(() => { void save().catch(() => {}) }, 650)
    return () => window.clearTimeout(timer)
    // save reads the current draft and serializes writes; no stale closure settings.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings, dirty, error, videoId])

  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])

  function apply(next: EditingSettings, remember = true) {
    if (remember && current.current) {
      undo.current = [...undo.current.slice(-49), current.current]; redo.current = []
    }
    current.current = next
    setSettings(next); setDirty(JSON.stringify(next) !== saved.current); setHistoryVersion(v => v + 1)
  }
  function travel(back: boolean) {
    const from = back ? undo.current : redo.current
    const to = back ? redo.current : undo.current
    const next = from.pop()
    if (!next || !current.current) return
    to.push(current.current); apply(next, false)
  }

  async function refreshSources() {
    if (!videoId) return
    const data = await fetchAPI<EditingState>(`/api/videos/${videoId}/editing`)
    if (alive.current) setState(previous => previous ? { ...previous, source: data.source, fonts: data.fonts,
      speech_cues: data.speech_cues, scene_texts: data.scene_texts, music: data.music,
      preview_available: data.preview_available, last_export: data.last_export, exports: data.exports } : previous)
  }

  return { state, settings, error, saving, dirty, apply, save, reload: () => setReload(v => v + 1),
    refreshSources,
    undo: () => travel(true), redo: () => travel(false),
    canUndo: historyVersion >= 0 && undo.current.length > 0, canRedo: historyVersion >= 0 && redo.current.length > 0 }
}
