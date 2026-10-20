"""Small standalone AI previews. Never feeds a full-length source to ProPainter."""
import asyncio
import json
import math
import shutil
from pathlib import Path
import re
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from contextvars import ContextVar

from agent.models.editing import EditingSettings
from agent.services import editing, media_jobs, model_storage
from agent.services.speech_store import atomic_json, read_json
from agent.paths import DATA_DIR, RESOURCE_DIR

PROJECT = DATA_DIR
CACHE = PROJECT / '.flowkit' / 'propainter'
PYTHON = PROJECT / '.venvs' / 'propainter' / 'bin' / 'python'
LOCK = asyncio.Lock()
WEIGHTS = ('ProPainter.pth', 'raft-things.pth', 'recurrent_flow_completion.pth')
_session = ContextVar('propainter_session', default=None)


def weights_directory():
    return model_storage.location('propainter', CACHE / 'weights', preserve_existing=True)


@asynccontextmanager
async def inference_session():
    """Nested clip/trial calls reuse their batch's lock and lazy model process."""
    from agent.services.propainter_runtime import ProPainterSession
    if _session.get() is not None:
        yield _session.get()
        return
    async with LOCK:
        model_storage.assert_weights('propainter', weights_directory(), WEIGHTS)
        session = ProPainterSession(PYTHON, RESOURCE_DIR / 'tools' / 'propainter_worker.py', source_directory())
        token = _session.set(session)
        try:
            yield session
        finally:
            _session.reset(token)
            await session.close()


def source_directory():
    local = PROJECT / 'ProPainter-main'
    return local if (local / 'inference_propainter.py').is_file() else CACHE / 'source'


def availability():
    benchmark = editing.read_json(CACHE / 'benchmark.json', {})
    try:
        model_storage.assert_weights('propainter', weights_directory(), WEIGHTS)
        model_error = None
    except ValueError as exc:
        model_error = str(exc)
    reason = ('Thiếu mã nguồn ProPainter. Cần khôi phục môi trường xử lý.' if not (source_directory() / 'inference_propainter.py').is_file()
              else 'Thiếu môi trường Python ProPainter.' if not PYTHON.is_file()
              else model_error if model_error
              else 'Môi trường ProPainter chưa đạt thử nghiệm.' if benchmark.get('status') != 'COMPLETED' else None)
    return {'available': reason is None, 'unavailable_reason': reason, 'benchmark': benchmark, 'max_seconds': 3, 'max_frames': 60, 'max_side': 320,
            'scope': 'Xử lý đoạn 1–3 giây, tối đa 60 khung hình, cạnh dài 320 pixel.'}


async def inpaint(work, width, height, dilation, update):
    """One bounded window, shared by trials and full-scene restoration."""
    peak_rss = [0]
    import psutil
    async def monitor():
        process = psutil.Process()
        while True:
            try:
                peak_rss[0] = max(peak_rss[0], max((p.memory_info().rss for p in process.children(recursive=True)
                    if 'propainter_worker.py' in ' '.join(p.cmdline())), default=0))
            except psutil.Error:
                pass
            await asyncio.sleep(.25)
    def line(text):
        phases = {'load': ('Nạp model lần đầu cho tác vụ', 15),
                  'flow': ('AI đang tính chuyển động', 20), 'complete': ('AI đang khôi phục chuyển động vùng xóa', 40),
                  'propagate': ('AI đang lấp nền từ các khung lân cận', 55), 'inpaint': ('AI đang tái tạo chi tiết nền', 65)}
        if match := re.search(r'FLOWKIT_STAGE:(\w+)', text):
            if match[1] in phases:
                stage, progress = phases[match[1]]
                update(stage=stage, progress=progress)
        elif match := re.search(r'(\d+)%\|.*?\|\s*(\d+)/(\d+)', text):
            update(stage=f'AI tái tạo nền: lượt {match[2]}/{match[3]}', progress=65 + round(int(match[1]) * .2))
    observer = asyncio.create_task(monitor())
    try:
        async with inference_session() as session:
            await session.run(work, width, height, dilation, line)
    finally:
        observer.cancel()
        await asyncio.gather(observer, return_exceptions=True)
    return peak_rss[0]


