import type { Clip, Document, Track, Visuals } from './types.ts';
export function envelopeGain(clip: Clip, local: number) {
    const points = clip.envelope ?? [];
    let gain = points.length ? local <= points[0].time ? points[0].value : points.at(-1)!.value : 1;
    for (let i = 0; i < points.length - 1; i++) {
        const a = points[i], b = points[i + 1];
        if (local >= a.time && local <= b.time) {
            gain = a.value + (b.value - a.value) * (local - a.time) / (b.time - a.time);
            break;
        }
    }
    if (clip.fade_in)
        gain *= Math.max(0, Math.min(1, local / clip.fade_in));
    if (clip.fade_out)
        gain *= Math.max(0, Math.min(1, (clip.duration - local) / clip.fade_out));
    return gain;
}
export const audible = (doc: Document, track: Track) => !track.muted && (!doc.tracks.some(t => t.kind === 'audio' && t.solo) || !!track.solo);
export function voiceIntervals(doc: Document, visuals: Record<string, Visuals> = {}) {
    const intervals = doc.clips.filter(c => {
        const t = doc.tracks.find(t => t.id === c.track_id);
        return t?.kind === 'audio' && t.role === 'voice' && audible(doc, t) && !c.muted && c.gain > 0;
    }).flatMap(c => {
        const summary = doc.ducking?.detect_pauses ? visuals[c.asset_id ?? ''] : undefined;
        const ranges: number[][] = [], speed = c.speed ?? 1, end = c.source_in+c.duration*speed;
        let cursor = c.source_in;
        for (const quiet of summary?.silences ?? []) {
            const a = Math.max(cursor,quiet.start), b = Math.min(end,quiet.end);
            if (b<=a) continue;
            if (a>cursor) ranges.push([c.start+(cursor-c.source_in)/speed,c.start+(a-c.source_in)/speed]);
            cursor = b;
        }
        if (cursor<end) ranges.push([c.start+(cursor-c.source_in)/speed,c.start+c.duration]);
        return ranges;
    }).sort((a, b) => a[0] - b[0]);
    const result: number[][] = [], duck = doc.ducking ?? { attack: .2, release: .35 };
    for (const range of intervals) {
        const last = result.at(-1);
        if (last && range[0] <= last[1] + duck.attack + duck.release)
            last[1] = Math.max(last[1], range[1]);
        else
            result.push([...range]);
    }
    return result;
}
export function clipGain(doc: Document, clip: Clip, time: number, intervals = voiceIntervals(doc)) {
    let gain = (clip.muted ? 0 : clip.gain) * envelopeGain(clip, time - clip.start);
    const duck = doc.ducking, track = doc.tracks.find(t => t.id === clip.track_id);
    const role = track?.role ?? 'original', amount = role === 'music' ? duck?.amount ?? 1 : duck?.original_amount ?? 1;
    if (duck?.enabled && ['music','original'].includes(role) && amount < 1) {
        let multiplier = 1;
        for (const [a, b] of intervals) {
            const value = time < b ? 1 - (1 - amount) * Math.min(1, Math.max(0, (time - a + duck.attack) / duck.attack)) : amount + (1 - amount) * Math.min(1, Math.max(0, (time - b) / duck.release));
            multiplier = Math.min(multiplier, value);
        }
        gain *= multiplier;
    }
    return gain;
}
export function silences(visuals: Visuals | undefined, threshold = -40, minimum = .35) {
    if (!visuals?.peaks.length)
        return [];
    const limit = 10 ** (threshold / 20), ranges: {
        start: number;
        end: number;
    }[] = [];
    let begin: number | null = null;
    for (let i = 0; i <= visuals.peaks.length; i++) {
        if (i < visuals.peaks.length && visuals.peaks[i] < limit) {
            begin ??= i;
        }
        else if (begin !== null) {
            if ((i - begin) * visuals.interval >= minimum)
                ranges.push({ start: begin * visuals.interval, end: Math.min(visuals.duration, i * visuals.interval) });
            begin = null;
        }
    }
    return ranges;
}
export function clipSilences(clip: Clip, visuals: Visuals | undefined, threshold = -40, minimum = .35) {
    const speed = clip.speed ?? 1;
    return silences(visuals, threshold, minimum * speed).flatMap(range => {
        const a = Math.max(range.start, clip.source_in), b = Math.min(range.end, clip.source_in + clip.duration * speed);
        // The minimum applies to the retained edit, not the longer source pause.
        return b > a && (b - a) / speed >= minimum - 1e-6 ? [{ start: clip.start + (a - clip.source_in) / speed, end: clip.start + (b - clip.source_in) / speed }] : [];
    });
}
