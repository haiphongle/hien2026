"""Shared timing, gain envelopes and raster overlays for the timeline renderer."""
import asyncio
import math
from copy import deepcopy

from PIL import Image

from agent.models.editing import TextStyle, EditingSettings
from agent.models.timeline import TimelineDocument
from agent.services import editing, media_jobs


def audible(document, track):
    solo = any(t.kind == 'audio' and t.solo for t in document.tracks)
    return not track.muted and (not solo or track.solo)


def voice_intervals(document, summaries=None):
    tracks = {t.id: t for t in document.tracks}
    ranges = []
    for c in document.clips:
        track = tracks[c.track_id]
        if track.kind != 'audio' or track.role != 'voice' or not audible(document, track) or c.muted or not c.gain:
            continue
        summary = (summaries or {}).get(str(c.asset_id)) if document.ducking.detect_pauses else None
        cursor, end = c.source_in, c.source_in + c.duration*c.speed
        for quiet in (summary or {}).get('silences', []):
            a, b = max(cursor, quiet['start']), min(end, quiet['end'])
            if b <= a:
                continue
            if a > cursor:
                ranges.append((c.start+(cursor-c.source_in)/c.speed, c.start+(a-c.source_in)/c.speed))
            cursor = b
        if cursor < end:
            ranges.append((c.start+(cursor-c.source_in)/c.speed, c.start+c.duration))
    ranges.sort()
    merged = []
    for a, b in ranges:
        if merged and a <= merged[-1][1] + document.ducking.attack + document.ducking.release:
            merged[-1][1] = max(b, merged[-1][1])
        else:
            merged.append([a, b])
    return merged


def volume_expression(document, clip, track, *, original=None, offset=0, intervals=None):
    """Local output seconds, used after atempo and before placing on timeline."""
    original = original or clip
    expression = str(original.gain)
    points = original.envelope
    local = f'(t+{offset})' if offset else 't'
    if points:
        envelope = str(points[-1].value)
        for a, b in reversed(list(zip(points, points[1:]))):
            line = f'{a.value}+({b.value-a.value})*({local}-{a.time})/{b.time-a.time}'
            envelope = f'if(lt({local},{b.time}),{line},{envelope})'
        expression += f'*if(lt({local},{points[0].time}),{points[0].value},{envelope})'
    if original.fade_in:
        expression += f'*min(1,{local}/{original.fade_in})'
    if original.fade_out:
        expression += f'*min(1,max(0,({original.duration}-{local})/{original.fade_out}))'
    duck = document.ducking
    amount = duck.amount if track.role == 'music' else duck.original_amount
    if duck.enabled and track.role in {'music', 'original'} and amount < 1:
        envelopes = ['1']
        for a, b in voice_intervals(document) if intervals is None else intervals:
            local_a, local_b = a - clip.start, b - clip.start
            if local_b + duck.release < 0 or local_a - duck.attack > clip.duration:
                continue
            attack = f'1-(1-{amount})*min(1,max(0,(t-({local_a-duck.attack}))/{duck.attack}))'
            release = f'{amount}+(1-{amount})*min(1,max(0,(t-({local_b}))/{duck.release}))'
            envelopes.append(f'if(lt(t,{local_b}),{attack},{release})')
        while len(envelopes) > 1:
            envelopes = [f'min({envelopes[i]},{envelopes[i+1]})' if i+1 < len(envelopes) else envelopes[i]
                         for i in range(0, len(envelopes), 2)]
        expression += f'*({envelopes[0]})'
    return expression


def audio_windows(document, clips, tracks):
    """Bound ducking expressions for a film-long original/music track."""
    result = []
    for clip in clips:
        track = tracks[clip.track_id]
        amount = document.ducking.amount if track.role == 'music' else document.ducking.original_amount
        if not document.ducking.enabled or track.role not in {'music', 'original'} or amount >= 1:
            result.append(clip)
            continue
        for offset in range(math.ceil(clip.duration / 60)):
            elapsed = offset*60
            result.append(clip.model_copy(update={'start': clip.start+elapsed,
                'source_in': clip.source_in+elapsed*clip.speed, 'duration': min(60, clip.duration-elapsed)}))
    return sorted(result, key=lambda c: c.start)


