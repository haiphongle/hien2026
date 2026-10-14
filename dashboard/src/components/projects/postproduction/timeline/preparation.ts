import type { Asset, Document } from './types.ts'

/** One background preparation job at a time; failed/cancelled jobs need a manual retry. */
export function nextPreparation(doc: Document, assets: Asset[], attemptedVisuals: Set<string>, attemptedProxies: Set<string>) {
  const tracks = new Map(doc.tracks.map(t=>[t.id,t]))
  const used = new Set(doc.clips.flatMap(c=>{
    const t = tracks.get(c.track_id)
    const picture = t?.kind==='video'||t?.kind==='overlay'
    const voice = doc.ducking?.enabled&&doc.ducking.detect_pauses&&t?.kind==='audio'&&t.role==='voice'&&!t.muted&&!c.muted&&c.gain>0
    return c.asset_id&&(picture||voice)?[c.asset_id]:[]
  }))
  const candidates = assets.filter(a=>used.has(a.id)&&!a.missing)
  const visuals = candidates.filter(a=>!a.visuals_ready&&!attemptedVisuals.has(a.id)).slice(0,8)
  if (visuals.length) return { kind: 'visuals' as const, ids: visuals.map(a=>a.id) }
  const proxy = candidates.find(a=>a.kind==='video'&&a.duration>=180&&!a.proxy_url&&!attemptedProxies.has(a.id))
  return proxy ? { kind: 'proxies' as const, ids: [proxy.id] } : null
}
