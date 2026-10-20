"""Non-destructive timeline storage, cached media summaries and local rendering.

Inspired by OpenCut's edit decisions and source waveform summaries. The
existing FrameWeaver media job runner owns subprocess progress and cancellation.
"""
import array
import asyncio
import json
import math
from pathlib import Path
import shutil
import tempfile
import uuid
from PIL import Image

from agent.config import SHARED_OUTPUT_DIR
from agent.models.timeline import TimelineDocument
from agent.services import editing, image_effects, media_jobs, pacing, timeline_layers as layers, timeline_overlay, timeline_transitions
from agent.media_limits import MAX_DURATION
from agent.models.editing import EditingSettings, FrameSettings
from agent.services.speech_store import atomic_json, identifier, read_json

ROOT = SHARED_OUTPUT_DIR / 'timeline'
_probe_slots = asyncio.Semaphore(2)


def _out_time_seconds(line):
    """Return FFmpeg's output timestamp, ignoring its startup `N/A` marker."""
    key, separator, value = line.strip().partition('=')
    if key != 'out_time_us' or not separator:
        return None
    try:
        seconds = float(value) / 1e6
        return max(0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError, OverflowError):
        return None


def directory(vid):
    return ROOT / identifier(vid)


def asset_file(vid, aid):
    return directory(vid) / 'assets' / f'{identifier(aid)}.json'


def asset(vid, aid):
    data = read_json(asset_file(vid, aid))
    if not data:
        raise ValueError('Tư liệu không còn trong thư viện của bản dựng.')
    if data.get('category') == 'export' and (editing.directory(data['owner']) / 'deleting-exports.json').is_file():
        editing.load(data['owner'])  # Finish a confirmed deletion before reusing its file.
    path = Path(data['path'])
    if not path.is_file() or pacing.fingerprint(path) != data['fingerprint']:
        raise ValueError(f"Tư liệu đã thay đổi hoặc bị xóa: {data['name']}. Chọn lại tư liệu trong thư viện.")
    return data


def public_asset(vid, data):
    return {k: v for k, v in data.items() if k not in {'path'}} | {
        'url': f"/api/videos/{vid}/timeline/assets/{data['id']}/file",
        'visuals_ready': (directory(vid) / 'visuals' / data['id'] / 'summary.json').is_file(),
        'image_editable': not data.get('is_image') and data.get('ref', '').startswith('stickman:') and
                          read_json(Path(data['path']).with_suffix('.metadata.json'), {}).get('settings', {}).get('motion') == 'effects',
        'proxy_url': f"/api/videos/{vid}/timeline/assets/{data['id']}/proxy" if (directory(vid) / 'proxies' / f"{data['id']}.mp4").is_file() else None,
    }


async def register(vid, owner, ref, name, path, category):
    if not path.is_file():
        return None
    fingerprint = pacing.fingerprint(path)
    aid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'flowkit:timeline:{owner}:{ref}:{fingerprint}'))
    cached = read_json(asset_file(vid, aid))
    if cached:
        if cached['name'] != name[:120]:
            cached['name'] = name[:120]
            atomic_json(asset_file(vid, aid), cached)
        return public_asset(vid, cached)
    # Still images share the picture track, but have no finite source playback time.
    is_image = path.suffix.lower() in {'.image', '.png', '.jpg', '.jpeg', '.webp'}
    if is_image:
        with Image.open(path) as image:
            info = dict(width=image.width, height=image.height, duration=MAX_DURATION, fps=30,
                        has_audio=False, is_image=True, mime=Image.MIME.get(image.format, 'image/png'))
    else:
        async with _probe_slots:
            info = await pacing.probe(path)
    if category in {'source', 'export'} and not info['width']:
        raise ValueError(f'Tư liệu video không có hình ảnh: {name}.')
    if category in {'sfx', 'music'} and not info['has_audio']:
        raise ValueError(f'Tư liệu âm thanh không có tiếng: {name}.')
    data = dict(id=aid, owner=owner, ref=ref, name=name[:120], path=str(path.absolute()),
                fingerprint=fingerprint, category=category, kind='video' if info['width'] else 'audio', **info)
    atomic_json(asset_file(vid, aid), data)
    return public_asset(vid, data)