def range_document(document, start=0, end=None):
    """Clip all layers to an output window without mutating the saved decisions."""
    total = max((c.start + c.duration for c in document.clips), default=0)
    start = round(start * document.fps) / document.fps
    end = total if end is None else round(end * document.fps) / document.fps
    if start < 0 or end > total + .001 or end - start < 1 / document.fps:
        raise ValueError('Vùng xuất cần nằm trong bản dựng và dài ít nhất một khung hình.')
    if start == 0 and end == total:
        return document
    data = document.model_dump(mode='json')
    clips = []
    for original in data['clips']:
        c = deepcopy(original)
        a, b = max(start, c['start']), min(end, c['start'] + c['duration'])
        if b <= a:
            continue
        offset = a - c['start']
        c.update(start=a-start, source_in=round((c['source_in']+offset*c['speed'])*document.fps)/document.fps,
                 duration=round((b-a)*document.fps)/document.fps)
        if c.get('overlay') and c['overlay']['entrance']:
            c['overlay']['entrance_offset'] += offset
        if c['caption']['hop_interval']:
            c['caption']['hop_offset'] += offset
        # Bake cropped fade/envelope values into the new envelope, preserving
        # the sound at the selection boundary rather than restarting a fade.
        points = {0., c['duration']}
        points.update(p['time']-offset for p in original['envelope'] if offset < p['time'] < offset+c['duration'])
        points.update(p-offset for p in (original['fade_in'], original['duration']-original['fade_out'])
                      if offset < p < offset+c['duration'])
        def gain(at):
            t = at + offset
            env = original['envelope']
            value = 1
            if env:
                value = env[0]['value'] if t <= env[0]['time'] else env[-1]['value']
                for p, q in zip(env, env[1:]):
                    if p['time'] <= t <= q['time']:
                        value = p['value']+(q['value']-p['value'])*(t-p['time'])/(q['time']-p['time'])
                        break
            if original['fade_in']:
                value *= min(1, t/original['fade_in'])
            if original['fade_out']:
                value *= min(1, max(0, (original['duration']-t)/original['fade_out']))
            return round(value, 6)
        c['envelope'] = [{'time': p, 'value': gain(p)} for p in sorted(points)] if original['envelope'] or original['fade_in'] or original['fade_out'] else []
        c['fade_in'] = c['fade_out'] = 0
        clips.append(c)
    ids = {c['id'] for c in clips}
    for c in clips:
        if c['anchor'] not in ids:
            c['anchor'] = None
    data['clips'] = clips
    return TimelineDocument.model_validate(data)


def text_position(caption, elapsed=0):
    """Deterministic steps: seeking and range exports retain the same position."""
    step = math.floor((max(0, elapsed) + caption.hop_offset + 1e-7) / caption.hop_interval) % 5 if caption.hop_interval else 0
    return [(caption.x, caption.y), (.2, .15), (.8, .15), (.8, .75), (.2, .75)][step]


