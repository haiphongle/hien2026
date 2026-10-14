import { useRef } from 'react';
import type { Clip } from './types';
export function AudioHandles({ clip, scale, disabled, onCommit }: {
    clip: Clip;
    scale: number;
    disabled: boolean;
    onCommit: (changes: Partial<Clip>) => void;
}) {
    const drag = useRef<{
        x: number;
        y: number;
        kind: 'gain' | 'in' | 'out';
        value: number;
        next: number;
    } | null>(null);
    const begin = (e: React.PointerEvent, kind: 'gain' | 'in' | 'out') => { e.stopPropagation(); if (disabled)
        return; e.currentTarget.setPointerCapture(e.pointerId); const value = kind === 'gain' ? clip.gain : kind === 'in' ? clip.fade_in ?? 0 : clip.fade_out ?? 0; drag.current = { x: e.clientX, y: e.clientY, kind, value, next: value }; };
    const move = (e: React.PointerEvent) => { const g = drag.current; if (!g)
        return; g.next = g.kind === 'gain' ? Math.max(0, Math.min(2, g.value + (g.y - e.clientY) / 24)) : Math.max(0, Math.min(clip.duration - (g.kind === 'in' ? clip.fade_out ?? 0 : clip.fade_in ?? 0), g.value + (e.clientX - g.x) / scale * (g.kind === 'in' ? 1 : -1))); e.currentTarget.setAttribute('title', g.kind === 'gain' ? `${Math.round(g.next * 100)}%` : `${g.next.toFixed(2)}s`); if (g.kind === 'gain')
        (e.currentTarget as HTMLElement).style.top = `${52 - g.next * 16}px`;
    else
        (e.currentTarget as HTMLElement).style[g.kind === 'in' ? 'left' : 'right'] = `${g.next * scale}px`; };
    const finish = (e: React.PointerEvent, cancel = false) => { const g = drag.current; if (!g)
        return; drag.current = null; if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
        const value = cancel ? g.value : g.next;
        if (g.kind === 'gain') (e.currentTarget as HTMLElement).style.top = `${52 - value * 16}px`;
        else (e.currentTarget as HTMLElement).style[g.kind === 'in' ? 'left' : 'right'] = `${value * scale}px`;
        if (!cancel)
        onCommit(g.kind === 'gain' ? { gain: g.next } : g.kind === 'in' ? { fade_in: g.next } : { fade_out: g.next }); };
    const props = { onPointerMove: move, onPointerUp: (e: React.PointerEvent) => finish(e), onPointerCancel: (e: React.PointerEvent) => finish(e, true), onLostPointerCapture: (e: React.PointerEvent) => finish(e, true) };
    return <>
    <div className="tl-gain-line" style={{ top: 52 - clip.gain * 16 }} role="slider" aria-label="Kéo âm lượng đoạn" aria-valuemin={0} aria-valuemax={200} aria-valuenow={Math.round(clip.gain * 100)} onPointerDown={e => begin(e, 'gain')} {...props} onDoubleClick={e => { e.stopPropagation(); if (disabled)
        return; const box = e.currentTarget.parentElement!.getBoundingClientRect(); const time = Math.max(0, Math.min(clip.duration, (e.clientX - box.left) / scale)); if ((clip.envelope?.length ?? 0) >= 32)
        return; const points = [...(clip.envelope ?? []), { time, value: 1 }].sort((a, b) => a.time - b.time).filter((p, i, a) => !i || p.time - a[i - 1].time > .01); onCommit({ envelope: points }); }} title="Kéo âm lượng · bấm đúp thêm điểm"/>
    {(clip.envelope ?? []).map((p, i) => <span key={i} className="tl-gain-point" style={{ left: p.time * scale, top: 52 - clip.gain * p.value * 16 }} title="Kéo điểm âm lượng · bấm đúp để xóa" onPointerDown={e => { e.stopPropagation(); if (disabled)
        return; e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, kind: 'gain', value: p.value, next: p.value }; }} onPointerMove={e => { const g = drag.current; if (!g)
        return; g.next = Math.max(0, Math.min(2, g.value + (g.y - e.clientY) / 24)); (e.currentTarget as HTMLElement).style.top = `${52 - clip.gain * g.next * 16}px`; }} onPointerUp={e => { const g = drag.current; if (!g)
        return; drag.current = null; e.currentTarget.releasePointerCapture(e.pointerId); onCommit({ envelope: clip.envelope!.map((point, index) => index === i ? { ...point, value: g.next } : point) }); }} onPointerCancel={e => { const g=drag.current;drag.current = null;if(g)(e.currentTarget as HTMLElement).style.top=`${52-clip.gain*g.value*16}px`; }} onLostPointerCapture={e=>{const g=drag.current;drag.current=null;if(g)(e.currentTarget as HTMLElement).style.top=`${52-clip.gain*g.value*16}px`;}} onDoubleClick={e => { e.stopPropagation(); if (!disabled)
        onCommit({ envelope: clip.envelope!.filter((_, index) => index !== i) }); }}/>)}
    <span className="tl-fade-handle in" style={{ left: (clip.fade_in ?? 0) * scale }} role="slider" aria-label="Kéo fade vào" aria-valuenow={clip.fade_in ?? 0} onPointerDown={e => begin(e, 'in')} {...props}/>
    <span className="tl-fade-handle out" style={{ right: (clip.fade_out ?? 0) * scale }} role="slider" aria-label="Kéo fade ra" aria-valuenow={clip.fade_out ?? 0} onPointerDown={e => begin(e, 'out')} {...props}/>
  </>;
}