def initial(source):
    tracks = [{'id': str(uuid.uuid4()), 'name': name, 'kind': kind, 'locked': False, 'muted': False}
              for name, kind in [('Hình ảnh', 'video'), ('Âm gốc', 'audio'), ('Nhạc & hiệu ứng', 'audio')]]
    fps = source['fps'] if source else 30
    # Stored seconds always describe whole frames; trailing partial frames are dropped.
    length = math.floor(source['duration'] * fps + 1e-6) / fps if source else 0
    group = str(uuid.uuid4())
    clips = []
    if source and length >= 1 / fps:
        for track in tracks[:2 if source['has_audio'] else 1]:
            clips.append(dict(id=str(uuid.uuid4()), asset_id=source['id'], track_id=track['id'],
                              start=0, source_in=0, duration=length, gain=1, group=group))
    width, height = (source['width'], source['height']) if source else (1280, 720)
    factor = min(1, 3840 / max(width, height), math.sqrt(3840 * 2160 / (width * height)))
    return TimelineDocument(fps=fps, width=max(2, math.floor(width * factor / 2) * 2),
                            height=max(2, math.floor(height * factor / 2) * 2), tracks=tracks, clips=clips)


def load(vid):
    data = read_json(directory(vid) / 'document.json')
    if data:
        data['document'] = TimelineDocument.model_validate(data['document']).model_dump(mode='json')
    return data


def initialize(vid, source):
    saved = load(vid)
    if saved:
        return saved
    result = dict(revision=0, document=initial(source).model_dump(mode='json'))
    # A project with no local source is initialized only after preparation.
    if source:
        atomic_json(directory(vid) / 'document.json', result)
    return result


def validate_assets(vid, document):
    tracks = {t.id: t for t in document.tracks}
    assets = {aid: asset(vid, aid) for aid in {str(c.asset_id) for c in document.clips if c.asset_id}}
    for clip in document.clips:
        if not clip.asset_id:
            if clip.effect and clip.effect.kind == 'image' and (not clip.effect.image_id or not editing.asset_path(vid, str(clip.effect.image_id)).is_file()):
                raise ValueError('Ảnh/logo đã bị xóa hoặc chưa tải lên.')
            if tracks[clip.track_id].kind in {'caption', 'text'}:
                editing.font_path(clip.caption.font_id)
            continue
        data = assets[str(clip.asset_id)]
        if clip.image_effect and not data.get('is_image'):
            raise ValueError('Chọn ảnh gốc để dùng hiệu ứng ảnh; clip AI giữ chuyển động riêng.')
        if data.get('is_image') and tracks[clip.track_id].kind != 'video':
            raise ValueError('Ảnh cảnh chỉ dùng trên lớp hình ảnh chính.')
        if not data.get('is_image') and clip.source_in + clip.duration * clip.speed > data['duration'] + .001:
            raise ValueError(f"Đoạn cắt vượt thời lượng tư liệu: {data['name']}.")
        if tracks[clip.track_id].kind in {'video', 'overlay'} and data['kind'] != 'video':
            raise ValueError('Không thể đặt file âm thanh vào lớp hình ảnh.')
        if tracks[clip.track_id].kind == 'audio' and not data['has_audio']:
            raise ValueError('Tư liệu đã chọn không có âm thanh.')
    return assets


def save(vid, revision, document):
    current = load(vid)
    if (current or {}).get('revision', 0) != revision:
        raise ValueError('Bản dựng đã đổi ở tab khác. Nạp lại trước khi tiếp tục.')
    validate_assets(vid, document)
    result = dict(revision=revision + 1, document=document.model_dump(mode='json'))
    atomic_json(directory(vid) / 'document.json', result)
    return result


