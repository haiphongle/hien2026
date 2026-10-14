"""Restore selected source ranges; keep timeline decisions and full-frame detail."""
import hashlib
import json
import math
from pathlib import Path
import tempfile
import time
import uuid

from PIL import Image, ImageDraw, ImageFilter

from agent.services import editing, media_jobs, pacing, propainter_trial as trial
from agent.services.speech_store import atomic_json

MAX_CLIP_SECONDS = 30
WINDOW_FRAMES, OVERLAP = 60, 6


def windows(frames):
    """Disjoint cores with context on both sides; never exceed 60 frames."""
    if frames <= WINDOW_FRAMES:
        return [(0, frames)]
    core = WINDOW_FRAMES - 2 * OVERLAP
    sections = []
    for start in range(0, frames, core):
        end = min(frames, start + core + OVERLAP)
        sections.append((max(0, start - OVERLAP), end))
        if end == frames:
            break
    return sections


def geometry(region, info, max_side, dilation):
    w, h = info['width'], info['height']
    x, y, rw, rh = editing.region_pixels(region, w, h)
    pad = max(32, math.ceil(max(rw, rh) * .75))
    left, top = max(0, x - pad) // 2 * 2, max(0, y - pad) // 2 * 2
    right, bottom = min(w, math.ceil((x + rw + pad) / 2) * 2), min(h, math.ceil((y + rh + pad) / 2) * 2)
    cw, ch = right - left, bottom - top
    scale = min(1, max_side / max(cw, ch))
    mw, mh = max(16, int(cw * scale) // 16 * 16), max(16, int(ch * scale) // 16 * 16)
    mask = Image.new('L', (mw, mh))
    ImageDraw.Draw(mask).rectangle((math.floor((x-left)*mw/cw), math.floor((y-top)*mh/ch),
        math.ceil((x+rw-left)*mw/cw)-1, math.ceil((y+rh-top)*mh/ch)-1), fill=255)
    # Only the repaired area is composited back. Everything else retains source detail.
    margin = math.ceil(dilation * max(cw/mw, ch/mh)) + 2
    alpha = Image.new('L', (cw, ch))
    ImageDraw.Draw(alpha).rectangle((max(0, x-left-margin), max(0, y-top-margin),
        min(cw-1, x+rw-left+margin), min(ch-1, y+rh-top+margin)), fill=255)
    return (left, top, cw, ch, mw, mh), mask, alpha.filter(ImageFilter.GaussianBlur(1))


def cache_key(source, start, duration, region, max_side, dilation):
    values = ['roi-v1', str(source.resolve()), pacing.fingerprint(source), start, duration,
              region.x, region.y, region.width, region.height, max_side, dilation]
    return hashlib.sha256(json.dumps(values, separators=(',', ':')).encode()).hexdigest()


async def run_clip(vid, source, start, duration, region, dilation, max_side, targets, update):
    info = await trial.source_info(source)
    if not 0 < duration <= MAX_CLIP_SECONDS or start + duration > info['duration'] + .001:
        raise ValueError('Mỗi cảnh xử lý tối đa 30 giây nguồn. Tách cảnh dài trên timeline trước.')
    if region.width * region.height > .35:
        raise ValueError('Khoanh vùng nhỏ hơn 35% khung hình để kiểm soát bộ nhớ.')
    fingerprint = pacing.fingerprint(source)
    key = cache_key(source, start, duration, region, max_side, dilation)
    rid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'flowkit:propainter:{vid}:{key}'))
    folder = editing.directory(vid) / 'propainter'
    folder.mkdir(parents=True, exist_ok=True)
    target, record = folder / f'{rid}.mp4', folder / f'{rid}.json'
    cached = next((r for r in trial.results(vid) if r['trial_id'] == rid), None)
    if cached:
        bindings = {t['clip_id']: t for t in cached.get('targets', [])}
        bindings.update({t['clip_id']: t for t in targets})
        cached['targets'] = list(bindings.values())
        atomic_json(record, cached)
        update(stage='Dùng lại cảnh đã xử lý', progress=100, result=cached)
        return cached
    checkpoints = trial.CACHE / 'parts' / key
    checkpoints.mkdir(parents=True, exist_ok=True)
    frames = math.ceil(duration * info['fps_number'] - 1e-6)
    sections = windows(frames)
    (x, y, cw, ch, mw, mh), mask, alpha = geometry(region, info, max_side, dilation)
    began, inference, peak_rss, peak_mps, device = time.monotonic(), 0, 0, 0, 'cpu'
    async with trial.LOCK:
        checkpoints.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='restore-', dir=trial.CACHE) as tmp:
            work = Path(tmp)
            parts = []
            for index, (first, last) in enumerate(sections):
                part = checkpoints / f'{index}.mp4'
                if part.is_file():
                    try:
                        checked = await trial.source_info(part)
                        if (abs(checked['duration'] - (last-first)/info['fps_number']) > .06
                                or checked['fps'] != info['fps'] or (checked['width'], checked['height']) != (mw, mh)):
                            raise ValueError('Invalid checkpoint')
                    except (ValueError, RuntimeError, OSError):
                        part.unlink(missing_ok=True)
                if not part.is_file():
                    with tempfile.TemporaryDirectory(prefix='window-', dir=work) as window:
                        window = Path(window)
                        (window / 'frames').mkdir()
                        (window / 'weights').symlink_to(trial.CACHE / 'weights', target_is_directory=True)
                        mask.save(window / 'mask.png')
                        update(stage=f'Đọc phần {index+1}/{len(sections)}', progress=round(85*index/len(sections)))
                        await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-ss', str(start + first/info['fps_number']),
                            '-i', str(source), '-an', '-vf', f'crop={cw}:{ch}:{x}:{y},scale={mw}:{mh},fps={info["fps"]}',
                            '-frames:v', str(last-first), str(window / 'frames' / '%05d.png')], timeout=120)
                        if len(list((window / 'frames').glob('*.png'))) != last-first:
                            raise ValueError('Nguồn không đủ khung hình cho đoạn đã chọn; nạp lại tư liệu.')
                        def phase(**change):
                            update(stage=f'Phần {index+1}/{len(sections)} · {change.get("stage", "AI đang xử lý")}',
                                   progress=round(85*(index + change.get('progress', 0)/100)/len(sections)))
                        peak_rss = max(peak_rss, await trial.inpaint(window, mw, mh, dilation, phase))
                        metrics = json.loads((window / 'metrics.json').read_text())
                        inference += metrics['inference_seconds']
                        peak_mps, device = max(peak_mps, metrics['peak_mps_driver_bytes']), metrics['device']
                        raw = window / 'output' / 'frames' / 'inpaint_out.mp4'
                        prepared = window / 'ready.mp4'
                        await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-i', str(raw), '-an',
                            '-vf', f'setpts=N/({info["fps_number"]}*TB)', '-r', info['fps'], '-frames:v', str(last-first),
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(prepared)], timeout=60)
                        prepared.replace(part)
                parts.append(part)
            # Blend overlapping context without changing the source range's duration.
            update(stage='Nối các phần đã xóa', progress=88)
            merged = parts[0]
            for index in range(1, len(parts)):
                first, last = sections[index]
                previous_end = sections[index-1][1]
                overlap = (previous_end-first)/info['fps_number']
                output = work / f'joined-{index}.mp4'
                graph = (f'[0:v]settb=AVTB,setpts=PTS-STARTPTS[a];[1:v]settb=AVTB,setpts=PTS-STARTPTS[b];'
                         f'[a][b]xfade=transition=fade:duration={overlap}:offset={first/info["fps_number"]}[out]')
                await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-i', str(merged), '-i', str(parts[index]),
                    '-filter_complex_threads', '1', '-filter_complex', graph, '-map', '[out]', '-an', '-r', info['fps'],
                    '-frames:v', str(last), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(output)], timeout=120)
                if merged.parent == work:
                    merged.unlink(missing_ok=True)
                merged = output
            alpha.save(work / 'alpha.png')
            candidate, before = work / 'result.mp4', folder / f'{rid}-before.mp4'
            update(stage='Ghép vùng đã xóa vào hình gốc, giữ tiếng và thời lượng', progress=94)
            graph = (f'[0:v]setpts=PTS-STARTPTS[base];[1:v]scale={cw}:{ch},format=rgb24[patch];'
                     f'[patch][2:v]alphamerge[repair];[base][repair]overlay={x}:{y}:eof_action=pass,format=yuv420p[out]')
            await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-ss', str(start), '-i', str(source), '-i', str(merged),
                '-loop', '1', '-i', str(work / 'alpha.png'), '-filter_complex_threads', '1', '-filter_complex', graph,
                '-map', '[out]', '-map', '0:a:0?', '-t', str(duration), '-r', info['fps'], '-c:v', 'libx264', '-crf', '18',
                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(candidate)], timeout=180)
            actual = await trial.source_info(candidate)
            if (abs(actual['duration']-duration) > max(.06, 1/info['fps_number']) or actual['fps'] != info['fps']
                    or actual['has_audio'] != info['has_audio'] or (actual['width'], actual['height']) != (info['width'], info['height'])):
                raise ValueError('Kết quả chưa giữ đúng thời lượng, FPS, kích thước hoặc âm thanh; chưa áp dụng.')
            if pacing.fingerprint(source) != fingerprint:
                raise ValueError('Nguồn đã thay đổi trong khi xử lý; chưa áp dụng kết quả.')
            try:
                await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-ss', str(start), '-i', str(source), '-t', str(duration),
                    '-vf', 'scale=640:640:force_original_aspect_ratio=decrease', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', str(before)], timeout=120)
                result = dict(trial_id=rid, created_at=time.time(), start=start, requested_duration=duration, **actual,
                    inference_seconds=round(inference, 2), device=device, peak_rss_bytes=peak_rss, peak_mps_driver_bytes=peak_mps,
                    wall_seconds=round(time.monotonic()-began, 2), full_resolution=True, source_fingerprint=fingerprint,
                    targets=targets, region=region.model_dump(mode='json'), max_side=max_side, dilation=dilation,
                    url=f'/api/videos/{vid}/editing/propainter/results/{rid}',
                    before_url=f'/api/videos/{vid}/editing/propainter/results/{rid}/before')
                candidate.replace(target)
                atomic_json(record, result)
            except BaseException:
                target.unlink(missing_ok=True)
                record.unlink(missing_ok=True)
                before.unlink(missing_ok=True)
                raise
    # Successful windows are no longer needed; failed/cancelled runs keep checkpoints.
    import shutil
    shutil.rmtree(checkpoints, ignore_errors=True)
    update(progress=100, result=result)
    return result
