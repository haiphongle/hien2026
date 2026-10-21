"""First-pass editing with bounded memory; audio and picture share every cut.

Ideas: auto-editor's threshold/margin, Shotcut's ripple editing, ShortGPT's
timed audio events. No code or dependency is copied from those applications.
"""
from agent.media_limits import MAX_DURATION
import asyncio
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import tempfile
import threading
import time
import uuid
import wave

from agent.config import SHARED_OUTPUT_DIR
from agent.models.pacing import PacingDraft
from agent.services import editing, media_jobs
from agent.services.speech_store import atomic_json, identifier, read_json

ROOT = SHARED_OUTPUT_DIR / 'pacing'
BUILTINS = [('boing', 'Boing · hài hước', .7), ('surprise', 'Ngỡ ngàng · chime', .8),
            ('whoosh', 'Vút · chuyển cảnh', .55), ('impact', 'Bụp · nhấn mạnh', .35)]
_library_lock = threading.Lock()


def directory(vid):
    return ROOT / identifier(vid)


def load(vid):
    data = read_json(directory(vid) / 'draft.json', {'revision': 0, 'draft': PacingDraft().model_dump(mode='json')})
    data['draft'] = PacingDraft.model_validate(data['draft']).model_dump(mode='json')
    return data


def plan(vid, plan_id):
    return read_json(directory(vid) / 'plans' / f'{identifier(plan_id)}.json') if plan_id else None


def fingerprint(path):
    info = path.stat()
    return hashlib.sha256(f'{path.absolute()}:{info.st_size}:{info.st_mtime_ns}'.encode()).hexdigest()


def candidates(intervals, duration, minimum, padding):
    """Keep a small breathing margin on both sides of every proposed cut."""
    cuts = []
    for start, end in intervals:
        start, end = max(0, start), min(duration, end)
        if end - start < minimum:
            continue
        a = start + (padding if start > 0 else 0)
        b = end - (padding if end < duration else 0)
        if b - a >= .08:
            cuts.append({'id': str(uuid.uuid4()), 'start': round(a, 6), 'end': round(b, 6)})
    if len(cuts) > 300:
        raise ValueError('Có hơn 300 khoảng lặng. Tăng độ dài khoảng nghỉ tối thiểu rồi phân tích lại.')
    return cuts


def silence_reader(duration):
    intervals, pending = [], None

    def line(text):
        nonlocal pending
        match = re.search(r'silence_start: ([\d.e+\-]+)', text)
        if match:
            pending = float(match[1])
        match = re.search(r'silence_end: ([\d.e+\-]+)', text)
        if match and pending is not None:
            intervals.append((pending, float(match[1])))
            pending = None

    def finish():
        return [*intervals, *(([(pending, duration)]) if pending is not None else [])]
    return line, finish


def speech_gaps(cues, duration):
    cursor, gaps = 0., []
    for cue in sorted(cues, key=lambda c: c.start):
        if cue.start > cursor:
            gaps.append((cursor, cue.start))
        cursor = max(cursor, cue.end)
    if cursor < duration:
        gaps.append((cursor, duration))
    return gaps


def keep_ranges(duration, cuts, fps):
    """Quantize to whole frames, then use the same bounds for audio and video."""
    total = max(1, round(duration * fps))
    intervals = sorted((max(0, round(c['start'] * fps)), min(total, round(c['end'] * fps))) for c in cuts)
    cursor, ranges, output = 0, [], 0.
    for start, end in [*intervals, (total, total)]:
        if start > cursor:
            length = (start - cursor) / fps
            ranges.append({'start': cursor / fps, 'end': start / fps, 'output_start': output, 'output_end': output + length})
            output += length
        cursor = max(cursor, end)
    if output < .2:
        raise ValueError('Lựa chọn sẽ xóa gần hết video. Bỏ chọn một số khoảng hoặc giảm độ nhạy.')
    return ranges


def selected_plan(vid, draft, path):
    data = plan(vid, draft.plan_id)
    if draft.cut_ids and not data:
        raise ValueError('Phân tích khoảng lặng trước khi chọn đoạn cắt.')
    if data and (data['source'] != draft.source or data['fingerprint'] != fingerprint(path)):
        raise ValueError('Video nguồn đã thay đổi. Phân tích lại khoảng lặng trước khi xuất.')
    if data and data.get('speech_revision') is not None:
        from agent.services.speech_store import load_document
        if load_document(vid).revision != data['speech_revision']:
            raise ValueError('Lời thoại đã đổi. Phân tích lại khoảng trống giữa lời thoại.')
    ids = {str(i) for i in draft.cut_ids}
    cuts = [c for c in (data or {}).get('cuts', []) if c['id'] in ids]
    if len(cuts) != len(ids) or len(ids) != len(draft.cut_ids):
        raise ValueError('Đoạn cắt không nằm trong bản phân tích đã chọn hoặc bị lặp.')
    if len({e.id for e in draft.effects}) != len(draft.effects):
        raise ValueError('ID hiệu ứng âm thanh bị lặp.')
    return data, cuts