def summarize_pcm(path, duration):
    """Bounded memory: 256 PCM samples per bucket, not a full decoded AudioBuffer."""
    peaks = []
    with path.open('rb') as stream:
        while raw := stream.read(256 * 2):
            values = array.array('h', raw)
            peaks.append(round(max((abs(n) for n in values), default=0) / 32768, 4))
    return dict(peaks=peaks, interval=.032, duration=duration, silences=layers.silence_ranges(peaks, .032),
                peak_db=round(20*math.log10(max(max(peaks, default=0), 1e-6)), 2))


async def visualize(vid, aid, update):
    data = asset(vid, aid)
    target = directory(vid) / 'visuals' / aid
    target.mkdir(parents=True, exist_ok=True)
    cached = read_json(target / 'summary.json')
    if cached:
        return cached
    with tempfile.TemporaryDirectory(prefix='visuals-', dir=directory(vid)) as work:
        work = Path(work)
        result = dict(peaks=[], interval=.032, duration=data['duration'], thumbnail_count=0)
        if data['has_audio']:
            update(stage='Đọc âm thanh thật', progress=8)
            def progress(line):
                seconds = _out_time_seconds(line)
                if seconds is not None:
                    update(progress=round(8 + 65 * min(1, seconds / data['duration'])))
            pcm = work / 'audio.pcm'
            await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-i', data['path'], '-map', '0:a:0', '-vn',
                '-ac', '1', '-ar', '8000', '-f', 's16le', '-progress', 'pipe:1', str(pcm)],
                timeout=max(180, data['duration'] * 2), on_line=progress)
            result.update(await asyncio.to_thread(summarize_pcm, pcm, data['duration']))
        if data['kind'] == 'video':
            update(stage='Tạo dải ảnh thật từ video', progress=78)
            count = 1 if data.get('is_image') else min(60, max(1, math.ceil(data['duration'] / 4)))
            interval = data['duration'] / count
            sprite = work / 'sprite.jpg'
            thumbnail_filter = 'scale=120:68:force_original_aspect_ratio=decrease,pad=120:68:(ow-iw)/2:(oh-ih)/2'
            if data.get('is_image'):
                await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-i', data['path'], '-vf', thumbnail_filter,
                                         '-frames:v', '1', str(sprite)], timeout=60)
            elif data['duration'] > 600:
                # Seek to the 60 samples; do not decode hours of frames just for a sprite.
                for index in range(count):
                    await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-ss', str((index+.5)*interval),
                        '-i', data['path'], '-an', '-vf', thumbnail_filter, '-frames:v', '1', str(work / f'thumb{index}.jpg')], timeout=60)
                    update(stage='Tạo dải ảnh thật từ video', progress=78+round(20*(index+1)/count))
                await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-framerate', '1', '-i', str(work / 'thumb%d.jpg'),
                    '-vf', f'tile={count}x1:padding=0', '-frames:v', '1', str(sprite)], timeout=60)
            else:
                await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-i', data['path'], '-an',
                    '-vf', f'fps={1/interval},{thumbnail_filter},tile={count}x1:padding=0',
                    '-frames:v', '1', str(sprite)], timeout=max(180, data['duration'] * 3))
            sprite.replace(target / 'sprite.jpg')
            result.update(thumbnail_count=count, thumbnail_interval=interval, thumbnail_width=120,
                          thumbnail_height=68, thumbnail_url=f'/api/videos/{vid}/timeline/assets/{aid}/sprite')
        asset(vid, aid)  # Never publish a cache made from a replaced source.
        atomic_json(target / 'summary.json', result)
        return result


def duration(document):
    return max((c.start + c.duration for c in document.clips), default=0)


def sound_clips(document, length=None):
    tracks = {t.id: t for t in document.tracks}
    length = duration(document) if length is None else length
    return sorted((c for c in document.clips if tracks[c.track_id].kind == 'audio'
        and layers.audible(document, tracks[c.track_id]) and not c.muted and c.gain
        and c.start < length), key=lambda c: c.start)