def caption_image(text, width, height, caption, output, elapsed=0):
    """Use the same glyph rasterizer for the browser and exported video."""
    import io
    buffer = io.BytesIO()
    style = TextStyle.model_validate({**caption.model_dump(exclude={'x', 'y', 'opacity', 'hop_interval', 'hop_offset', 'layout_size', 'box_width', 'box_height'}),
                                     'size': caption.layout_size or caption.size})
    if caption.box_width is not None:
        bw, bh = max(1, round(width * caption.box_width)), max(1, round(height * caption.box_height))
        cx, cy = text_position(caption, elapsed)
        x, y = max(0, min(width - bw, round(width * cx - bw / 2))), max(0, min(height - bh, round(height * cy - bh / 2)))
        overflow = editing.caption_image(text, width, height, style.model_copy(update={'size': caption.size}), buffer,
                                        text_box=(x, y, bw, bh))
        buffer.seek(0)
        with Image.open(buffer) as rendered:
            result = rendered.convert('RGBA')
            if caption.opacity != 1:
                result.putalpha(result.getchannel('A').point(lambda alpha: round(alpha * caption.opacity)))
            result.save(output, 'PNG')
        return {'x': x / width, 'y': y / height, 'width': bw / width, 'height': bh / height, 'overflow': overflow}
    editing.caption_image(text, width, height, style, buffer)
    buffer.seek(0)
    with Image.open(buffer) as rendered:
        box = rendered.getbbox()
        result = Image.new('RGBA', (width, height))
        bounds = None
        if box:
            cropped = rendered.crop(box)
            if caption.layout_size:
                scale = min(caption.size / caption.layout_size, width / cropped.width, height / cropped.height)
                size = (max(1, min(width, round(cropped.width * scale))),
                        max(1, min(height, round(cropped.height * scale))))
                if size != cropped.size:
                    cropped = cropped.resize(size, Image.Resampling.LANCZOS)
            cx, cy = text_position(caption, elapsed)
            x = max(0, min(width-cropped.width, round(width*cx-cropped.width/2)))
            y = max(0, min(height-cropped.height, round(height*cy-cropped.height/2)))
            if caption.opacity != 1:
                cropped.putalpha(cropped.getchannel('A').point(lambda alpha: round(alpha * caption.opacity)))
            result.alpha_composite(cropped, (x, y))
            bounds = {'x': x/width, 'y': y/height, 'width': cropped.width/width, 'height': cropped.height/height}
        result.save(output, 'PNG')
        return bounds


async def caption_movie(document, work, length, update):
    """One sequential PNG decoder: no thousands of simultaneous inputs."""
    tracks = {t.id: t for t in document.tracks}
    captions = [c for c in document.clips if tracks[c.track_id].kind in {'caption', 'text'} and not tracks[c.track_id].muted
                and c.text.strip() and c.start < length]
    if not captions:
        return None
    boundaries = {0., length, *(x for c in captions for x in (c.start, min(length, c.start+c.duration)))}
    for c in captions:
        interval = c.caption.hop_interval
        if interval:
            first = math.floor(c.caption.hop_offset / interval) + 1
            last = math.ceil((c.caption.hop_offset + c.duration) / interval)
            for step in range(first, last):
                at = math.ceil((c.start + step * interval - c.caption.hop_offset - 1e-7) * document.fps) / document.fps
                if c.start < at < min(length, c.start + c.duration):
                    boundaries.add(at)
    boundaries = sorted(boundaries)
    events = sorted([(c.start, 1, i, c) for i, c in enumerate(captions)] +
                    [(c.start+c.duration, 0, i, c) for i, c in enumerate(captions)], key=lambda e: e[:3])
    cursor, visible = 0, {}
    lines, rendered = [], {}
    for index, (a, b) in enumerate(zip(boundaries, boundaries[1:])):
        while cursor < len(events) and events[cursor][0] <= a+1e-6:
            _, entering, order, clip = events[cursor]
            if entering:
                visible[order] = clip
            else:
                visible.pop(order, None)
            cursor += 1
        active = [visible[i] for i in sorted(visible)]
        signature = tuple((i, text_position(visible[i].caption, a-visible[i].start)) for i in sorted(visible))
        path = rendered.get(signature, work / f'caption{index}.png')
        def draw():
            composite = Image.new('RGBA', (document.width, document.height))
            for c in active:
                import io
                buffer = io.BytesIO()
                caption_image(c.text, document.width, document.height, c.caption, buffer, elapsed=a-c.start)
                buffer.seek(0)
                with Image.open(buffer) as layer:
                    composite.alpha_composite(layer)
            composite.save(path, 'PNG')
        if signature not in rendered:
            await asyncio.to_thread(draw)
            rendered[signature] = path
        lines.extend([f"file '{path.name}'", f'option framerate {document.fps}', f'duration {b-a:.9f}'])
        if index % 20 == 0:
            update(stage=f'Vẽ chữ và phụ đề · {index+1}/{len(boundaries)-1}', progress=80+round(5*(index+1)/(len(boundaries)-1)))
    lines.extend([f"file '{path.name}'", f'option framerate {document.fps}'])
    manifest = work / 'captions.txt'; manifest.write_text('\n'.join(lines))
    output = work / 'captions.mov'
    # Only server-generated UUID paths are written above; safe=0 permits the
    # per-file frame-rate option needed for frame-accurate PNG durations.
    def progress(line):
        if line.startswith('out_time_us='):
            try:
                seconds = float(line.split('=', 1)[1]) / 1e6
            except (ValueError, OverflowError):
                return  # FFmpeg may emit N/A until the first output frame.
            if math.isfinite(seconds):
                update(stage='Đồng bộ chữ và phụ đề', progress=85+round(5*min(1,max(0,seconds)/length)))
    await media_jobs.process(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(manifest),
        '-vf', f'fps={document.fps}', '-t', str(length), '-c:v', 'qtrle', '-pix_fmt', 'argb',
        '-progress', 'pipe:1', str(output)], timeout=max(180, length*10), on_line=progress)
    return output