def asset_path(vid, asset_id):
    return directory(vid) / 'assets' / f'{identifier(asset_id)}.wav'


def asset_info(vid, asset_id):
    data = read_json(directory(vid) / 'assets' / f'{identifier(asset_id)}.json')
    if not data or not asset_path(vid, asset_id).is_file():
        raise ValueError('Hiệu ứng không còn trong thư viện. Tải lại âm thanh.')
    return data


def library(vid):
    # Timeline and the pacing panel both request this catalog on first open.
    # Serialize creation so neither sees half-published files/fingerprints.
    with _library_lock:
        return _library(vid)


def _library(vid):
    assets = directory(vid) / 'assets'
    assets.mkdir(parents=True, exist_ok=True)
    for kind, name, duration in BUILTINS:
        asset_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'flowkit:sfx:{kind}:v1'))
        path = asset_path(vid, asset_id)
        if path.is_file() and path.with_suffix('.json').is_file():
            continue
        # Own synthesized demos: no stock recording or external license required.
        frames = bytearray()
        rate = 44100
        for i in range(round(duration * rate)):
            t = i / rate
            envelope = min(1, t / .015) * (1 - t / duration) ** 2
            if kind == 'boing':
                phase = 2 * math.pi * (130 * t + 90 * duration / (2 * math.pi) * math.sin(2 * math.pi * t / duration))
                value = math.sin(phase)
            elif kind == 'surprise':
                value = (math.sin(2 * math.pi * 880 * t) + .5 * math.sin(2 * math.pi * 1320 * t)) / 1.5
            elif kind == 'whoosh':
                value = math.sin(2 * math.pi * (350 * t + 1200 * t * t)) * math.sin(2 * math.pi * 53 * t)
            else:
                value = math.sin(2 * math.pi * (60 * t + 100 * duration * (1 - math.exp(-t / duration))))
            frames.extend(struct.pack('<h', round(value * envelope * 15000)))
        staged = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
        try:
            with wave.open(str(staged), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(rate)
                output.writeframes(frames)
            staged.replace(path)
            atomic_json(path.with_suffix('.json'), {'id': asset_id, 'name': name, 'duration': duration, 'builtin': True})
        finally:
            staged.unlink(missing_ok=True)
    return [{**read_json(p), 'url': f'/api/videos/{vid}/pacing/assets/{p.stem}'} for p in sorted(assets.glob('*.json')) if not p.name.startswith('._') and p.with_suffix('.wav').is_file()]


async def probe(path):
    raw = await media_jobs.process(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,width,height,avg_frame_rate,duration:format=duration', '-of', 'json', str(path)], timeout=30)
    data = json.loads(raw)
    streams = data.get('streams', [])
    try:
        duration = float((data.get('format') or {}).get('duration', ''))
    except (TypeError, ValueError):
        duration = math.nan
    if not math.isfinite(duration) or duration <= 0:
        # Some valid MP4 outputs expose N/A for the container duration while
        # keeping an exact duration on their video stream. Prefer video timing
        # so audio padding does not make the rendered picture appear longer.
        video_durations = []
        stream_durations = []
        for stream in streams:
            try:
                value = float(stream.get('duration', ''))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value) or value <= 0:
                continue
            stream_durations.append(value)
            if stream.get('codec_type') == 'video':
                video_durations.append(value)
        duration = max(video_durations or stream_durations, default=0)
    if not math.isfinite(duration) or not 0 < duration <= MAX_DURATION + .1:
        raise ValueError(f'Video cần có thời lượng hợp lệ, tối đa {MAX_DURATION // 60} phút.')
    video = next((s for s in streams if s['codec_type'] == 'video'), None)
    try:
        fps = float(Fraction(video.get('avg_frame_rate', '30/1'))) if video else 30
    except (ValueError, ZeroDivisionError):
        fps = 30
    if not math.isfinite(fps) or fps <= 0:
        fps = 30
    return {'duration': duration, 'fps': min(fps, 60), 'width': video['width'] if video else 0,
            'height': video['height'] if video else 0, 'has_audio': any(s['codec_type'] == 'audio' for s in streams)}


async def render(vid, path, draft, target, update, *, preview=False):
    before = fingerprint(path)
    _, cuts = selected_plan(vid, draft, path)
    info = await probe(path)
    ranges = keep_ranges(info['duration'], cuts, info['fps'])
    duration = ranges[-1]['output_end']
    events = []
    for event in draft.effects:
        asset = asset_info(vid, str(event.asset_id))
        if event.start >= duration:
            raise ValueError('Mốc hiệu ứng vượt thời lượng sau khi cắt. Sửa mốc rồi xuất lại.')
        events.append((event, asset))
    if shutil.disk_usage(directory(vid)).free < max(512 * 1024**2, path.stat().st_size * 2):
        raise ValueError('Cần thêm dung lượng trống để dựng các đoạn và bản xuất.')
    render_duration = min(12, duration) if preview else duration
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pacing-', dir=directory(vid)) as work:
        work = Path(work)
        clips, mapping = [], []
        for segment in ranges:
            if segment['output_start'] >= render_duration:
                break
            length = min(segment['output_end'], render_duration) - segment['output_start']
            clip = work / f'clip{len(clips)}.mov'
            begin = segment['output_start']

            def progress(line, begin=begin, length=length):
                if line.startswith('out_time_us='):
                    try:
                        done = min(length, float(line.split('=')[1]) / 1e6)
                        update(progress=round(8 + 67 * (begin + done) / render_duration), stage=f'Cắt và nối đoạn {len(clips) + 1}')
                    except ValueError:
                        pass
            args = ['ffmpeg', '-v', 'error', '-y', '-ss', str(segment['start']), '-i', str(path)]
            if not info['has_audio']:
                args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            args += ['-map', '0:v:0', '-map', '0:a:0' if info['has_audio'] else '1:a:0', '-t', str(length),
                     '-vf', f'setpts=PTS-STARTPTS,fps={info["fps"]},scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1',
                     '-af', f'aresample=48000:async=1:first_pts=0,apad,atrim=duration={length},asetpts=PTS-STARTPTS',
                     '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p',
                     '-c:a', 'pcm_s16le', '-ar', '48000', '-ac', '2', '-progress', 'pipe:1', str(clip)]
            await media_jobs.process(args, timeout=max(180, length * 20), on_line=progress)
            clips.append(clip)
            mapping.append({**segment, 'end': segment['start'] + length, 'output_end': begin + length})
        manifest = work / 'clips.txt'
        manifest.write_text('\n'.join(f"file '{c.name}'" for c in clips))
        args = ['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '1', '-i', str(manifest)]
        graph = f'[0:a]volume={draft.base_volume},atrim=duration={render_duration},asetpts=PTS-STARTPTS[base]'
        labels = ['[base]']
        for index, (event, asset) in enumerate(events, 1):
            if event.start >= render_duration:
                continue
            args += ['-i', str(asset_path(vid, str(event.asset_id)))]
            length = min(asset['duration'], render_duration - event.start)
            label = f'fx{index}'
            input_index = len(labels)
            graph += (f';[{input_index}:a]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={length},asetpts=PTS-STARTPTS,'
                      f'afade=t=in:d=0.015,afade=t=out:st={max(0, length-.04)}:d=0.04,volume={event.volume},adelay={round(event.start*1000)}:all=1[{label}]')
            labels.append(f'[{label}]')
        graph += f';{"".join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,alimiter=limit=.95:level=0:latency=1[aout]'
        def final_progress(line):
            if line.startswith('out_time_us='):
                try:
                    update(progress=round(76 + 20 * min(1, float(line.split('=')[1]) / 1e6 / render_duration)), stage='Trộn hiệu ứng và lưu MP4')
                except ValueError:
                    pass
        args += ['-filter_complex', graph, '-map', '0:v:0', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                 '-t', str(render_duration), '-movflags', '+faststart', '-progress', 'pipe:1', str(target)]
        try:
            await media_jobs.process(args, timeout=max(180, render_duration * 10), on_line=final_progress)
            output = await probe(target)
            if abs(output['duration'] - render_duration) > max(.15, 2 / info['fps']):
                raise ValueError('Thời lượng bản dựng không khớp. Chưa lưu bản xuất.')
            if fingerprint(path) != before:
                raise ValueError('Video nguồn đã đổi trong lúc dựng. Hãy chạy lại.')
            return {**output, 'mapping': mapping, 'original_duration': info['duration'], 'removed': info['duration'] - duration,
                    'cut_count': len(cuts), 'effects_count': len(events), 'preview': preview}
        except BaseException:
            target.unlink(missing_ok=True)
            raise