def timing_key(document):
    """Picture edit identity; changing narration/captions must not invalidate it."""
    import hashlib
    tracks = {t.id: t for t in document.tracks}
    fields = ('id', 'asset_id', 'start', 'source_in', 'duration', 'speed')
    values = [{k: c.model_dump(mode='json')[k] for k in fields} for c in sorted(document.clips, key=lambda c: (c.start, str(c.id)))
              if tracks[c.track_id].kind == 'video']
    return hashlib.sha256(json.dumps([document.fps, values], sort_keys=True).encode()).hexdigest()


async def audio_stems(document, assets, work, update, *, originals=None, range_start=0,
                      voice_ranges=None, progress=None, length=None):
    """One bounded mixer shared by video export and Whisper audio extraction."""
    length = duration(document) if length is None else length
    tracks = {t.id: t for t in document.tracks}
    originals = originals or {c.id: c for c in document.clips}
    sound = layers.audio_windows(document, [c.model_copy(update={'duration': min(c.duration, length-c.start)})
        for c in sound_clips(document, length)], tracks)
    if progress is None:
        def progress(begin, span, offset, total, stage):
            def line(text):
                seconds = _out_time_seconds(text)
                if seconds is not None:
                    update(stage=stage, progress=round(offset+total*min(1, seconds/span)))
            return line
    # Mix in batches, so a long timeline never opens 128 source decoders together.
    stems = []
    for batch in range(max(1, math.ceil(len(sound) / 12))):
        group = sound[batch * 12:(batch + 1) * 12]
        begin = min((c.start for c in group), default=0)
        batch_length = min(length, max((c.start+c.duration for c in group), default=length))-begin
        args = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
        graph, labels = [f'[0:a]atrim=duration={batch_length}[base]'], ['[base]']
        for index, clip in enumerate(group, 1):
            span = min(clip.duration, length - clip.start)
            args += ['-ss', str(clip.source_in), '-t', str(span*clip.speed), '-i', assets[str(clip.asset_id)]['path']]
            original = originals.get(clip.id, clip)
            volume = layers.volume_expression(document, clip, tracks[clip.track_id], original=original,
                offset=max(0, range_start+clip.start-original.start), intervals=voice_ranges)
            graph.append(f"[{index}:a:0]aresample=48000:async=1:first_pts=0,aformat=channel_layouts=stereo,atempo={clip.speed},apad,atrim=duration={span},asetpts=PTS-STARTPTS,volume='{volume}':eval=frame,adelay={round((clip.start-begin)*48000)}S:all=1[a{index}]")
            labels.append(f'[a{index}]')
        graph.append(f'{"".join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0[out]')
        stem = work / f'stem{batch}.wav'
        args += ['-filter_complex', ';'.join(graph), '-map', '[out]', '-t', str(batch_length), '-c:a', 'pcm_s16le', '-progress', 'pipe:1', str(stem)]
        await media_jobs.process(args, timeout=max(180, batch_length * 10),
                                 on_line=progress(length-batch_length, batch_length, 55 + batch * 25 / max(1, math.ceil(len(sound)/12)), 25 / max(1, math.ceil(len(sound)/12)), 'Trộn các lớp âm thanh'))
        stems.append((stem, begin, batch_length))
    stems = await layers.collapse_stems(stems, work, update)
    return stems


