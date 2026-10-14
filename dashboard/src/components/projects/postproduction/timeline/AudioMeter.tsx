import { useEffect, useRef, useState } from 'react';
export function AudioMeter({ analyser, playing }: {
    analyser: AnalyserNode | null;
    playing: boolean;
}) {
    const canvas = useRef<HTMLCanvasElement>(null), [db, setDb] = useState(-60), [spectrum, setSpectrum] = useState(false);
    useEffect(() => {
        if (!analyser)
            return;
        const samples = new Float32Array(analyser.fftSize), frequencies = new Uint8Array(analyser.frequencyBinCount);
        let raf = 0, last = 0;
        const draw = (now: number) => {
            if (now - last > 80) {
                last = now;
                analyser.getFloatTimeDomainData(samples);
                const peak = samples.reduce((p, n) => Math.max(p, Math.abs(n)), 0);
                setDb(20 * Math.log10(Math.max(peak, 1e-6)));
                const ctx = canvas.current?.getContext('2d');
                if (ctx && spectrum) {
                    analyser.getByteFrequencyData(frequencies);
                    ctx.clearRect(0, 0, 260, 55);
                    for (let i = 0; i < 52; i++) {
                        const index = Math.floor((Math.exp(i / 51 * Math.log(frequencies.length)) - 1));
                        const height = frequencies[index] / 255 * 50;
                        ctx.fillStyle = i > 40 ? '#8fb1fc' : '#7de0bf';
                        ctx.fillRect(i * 5, 55 - height, 3, height);
                    }
                }
            }
            if (playing)
                raf = requestAnimationFrame(draw);
        };
        raf = requestAnimationFrame(draw);
        return () => cancelAnimationFrame(raf);
    }, [analyser, playing, spectrum]);
    return <div className="tl-meter"><span>Mức âm</span><meter min={-60} max={0} high={-1} value={playing ? Math.min(0, db) : -60}/><b className={db >= -.5 && playing ? 'hot' : ''}>{playing ? Math.max(-60, db).toFixed(1) : '−∞'} dB</b>{db >= -.5 && playing && <span className="hot">Âm quá lớn</span>}<button aria-pressed={spectrum} onClick={() => setSpectrum(!spectrum)}>Phân tích phổ tần</button>{spectrum && <div className="tl-spectrum"><canvas ref={canvas} width={260} height={55}/><small>0 Hz → {((analyser?.context.sampleRate ?? 48000)/2000).toFixed(1)} kHz · phổ của bản trộn đang phát</small></div>}</div>;
}
