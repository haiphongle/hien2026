import { useEffect, useRef, useState } from 'react';
import { API_BASE_URL, resolveMediaUrl } from '../../../../api/client';
import type { Caption, Clip } from './types';
import { moveText, resizeText, type TextBounds } from './textTitles';

type Transform = { x: number; y: number; scale: number };
const STILL: Transform = { x: 0, y: 0, scale: 1 };
type TextGesture = { x: number; y: number; w: number; h: number; mode: 'move' | 'resize'; bounds: TextBounds; caption: Caption };

export function CaptionOverlay({ clip, videoId, width, height, visible = true, selected, disabled = false, onSelect, onChange }: {
    clip: Clip; videoId: string; width: number; height: number; visible?: boolean;
    selected: boolean; disabled?: boolean; onSelect: () => void; onChange: (caption: Caption) => void;
}) {
    const [rendered, setRendered] = useState<{ key: string; url: string; bounds: TextBounds | null } | null>(null);
    const [draft, setDraft] = useState<Transform | null>(null);
    const [pending, setPending] = useState<{ key: string; transform: Transform } | null>(null);
    const [failure, setFailure] = useState<{ key: string; message: string } | null>(null);
    const [retry, setRetry] = useState(0);
    const imageUrl = useRef('');
    const dragging = useRef<TextGesture | null>(null);
    const key = JSON.stringify({ text: clip.text, caption: clip.caption, width, height });
    useEffect(() => {
        const controller = new AbortController();
        const timer = setTimeout(() => {
            void (async () => {
                const response = await fetch(`${API_BASE_URL}/api/videos/${videoId}/timeline/caption-preview`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: key, signal: controller.signal,
                });
                if (!response.ok) throw new Error(`Không vẽ được chữ (API ${response.status}).`);
                const bounds: TextBounds | null = JSON.parse(response.headers.get('X-Text-Bounds') || 'null');
                const blob = await response.blob();
                if (controller.signal.aborted) return;
                const url = URL.createObjectURL(blob);
                if (imageUrl.current) URL.revokeObjectURL(imageUrl.current);
                imageUrl.current = url;
                setRendered({ key, url, bounds }); setFailure(null);
            })().catch(cause => {
                if (!controller.signal.aborted) setFailure({ key, message: cause instanceof Error ? cause.message : 'Không vẽ được chữ.' });
            });
        }, 100);
        return () => { clearTimeout(timer); controller.abort(); };
    }, [key, videoId, retry]);
    useEffect(() => () => { if (imageUrl.current) URL.revokeObjectURL(imageUrl.current); }, []);
    const c = clip.caption;
    if (!c) return null;
    const bounds = rendered?.bounds;
    const transform = draft ?? (pending?.key === key && rendered?.key !== key ? pending.transform : STILL);
    const editable = !disabled && rendered?.key === key && !!bounds;
    function begin(e: React.PointerEvent<HTMLButtonElement>, mode: TextGesture['mode']) {
        e.stopPropagation(); e.preventDefault(); onSelect();
        if (!editable || !bounds || e.button !== 0) return;
        const box = e.currentTarget.closest('.tl-caption-overlay')!.getBoundingClientRect();
        if (!box.width || !box.height) return;
        dragging.current = { x: e.clientX, y: e.clientY, w: box.width, h: box.height, mode, bounds, caption: c! };
        e.currentTarget.setPointerCapture(e.pointerId);
    }
    function calculate(e: React.PointerEvent, g: TextGesture) {
        const dx = e.clientX - g.x, dy = e.clientY - g.y;
        const centre = moveText(g.bounds, 0, 0);
        const moved = moveText(g.bounds, dx / g.w, dy / g.h);
        const size = resizeText(g.caption.size, g.bounds.width * g.w, g.bounds.height * g.h, dx, dy, 30);
        return g.mode === 'move'
            ? { caption: { ...g.caption, ...moved }, transform: { x: (moved.x - centre.x) * g.w, y: (moved.y - centre.y) * g.h, scale: 1 } }
            : { caption: { ...g.caption, ...centre, size }, transform: { x: 0, y: 0, scale: size / g.caption.size } };
    }
    function move(e: React.PointerEvent<HTMLButtonElement>) {
        if (dragging.current) setDraft(calculate(e, dragging.current).transform);
    }
    function finish(e: React.PointerEvent<HTMLButtonElement>, cancel = false) {
        const g = dragging.current;
        if (!g) return;
        dragging.current = null; setDraft(null);
        if (e.currentTarget.hasPointerCapture(e.pointerId)) e.currentTarget.releasePointerCapture(e.pointerId);
        if (cancel || Math.hypot(e.clientX - g.x, e.clientY - g.y) < 2) return;
        const result = calculate(e, g);
        const nextKey = JSON.stringify({ text: clip.text, caption: result.caption, width, height });
        setPending({ key: nextKey, transform: result.transform });
        onChange(result.caption);
    }
    return <div hidden={!visible} className={`tl-caption-overlay ${selected ? 'selected' : ''}`}>
        {rendered && <img src={rendered.url} alt={clip.text} draggable={false} style={{
            transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
            transformOrigin: bounds ? `${(bounds.x + bounds.width / 2) * 100}% ${(bounds.y + bounds.height / 2) * 100}%` : 'center',
        }} />}
        {bounds && <div className="tl-text-hitbox" style={{ left: `${bounds.x * 100}%`, top: `${bounds.y * 100}%`, width: `${bounds.width * 100}%`, height: `${bounds.height * 100}%`, transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})` }}>
            <button className="tl-text-move" aria-label={`Chọn chữ: ${clip.text}`} title={disabled ? 'Lớp chữ đang khóa' : 'Kéo để đặt vị trí chữ'}
                onClick={onSelect} onPointerDown={e => begin(e, 'move')} onPointerMove={move} onPointerUp={e => finish(e)} onPointerCancel={e => finish(e, true)} onLostPointerCapture={e => finish(e, true)} />
            {selected && !disabled && <button className="tl-text-resize" aria-label="Kéo góc để đổi cỡ chữ" title="Kéo góc để đổi cỡ chữ"
                onPointerDown={e => begin(e, 'resize')} onPointerMove={move} onPointerUp={e => finish(e)} onPointerCancel={e => finish(e, true)} onLostPointerCapture={e => finish(e, true)} />}
        </div>}
        {failure?.key === key && <button className="tl-text-retry" onClick={() => setRetry(n => n + 1)}>{failure.message} Thử lại</button>}
    </div>;
}
export function EffectOverlay({ clip, videoId, selected, onSelect, onMove }: {
    clip: Clip;
    videoId: string;
    selected: boolean;
    onSelect: () => void;
    onMove: (x: number, y: number) => void;
}) {
    const r = clip.effect, dragging = useRef<{
        x: number;
        y: number;
        w: number;
        h: number;
    } | null>(null), [offset, setOffset] = useState({ x: 0, y: 0 });
    if (!r)
        return null;
    return <div className={`tl-effect-overlay ${selected ? 'selected' : ''}`} style={{ left: `${r.x * 100}%`, top: `${r.y * 100}%`, width: `${r.width * 100}%`, height: `${r.height * 100}%`, transform: `translate(${offset.x}px,${offset.y}px)`, background: r.kind === 'color' ? r.color : undefined, opacity: r.kind === 'color' || r.kind === 'image' ? r.opacity : 1, backdropFilter: r.kind === 'blur' || r.kind === 'delogo' ? `blur(${Math.max(1, r.strength / 2)}px)` : undefined }} role="button" tabIndex={0} aria-label="Chọn vùng hiệu ứng" onClick={onSelect} onKeyDown={e => { if (e.key === 'Enter')
        onSelect(); }} onPointerDown={e => { e.stopPropagation(); onSelect(); const box = e.currentTarget.parentElement!.getBoundingClientRect(); dragging.current = { x: e.clientX, y: e.clientY, w: box.width, h: box.height }; e.currentTarget.setPointerCapture(e.pointerId); }} onPointerMove={e => { const g = dragging.current; if (g)
        setOffset({ x: e.clientX - g.x, y: e.clientY - g.y }); }} onPointerUp={e => { const g = dragging.current; if (!g)
        return; dragging.current = null; setOffset({ x: 0, y: 0 }); onMove(Math.max(0, Math.min(1 - r.width, r.x + (e.clientX - g.x) / g.w)), Math.max(0, Math.min(1 - r.height, r.y + (e.clientY - g.y) / g.h))); e.currentTarget.releasePointerCapture(e.pointerId); }} onPointerCancel={() => { dragging.current = null; setOffset({ x: 0, y: 0 }); }}>
    {r.kind === 'image' && r.image_id && <img src={resolveMediaUrl(`/api/videos/${videoId}/editing/assets/${r.image_id}`)} alt="Logo" draggable={false}/>}
    {r.kind === 'delogo' && <small>Delogo · xem gần đúng</small>}
  </div>;
}