async def render_audio(vid, document, target, update, *, start=0, end=None, allow_silence=False):
    """Render only the requested audio window, never re-encode source pictures."""
    assets = validate_assets(vid, document)
    cropped = layers.range_document(document, start, end)
    if not sound_clips(cropped) and not allow_silence:
        raise ValueError('Bản dựng không có lớp âm thanh đang bật trong đoạn này. Bật Giọng đọc/Âm gốc hoặc nhập SRT; Whisper cần tiếng để nhận dạng.')
    start = round(start*document.fps)/document.fps
    end = round((duration(document) if end is None else end)*document.fps)/document.fps
    length = end-start
    reserve = 256*1024**2 + (sum(c.duration for c in sound_clips(cropped))+2*length)*192000
    if shutil.disk_usage(directory(vid)).free < reserve:
        raise ValueError('Không đủ dung lượng trống để trộn âm thanh nhận dạng. Dọn dung lượng rồi tiếp tục tác vụ.')
    summaries = {}
    if document.ducking.enabled and document.ducking.detect_pauses:
        voice_tracks = {t.id for t in document.tracks if t.role == 'voice'}
        for aid in dict.fromkeys(str(c.asset_id) for c in sound_clips(cropped) if c.track_id in voice_tracks):
            summaries[aid] = await visualize(vid, aid, lambda **_: None)
    with tempfile.TemporaryDirectory(prefix='asr-mix-', dir=directory(vid)) as work:
        stems = await audio_stems(cropped, assets, Path(work), update,
            originals={c.id: c for c in document.clips}, range_start=round(start*document.fps)/document.fps,
            voice_ranges=[(a-round(start*document.fps)/document.fps, b-round(start*document.fps)/document.fps)
                          for a, b in layers.voice_intervals(document, summaries)], length=length)
        args = ['ffmpeg', '-nostdin', '-v', 'error', '-y']
        for stem, _, _ in stems:
            args += ['-i', str(stem)]
        graph = ';'.join(f'[{i}:a]adelay={round(begin*48000)}S:all=1[s{i}]' for i, (_, begin, _) in enumerate(stems))
        graph += ';' + ''.join(f'[s{i}]' for i in range(len(stems))) + f'amix=inputs={len(stems)}:normalize=0,apad,atrim=duration={length},alimiter=limit=.95:level=0:latency=1[out]'
        await media_jobs.process(args+['-filter_complex', graph, '-map', '[out]', '-ac', '1', '-ar', '16000',
            '-c:a', 'pcm_s16le', '-t', str(length), str(target)], timeout=max(180, length*10))
    validate_assets(vid, document)
    return length