async def collapse_stems(stems, work, update):
    """Keep the final mixer below 16 inputs, even with thousands of voice cues."""
    level = 0
    while len(stems) > 16:
        reduced = []
        for index in range(0, len(stems), 16):
            group = stems[index:index+16]
            if len(group) == 1:
                reduced.extend(group)
                continue
            begin = min(a for _, a, _ in group)
            span = max(a+d for _, a, d in group)-begin
            args = ['ffmpeg', '-v', 'error', '-y']
            graph = []
            for i, (path, start, _) in enumerate(group):
                args += ['-i', str(path)]
                graph.append(f'[{i}:a]adelay={round((start-begin)*48000)}S:all=1[a{i}]')
            graph.append(''.join(f'[a{i}]' for i in range(len(group))) + f'amix=inputs={len(group)}:normalize=0[out]')
            target = work / f'mix{level}-{index}.wav'
            update(stage='Gom các lớp âm thanh cho video dài', progress=80)
            await media_jobs.process(args + ['-filter_complex', ';'.join(graph), '-map', '[out]', '-t', str(span),
                '-c:a', 'pcm_s16le', str(target)], timeout=max(180, span*10))
            reduced.append((target, begin, span))
        stems = reduced
        level += 1
    return stems


def effect_settings(document, length):
    tracks = {t.id: t for t in document.tracks}
    clips = [c for c in document.clips if tracks[c.track_id].kind == 'effect' and not tracks[c.track_id].muted and c.start < length]
    regions = [c.effect.model_copy(update={'start': c.start, 'end': min(length, c.start+c.duration)}) for c in clips if c.effect]
    # The existing editor has an eight-region limit. Timeline regions are
    # processed in batches so it retains that validation and pixel geometry.
    return clips, regions


def silence_ranges(peaks, interval, threshold=-40, minimum=.35):
    limit, begin, ranges = 10**(threshold/20), None, []
    for index in range(len(peaks)+1):
        quiet = index < len(peaks) and peaks[index] < limit
        if quiet and begin is None:
            begin = index
        elif not quiet and begin is not None:
            if (index-begin)*interval >= minimum:
                ranges.append({'start': round(begin*interval, 4), 'end': round(index*interval, 4)})
            begin = None
    return ranges
