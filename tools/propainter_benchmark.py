"""Bounded, isolated ProPainter benchmark; no changes to production video files.

Run with .venvs/propainter/bin/python tools/propainter_benchmark.py.
The upstream source and model weights must already be present locally.
"""
import argparse
import json
import os
from pathlib import Path
import runpy
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / '.flowkit' / 'propainter' / 'benchmark'


def ffmpeg(*args):
    subprocess.run(['ffmpeg', '-v', 'error', '-y', *map(str, args)], check=True, capture_output=True, timeout=60)


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def prepare():
    from PIL import Image, ImageDraw
    WORK.mkdir(parents=True, exist_ok=True)
    width, height = 320, 192
    image = Image.new('RGB', (width, height))
    pixels = image.load()
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (int(80 + 50 * y / height), int(150 + 25 * y / height), int(210 - 90 * y / height))
    draw = ImageDraw.Draw(image)
    draw.polygon([(0, 130), (90, 55), (165, 130)], fill=(55, 110, 95))
    draw.polygon([(85, 130), (220, 45), (320, 130)], fill=(80, 130, 115))
    draw.rectangle((0, 130, width, height), fill=(110, 155, 95))
    image.save(WORK / 'clean.png')
    draw.rectangle((245, 147, 291, 168), fill=(215, 35, 30))
    draw.text((248, 151), 'TEST', fill='white')
    image.save(WORK / 'marked.png')
    mask = Image.new('L', (width, height))
    ImageDraw.Draw(mask).rectangle((243, 145, 293, 170), fill=255)
    mask.save(WORK / 'mask.png')
    ffmpeg('-loop', '1', '-framerate', '12', '-i', WORK / 'marked.png', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2',
           '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', WORK / 'sample.mp4')
    # Upstream's deprecated torchvision video reader is incompatible with newer
    # PyAV. Decode with FFmpeg and use its supported frame-folder input instead.
    frames = WORK / 'sample'
    frames.mkdir(exist_ok=True)
    ffmpeg('-i', WORK / 'sample.mp4', frames / '%05d.png')
    weights = WORK / 'weights'
    if not weights.exists():
        weights.symlink_to(ROOT / '.flowkit' / 'propainter' / 'weights', target_is_directory=True)


def worker():
    import torch
    sys.path.insert(0, str(ROOT / 'ProPainter-main'))
    os.chdir(WORK)
    sys.argv = ['inference_propainter.py', '-i', str(WORK / 'sample'), '-m', str(WORK / 'mask.png'),
                '-o', str(WORK / 'output'), '--width', '320', '--height', '192', '--neighbor_length', '6',
                '--subvideo_length', '24', '--ref_stride', '12', '--raft_iter', '10', '--save_fps', '12']
    started = time.monotonic()
    runpy.run_path(str(ROOT / 'ProPainter-main' / 'inference_propainter.py'), run_name='__main__')
    if torch.backends.mps.is_available():
        torch.mps.synchronize()
    (WORK / 'worker.json').write_text(json.dumps({'inference_seconds': round(time.monotonic() - started, 2),
        'device': 'mps' if torch.backends.mps.is_available() else 'cpu',
        'mps_driver_bytes_at_finish': torch.mps.driver_allocated_memory() if torch.backends.mps.is_available() else 0}))


def benchmark(timeout):
    import psutil
    import numpy as np
    from PIL import Image
    prepare()
    (WORK / 'worker.json').unlink(missing_ok=True)
    started = time.monotonic()
    peak = 0
    result = {'status': 'FAILED', 'duration_requested': 2, 'resolution': [320, 192], 'fps': 12,
              'scope': 'Synthetic static background with an owned test mark; real-scene quality is unverified.'}
    with (WORK / 'inference.log').open('w') as log:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--worker'], stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while proc.poll() is None:
                try:
                    process = psutil.Process(proc.pid)
                    peak = max(peak, sum(p.memory_info().rss for p in [process, *process.children(recursive=True)] if p.is_running()))
                except psutil.Error:
                    pass
                if time.monotonic() - started > timeout:
                    os.killpg(proc.pid, signal.SIGKILL)
                    result['error'] = f'Exceeded {timeout}s time limit'
                    break
                time.sleep(.25)
            code = proc.wait()
            if code and 'error' not in result:
                result['error'] = (WORK / 'inference.log').read_text()[-3500:]
            if not code:
                result.update(json.loads((WORK / 'worker.json').read_text()))
                raw = WORK / 'output' / 'sample' / 'inpaint_out.mp4'
                final = WORK / 'result-with-audio.mp4'
                ffmpeg('-i', raw, '-i', WORK / 'sample.mp4', '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-t', '2', final)
                info = probe(final)
                streams = info['streams']
                result.update(status='COMPLETED', output=str(final), duration=float(info['format']['duration']),
                              has_audio=any(s['codec_type'] == 'audio' for s in streams),
                              output_fps=next(s['avg_frame_rate'] for s in streams if s['codec_type'] == 'video'))
                ffmpeg('-ss', '1', '-i', final, '-frames:v', '1', WORK / 'after.png')
                ffmpeg('-ss', '1', '-i', WORK / 'sample.mp4', '-frames:v', '1', WORK / 'before.png')
                clean = np.asarray(Image.open(WORK / 'clean.png').convert('RGB')).astype(float)
                before = np.asarray(Image.open(WORK / 'before.png').convert('RGB')).astype(float)
                after = np.asarray(Image.open(WORK / 'after.png').convert('RGB')).astype(float)
                region = np.zeros(clean.shape[:2], bool)
                region[145:171, 243:294] = True
                result.update(mask_mae_before=round(float(np.abs(before[region] - clean[region]).mean()), 3),
                              mask_mae_after=round(float(np.abs(after[region] - clean[region]).mean()), 3),
                              outside_change_mae=round(float(np.abs(after[~region] - before[~region]).mean()), 3))
        except Exception as exc:
            result.update(status='FAILED', error=str(exc))
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
            result.update(wall_seconds=round(time.monotonic() - started, 2), peak_rss_bytes=peak)
            (WORK.parent / 'benchmark.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    worker() if args.worker else benchmark(args.timeout)