def results(vid):
    """Completed previews outlive the shared workbench's most recent job."""
    folder = editing.directory(vid) / 'propainter'
    saved = []
    for path in folder.glob('*.json'):
        try:
            identifier = str(uuid.UUID(path.stem))
        except ValueError:
            continue
        try:
            data = read_json(path, {})
            if not isinstance(data, dict) or data.get('trial_id') != identifier or not (folder / f'{identifier}.mp4').is_file():
                continue
            # One damaged record must not break the tool or the entire timeline catalog.
            numeric = ('start', 'created_at', 'duration', 'width', 'height', 'inference_seconds', 'peak_rss_bytes', 'peak_mps_driver_bytes')
            if any(not isinstance(data.get(key), (int, float)) or isinstance(data[key], bool)
                   or not math.isfinite(data[key]) or data[key] < 0 for key in numeric):
                continue
            if min(data['duration'], data['width'], data['height']) <= 0 or not isinstance(data.get('device'), str):
                continue
            data['url'] = f'/api/videos/{vid}/editing/propainter/results/{identifier}'
            data['before_url'] = f"{data['url']}/before" if (folder / f'{identifier}-before.mp4').is_file() else None
            saved.append(data)
        except (OSError, ValueError, UnicodeError):
            continue
    return sorted(saved, key=lambda r: r.get('created_at', 0), reverse=True)


async def source_info(path):
    raw = await media_jobs.process(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], timeout=30)
    data = json.loads(raw)
    streams = data.get('streams') or []
    stream = next((s for s in streams if s.get('codec_type') == 'video'), None)
    if not stream:
        raise ValueError('Tư liệu không có hình ảnh để xử lý ProPainter.')
    from fractions import Fraction
    rate = None
    for key in ('avg_frame_rate', 'r_frame_rate'):
        try:
            candidate = Fraction(stream.get(key, ''))
            if candidate > 0:
                rate = candidate
                break
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    if rate is None or rate > 60:
        raise ValueError('FPS nguồn không phù hợp để thử ProPainter.')
    duration = 0
    for value in ((data.get('format') or {}).get('duration'), stream.get('duration')):
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(candidate) and candidate > 0:
            duration = candidate
            break
    if not duration:
        raise ValueError('Không đọc được thời lượng video. Chuẩn hóa file trước khi dùng ProPainter.')
    return {'width': int(stream['width']), 'height': int(stream['height']), 'fps': str(rate), 'fps_number': float(rate),
            'duration': duration, 'has_audio': any(s.get('codec_type') == 'audio' for s in streams)}


def validate(settings, region_id, start, duration, info):
    region = next((r for r in settings.regions if str(r.id) == str(region_id)), None)
    if not region:
        raise ValueError('Thêm và chọn một vùng trước khi thử ProPainter.')
    if start < region.start or start + duration > min(region.end, info['duration']) + .05:
        raise ValueError('Đoạn thử phải nằm trong khoảng thời gian của vùng chọn và video.')
    if duration * info['fps_number'] > 60.01:
        raise ValueError('Đoạn thử tối đa 60 frame. Chọn 1 giây ở 60 FPS, 2 giây ở 30 FPS hoặc 3 giây ở 20 FPS.')
    if region.width * region.height > .35:
        raise ValueError('Thử vùng nhỏ hơn 35% khung hình trước để kiểm soát bộ nhớ và chất lượng.')
    return region