async def render(vid, document, target, update, *, preview=False, source_document=None, range_start=0):
    assets = validate_assets(vid, document)
    originals = {c.id: c for c in (source_document or document).clips}
    transitions = timeline_transitions.joins(source_document or document)
    original_tracks = {t.id: t for t in (source_document or document).tracks}
    length = duration(document)
    if length < .2:
        raise ValueError('Thêm ít nhất 0.2 giây tư liệu vào bản dựng trước khi xuất.')
    length = min(length, 12) if preview else length
    fps, width, height = document.fps, document.width, document.height
    frame = document.frame or FrameSettings(color='#000000')
    width, height = editing.dimensions(frame, width, height)
    output_frame = frame.model_copy(update={'preset': 'custom', 'width': width, 'height': height})
    length = round(length * fps) / fps
    tracks = {t.id: t for t in document.tracks}
    picture = sorted((c for c in document.clips if tracks[c.track_id].kind == 'video' and not tracks[c.track_id].muted), key=lambda c: c.start)
    sound = sorted((c for c in document.clips if tracks[c.track_id].kind == 'audio' and layers.audible(document, tracks[c.track_id]) and not c.muted and c.gain and c.start < length), key=lambda c: c.start)
    # Clip before splitting long ducked tracks: no window may start beyond a
    # short preview. Original envelopes still come from `originals` below.
    sound = layers.audio_windows(document, [c.model_copy(update={
        'duration': min(c.duration, length-c.start)}) for c in sound], tracks)
    overlays = sorted((c for c in document.clips if tracks[c.track_id].kind == 'overlay'
                       and not tracks[c.track_id].muted and not c.overlay.hidden and c.start < length), key=lambda c: c.start)
    size = sum(Path(a['path']).stat().st_size for a in assets.values())
    directory(vid).mkdir(parents=True, exist_ok=True)
    # PCM intermediates cost 192 KB/s per mix stem; bound work before rendering.
    stem_seconds = sum(min(length, max(c.start+c.duration for c in sound[i:i+12]))-sound[i].start for i in range(0,len(sound),12))
    caption_seconds = length if any(tracks[c.track_id].kind in {'caption', 'text'} and not tracks[c.track_id].muted for c in document.clips) else 0
    mix_levels = max(0, math.ceil(math.log(max(1, math.ceil(len(sound)/12)), 16))-1)
    reserve = max(512 * 1024**2, size * 2 + (length*2+stem_seconds*(1+mix_levels))*192000 + caption_seconds*width*height*.15)
    if shutil.disk_usage(directory(vid)).free < reserve:
        raise ValueError('Không đủ dung lượng trống để dựng video và các lớp âm thanh.')
    target.parent.mkdir(parents=True, exist_ok=True)
    summaries = {}
    if document.ducking.enabled and document.ducking.detect_pauses:
        voice_ids = list(dict.fromkeys(str(c.asset_id) for c in sound if tracks[c.track_id].role == 'voice'))
        for index, aid in enumerate(voice_ids):
            def analysis_progress(index=index, **changes):
                update(stage='Phân tích khoảng có lời kể', progress=round(3*(index+changes.get('progress', 0)/100)/len(voice_ids)))
            summaries[aid] = await visualize(vid, aid, analysis_progress)
    voice_ranges = layers.voice_intervals(document, summaries)
    with tempfile.TemporaryDirectory(prefix='render-', dir=directory(vid)) as work:
        work = Path(work)
        cursor, segments = 0., []
        for clip in picture:
            if clip.start >= length:
                break
            if clip.start > cursor + 1e-6:
                segments.append((cursor, clip.start - cursor, None))
            span = min(clip.duration, length - clip.start)
            segments.append((clip.start, span, clip))
            cursor = clip.start + span
        if cursor < length - 1e-6:
            segments.append((cursor, length - cursor, None))

        def progress(begin, span, offset, total, stage):
            def line(text):
                seconds = _out_time_seconds(text)
                if seconds is not None:
                    done = min(span, seconds)
                    update(progress=round(offset + total * (begin + done) / length), stage=stage)
            return line

        segments = timeline_overlay.main_segments(segments, overlays)
        for index, (begin, span, clip, split) in enumerate(segments):
            path = work / f'clip{index}.mp4'
            args = ['ffmpeg', '-v', 'error', '-y']
            if clip:
                source = assets[str(clip.asset_id)]
                pane = output_frame.model_copy(update={'width': timeline_overlay.pane_width(width)}) if split else output_frame
                layout, _ = editing.layout_filter(pane, source['width'], source['height'])
                if split:
                    layout = layout.replace('[vout]', '[pane]') + f';[pane]pad={width}:{height}:0:0:color={frame.color}[vout]'
                graph = f'[0:v]setpts=(PTS-STARTPTS)/{clip.speed},fps={fps}[timed];' + layout.replace('[0:v]', '[timed]')
                source_in = clip.source_in + (begin-clip.start)*clip.speed
                if source.get('is_image'):
                    animated = clip.model_copy(update={'source_in': source_in})
                    effect_graph = image_effects.clip_filter(animated, source, span, fps, document.width, document.height)
                    layout, _ = editing.layout_filter(pane, document.width, document.height)
                    if split:
                        layout = layout.replace('[vout]', '[pane]') + f';[pane]pad={width}:{height}:0:0:color={frame.color}[vout]'
                    graph = '[0:v]' + effect_graph + '[timed];' + layout.replace('[0:v]', '[timed]')
                    args += ['-i', source['path']]
                else:
                    args += ['-ss', str(source_in), '-i', source['path']]
                join = transitions.get(clip.id)
                original = originals[clip.id]
                elapsed = max(0, range_start + begin - original.start)
                video_output = 'vout'
                if join and elapsed < join[1] - 1e-6:
                    previous, transition_duration = join
                    previous_source = assets.get(str(previous.asset_id)) or asset(vid, str(previous.asset_id))
                    previous_time = previous.start + previous.duration - 1/fps
                    previous_split = any(original_tracks[c.track_id].kind == 'overlay' and not original_tracks[c.track_id].muted
                        and not c.overlay.hidden and c.overlay.layout == 'split' and c.start <= previous_time < c.start+c.duration
                        for c in (source_document or document).clips)
                    extra, transition_graph = timeline_transitions.compose(previous, previous_source, original.transition,
                        transition_duration, elapsed, output_frame, fps, width, height, previous_split,
                        image_size=(document.width, document.height))
                    args += extra
                    graph += ';' + transition_graph
                    video_output = 'transitioned'
                args += ['-filter_complex_threads', '2', '-filter_complex', graph, '-map', f'[{video_output}]']
            else:
                args += ['-f', 'lavfi', '-i', f'color=c={frame.color}:s={width}x{height}:r={fps}']
            args += ['-an', '-t', str(span), '-frames:v', str(round(span * fps)), '-c:v', 'libx264', '-preset', 'veryfast',
                '-crf', '20', '-pix_fmt', 'yuv420p', '-progress', 'pipe:1', str(path)]
            # Cache picture segments independently of voices, captions and final mix.
            # Arguments include crop, transition phase, source seek and effect values.
            import hashlib
            cache_key = hashlib.sha256(json.dumps([2, args[:-1],
                sorted((a['path'], a['fingerprint']) for a in assets.values() if a['path'] in args)], sort_keys=True).encode()).hexdigest()
            cache_dir = directory(vid)/'picture-cache'
            cache_dir.mkdir(exist_ok=True)
            cached = cache_dir/f'{cache_key}.mp4'
            if cached.is_file():
                update(progress=4+round(51*(begin+span)/length), stage=f'Dùng lại hình · đoạn {index+1}/{len(segments)}')
                cached.touch()
            else:
                temporary = cached.with_name(f'{cache_key}.{uuid.uuid4().hex}.part.mp4')
                try:
                    await media_jobs.process(args[:-1]+[str(temporary)], timeout=max(180, span * 20),
                        on_line=progress(begin, span, 4, 51, f'Dựng hình · đoạn {index+1}/{len(segments)}'))
                    temporary.replace(cached)
                finally:
                    temporary.unlink(missing_ok=True)
            try:
                path.hardlink_to(cached)
            except OSError:
                # Portable to external exFAT/FAT volumes without hard-link support.
                shutil.copyfile(cached, path)
            # Bound this disposable cache; source images and exports remain separate.
            total = 0
            for entry in sorted(cache_dir.glob('*.mp4'), key=lambda p: p.stat().st_mtime, reverse=True):
                total += entry.stat().st_size
                if total > 256*1024**2 and entry != cached:
                    entry.unlink(missing_ok=True)
        manifest = work / 'clips.txt'
        manifest.write_text('\n'.join(f"file 'clip{i}.mp4'" for i in range(len(segments))))
        stems = await audio_stems(document, assets, work, update, originals=originals,
            range_start=range_start, voice_ranges=voice_ranges, progress=progress, length=length)
        args = ['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '1', '-i', str(manifest)]
        for stem, _, _ in stems:
            args += ['-i', str(stem)]
        graph = ';'.join(f'[{i+1}:a]adelay={round(begin*48000)}S:all=1[stem{i}]' for i,(_,begin,_) in enumerate(stems))
        graph += ';' + ''.join(f'[stem{i}]' for i in range(len(stems))) + f'amix=inputs={len(stems)}:normalize=0,apad,atrim=duration={length},alimiter=limit=.95:level=0:latency=1[a]'
        effects, regions = layers.effect_settings(document, length)
        caption_movie = await layers.caption_movie(document.model_copy(update={'width': width, 'height': height}), work, length, update)
        has_video_graph = bool(effects or caption_movie or overlays)
        current = '0:v'
        for index, effect in enumerate(c for c in effects if c.colors):
            color = effect.colors
            graph += f";[{current}]eq=brightness={color.brightness}:contrast={color.contrast}:saturation={color.saturation}:enable='gte(t,{effect.start})*lt(t,{effect.start+effect.duration})'[color{index}]"
            current = f'color{index}'
        pip_args, pip_graph, current, pip_inputs = timeline_overlay.compose(overlays, assets, width, height, fps, length, 1+len(stems), current, work)
        args += pip_args
        if pip_graph:
            graph += ';' + ';'.join(pip_graph)
        settings = EditingSettings.model_construct(regions=regions)
        video_graph, images, _ = editing.processing_filter(settings, width, height, video_id=vid)
        if regions:
            for index in range(len(images), 0, -1):
                video_graph = video_graph.replace(f'[{index}:v]', f'[{index+len(stems)+pip_inputs}:v]')
            graph += ';' + video_graph.replace('[0:v]', f'[{current}]')
            args += editing.image_inputs(images)
            current = 'vout'
        if caption_movie:
            args += ['-i', str(caption_movie)]
            graph += f';[{current}][{1+len(stems)+pip_inputs+len(images)}:v]overlay=0:0:eof_action=pass[captioned]'
            current = 'captioned'
        if has_video_graph and current == '0:v':
            graph += ';[0:v]null[timelineout]'
            current = 'timelineout'
        video_map = f'[{current}]' if has_video_graph else '0:v:0'
        args += ['-filter_complex_threads', '2']
        args += ['-filter_complex', graph, '-map', video_map, '-map', '[a]', '-c:v', 'libx264' if has_video_graph else 'copy']
        if has_video_graph:
            args += ['-preset', 'veryfast', '-crf', '20', '-pix_fmt', 'yuv420p']
        args += ['-c:a', 'aac', '-b:a', '192k',
                 '-t', str(length), '-movflags', '+faststart', '-progress', 'pipe:1', str(target)]
        await media_jobs.process(args, timeout=max(180, length * 10), on_line=progress(0, length, 90 if caption_movie else 80, 7 if caption_movie else 17, 'Lưu bản dựng MP4'))
        output = await pacing.probe(target)
        if abs(output['duration'] - length) > max(.15, 2 / fps):
            raise ValueError('Thời lượng bản xuất chưa khớp bản dựng. Chưa lưu phiên bản.')
        validate_assets(vid, document)
        return {**output, 'preview': preview, 'clip_count': len(document.clips)}


async def proxy(vid, aid, update):
    data = asset(vid, aid)
    if data['kind'] != 'video' or data.get('is_image'):
        raise ValueError('Bản xem nhẹ chỉ dùng cho video.')
    target = directory(vid) / 'proxies' / f'{aid}.mp4'
    if target.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.part.mp4')
    if shutil.disk_usage(target.parent).free < max(512*1024**2, data['duration']*250000):
        raise ValueError('Không đủ dung lượng cho bản xem nhẹ.')
    try:
        def progress(line):
            seconds = _out_time_seconds(line)
            if seconds is not None:
                update(progress=min(98, round(seconds/data['duration']*98)), stage='Tạo bản xem nhẹ 720p')
        await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-i', data['path'], '-map', '0:v:0',
            '-an', '-vf', r'scale=w=trunc(iw*min(1\,720/max(iw\,ih))/2)*2:h=trunc(ih*min(1\,720/max(iw\,ih))/2)*2',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '28', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
            '-progress', 'pipe:1', str(temp)], timeout=max(3600, data['duration']*10), on_line=progress)
        measured = await pacing.probe(temp)
        if abs(measured['duration']-data['duration']) > max(.15, 2/(data['fps'] or 30)):
            raise ValueError('Bản xem nhẹ chưa khớp thời lượng nguồn.')
        asset(vid, aid)
        temp.replace(target)
    finally:
        temp.unlink(missing_ok=True)