async def run_trial(vid, source, settings: EditingSettings, region, start, duration, dilation, info, update, max_side=192):
    from PIL import Image, ImageDraw
    started, peak_rss = time.monotonic(), [0]
    trial_id = str(uuid.uuid4())
    target = editing.directory(vid) / 'propainter' / f'{trial_id}.mp4'
    before = target.with_name(f'{trial_id}-before.mp4')
    record = target.with_suffix('.json')
    target.parent.mkdir(parents=True, exist_ok=True)
    update(stage='Đang chờ worker ProPainter', progress=0)
    async with inference_session():
        with tempfile.TemporaryDirectory(prefix='trial-', dir=CACHE) as folder:
            work = Path(folder)
            (work / 'weights').symlink_to(weights_directory(), target_is_directory=True)
            (work / 'frames').mkdir()
            clean = settings.model_copy(update={'regions': []}, deep=True)
            graph, _, size = editing.processing_filter(clean, info['width'], info['height'])
            scale = min(1, max_side / max(size))
            width, height = max(16, int(size[0] * scale) // 16 * 16), max(16, int(size[1] * scale) // 16 * 16)
            graph += f';[vout]scale={width}:{height}[small]'
            clip = work / 'clip.mp4'
            try:
                update(stage='Cắt đoạn ngắn và dựng mask', progress=5)
                await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-ss', str(start), '-t', str(duration), '-i', str(source),
                    '-filter_complex', graph, '-map', '[small]', '-map', '0:a?', '-r', info['fps'], '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(clip)], timeout=120)
                await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-i', str(clip), str(work / 'frames' / '%05d.png')], timeout=60)
                if len(list((work / 'frames').glob('*.png'))) > 60:
                    raise ValueError('Đoạn đã cắt vượt 60 frame; không chạy model trên đoạn này.')
                mask = Image.new('L', (width, height))
                x, y, w, h = editing.region_pixels(region, width, height)
                ImageDraw.Draw(mask).rectangle((x, y, x + w - 1, y + h - 1), fill=255)
                mask.save(work / 'mask.png')
                update(stage='ProPainter: nạp model và tính chuyển động', progress=15)
                peak_rss[0] = await inpaint(work, width, height, dilation, update)
                update(stage='Khôi phục FPS, ghép âm gốc và kiểm tra', progress=90)
                raw = work / 'output' / 'frames' / 'inpaint_out.mp4'
                # Upstream writes 12 FPS. Restore the exact source rate and copy the clip audio.
                await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-i', str(raw), '-i', str(clip),
                    '-map', '0:v:0', '-map', '1:a?', '-vf', f"setpts=12/{info['fps_number']}*PTS", '-r', info['fps'],
                    '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', str(target)], timeout=60)
                actual = await source_info(target)
                if abs(actual['duration'] - duration) > .12 or actual['fps'] != info['fps'] or actual['has_audio'] != info['has_audio']:
                    raise ValueError('Đoạn thử chưa giữ đúng FPS, âm thanh hoặc thời lượng; không lưu kết quả.')
                metrics = json.loads((work / 'metrics.json').read_text())
                # Commit the comparison and metadata only after the result passes validation.
                shutil.copyfile(clip, before)
                result = {'trial_id': trial_id, 'created_at': time.time(),
                    'url': f'/api/videos/{vid}/editing/propainter/results/{trial_id}',
                    'before_url': f'/api/videos/{vid}/editing/propainter/results/{trial_id}/before', **actual, **metrics,
                    'start': start, 'requested_duration': duration, 'wall_seconds': round(time.monotonic() - started, 2), 'peak_rss_bytes': peak_rss[0],
                    'settings': settings.model_dump(mode='json'), 'region_id': str(region.id), 'dilation': dilation, 'max_side': max_side,
                    'scope': 'Đoạn thử độ phân giải thấp; cần tự xem chất lượng vùng trước khi sử dụng.'}
                atomic_json(record, result)
                update(result=result)
            except BaseException:
                target.unlink(missing_ok=True)
                before.unlink(missing_ok=True)
                record.unlink(missing_ok=True)
                raise
