"""Map existing AI results onto edit decisions and hand exports to postproduction."""
import asyncio
import math
import shutil
import uuid
from pathlib import Path

from agent.db import crud
from agent.config import OUTPUT_DIR
from agent.models.timeline import Clip, Track, TimelineDocument
from agent.services import editing, imported_video, media_jobs, pacing, speech, speech_store, timeline, timeline_layers
from agent.services.music_library import resolve_music_file
from agent.services.tts import narration_matches_text, narration_path_for_scene
from agent.utils.slugify import slugify

_handoff_lock = asyncio.Lock()


def frame(time, fps):
    return round(time*fps)/fps


async def legacy_audio(vid):
    item = await crud.get_video(vid)
    project = await crud.get_project(item['project_id'])
    directory = OUTPUT_DIR / slugify(project['name'])
    return directory, speech_store.read_json(directory / 'audio_mix' / f'{vid}.json', {})


async def has_legacy(vid):
    _, audio = await legacy_audio(vid)
    return editing.load(vid)['revision'] > 0 or bool(audio) or pacing.load(vid)['revision'] > 0


async def snapshot_narration(vid, path):
    """Scene TTS files are overwritten on regeneration; editor assets cannot be."""
    target = timeline.directory(vid)/'voices'/f'{pacing.fingerprint(path)}.wav'
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f'.{uuid.uuid4()}.tmp')
        try:
            await asyncio.to_thread(shutil.copyfile, path, temporary)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return target


def scene_source(vid, clip, asset):
    """Map restored pixels back to their immutable source without changing the edit."""
    seen, source_in = set(), clip.source_in
    while asset['ref'].startswith('propainter:'):
        if asset['id'] in seen:
            raise ValueError('Liên kết cảnh ProPainter bị vòng lặp; chưa nạp kết quả AI.')
        seen.add(asset['id'])
        rid = speech_store.identifier(asset['ref'].split(':', 1)[1])
        record = speech_store.read_json(editing.directory(asset['owner'])/'propainter'/f'{rid}.json', {})
        if not isinstance(record, dict) or not isinstance(record.get('targets'), list) or any(not isinstance(t, dict) for t in record['targets']):
            raise ValueError('Thiếu liên kết cảnh gốc của ProPainter; chưa nạp kết quả AI.')
        parents = {t.get('asset_id') for t in record['targets']}
        start = record.get('start')
        if (not record.get('full_resolution') or record.get('trial_id') != rid or len(parents) != 1 or None in parents
                or not isinstance(start, (float, int)) or isinstance(start, bool) or not math.isfinite(start) or start < 0):
            raise ValueError('Thiếu liên kết cảnh gốc của ProPainter; chưa nạp kết quả AI.')
        parent = timeline.asset(vid, parents.pop())
        if parent['fingerprint'] != record.get('source_fingerprint'):
            raise ValueError('Nguồn ProPainter đã thay đổi; chưa nạp kết quả AI.')
        source_in += start
        asset = parent
    if source_in + clip.duration * clip.speed > asset['duration'] + .05:
        raise ValueError('Mốc cảnh ProPainter vượt nguồn gốc; chưa nạp kết quả AI.')
    return clip.model_copy(update={'source_in': source_in}), asset


async def import_results(vid, document, *, captions=True, voices=True, effects=True, legacy=False, source='auto', report=None):
    data = document.model_dump(mode='json')
    assets = timeline.validate_assets(vid, document)
    tracks = {t.id: t for t in document.tracks}
    picture = [c for c in document.clips if tracks[c.track_id].kind == 'video']
    warnings, added = [], []
    report = report if report is not None else {}
    report.update(voices=0, fitted=0, blocked=[])
    legacy_settings = editing.load(vid)['settings'] if legacy else None
    if legacy_settings and not editing.load(vid)['revision']:
        _, audio = await legacy_audio(vid)
        narration = audio.get('narration') or {}
        if narration:
            legacy_settings = {**legacy_settings, 'source_volume': narration.get('source_volume', 1),
                'narration_volume': narration.get('narration_volume', 1),
                'mode': 'subtitles' if narration.get('captions_only') else 'voice_subtitles' if narration.get('burn_subtitles') else 'voice',
                'voice_source': 'speech' if narration.get('speech_revision') is not None else 'scenes'}
        elif pacing.load(vid)['revision']:
            legacy_settings = {**legacy_settings, 'source_volume': pacing.load(vid)['draft']['base_volume']}
    if legacy_settings and source == 'auto':
        source = legacy_settings['voice_source']
    if legacy_settings:
        if document.legacy_imported:
            raise ValueError('Thiết lập cũ đã nạp. Dùng hoàn tác để quay lại trước lần nạp nếu cần thử lại.')
        if any(t.locked for t in document.tracks):
            raise ValueError('Mở khóa các lớp trước khi nạp thiết lập hậu kỳ cũ.')
        data['frame'] = legacy_settings['frame']
        data['legacy_imported'] = True
        for clip in data['clips']:
            t = tracks[uuid.UUID(clip['track_id'])]
            # Early drafts used role=original for the mixed SFX track too.
            # The old source-volume setting applies only to extracted video
            # sound, never to music/SFX the user already placed by hand.
            if t.kind == 'audio' and t.purpose != 'overlay' and t.role == 'original' and assets[clip['asset_id']]['kind'] == 'video':
                clip['gain'] = legacy_settings['source_volume']
    def track(kind, role, name):
        existing = next((t for t in data['tracks'] if t['kind'] == kind and t.get('purpose') != 'overlay' and t.get('role', 'original') == role), None)
        if existing:
            if existing['locked']:
                raise ValueError(f"Mở khóa lớp {existing['name']} trước khi nạp kết quả.")
            return existing['id']
        value = Track(id=uuid.uuid4(), kind=kind, role=role, name=name).model_dump(mode='json')
        data['tracks'].append(value)
        return value['id']
    origins = {c.origin: c for c in document.clips if c.origin}
    replacements, wanted, synchronized, story_captions = {}, set(), set(), set()
    sources = {}
    edited_script = speech_store.load_document(vid) if source != 'scenes' else None
    output_timing = bool(edited_script and edited_script.edit_signature)
    if output_timing and edited_script.edit_signature != timeline.timing_key(document):
        raise ValueError('Bản dựng đã đổi mốc sau khi nhận dạng. Nhận dạng lại trước khi nạp phụ đề/giọng.')
    def merge(value):
        wanted.add(value.origin)
        previous = origins.get(value.origin)
        if not previous:
            added.append(value)
            return
        if tracks[previous.track_id].locked:
            raise ValueError('Mở khóa lớp có kết quả AI đã nạp trước khi cập nhật.')
        if value.text:
            updated = previous.model_copy(update={'text': value.text, 'start': value.start,
                'duration': value.duration, 'origin': value.origin})
        elif value.asset_id:
            ratio = value.duration/previous.duration
            updated = previous.model_copy(update={'asset_id': value.asset_id, 'source_in': value.source_in,
                'duration': value.duration, 'speed': value.speed, 'start': value.start,
                'fade_in': previous.fade_in*ratio, 'fade_out': previous.fade_out*ratio,
                'envelope': [p.model_copy(update={'time': p.time*ratio}) for p in previous.envelope]})
        else:
            return  # Keep regions/colors edited by hand in the timeline.
        if updated != previous:
            replacements[previous.id] = updated
    for c in picture:
        a = assets[str(c.asset_id)]
        if not output_timing and a['ref'].startswith('propainter:'):
            try:
                c, a = scene_source(vid, c, a)
            except (ValueError, OSError, TypeError, KeyError) as exc:
                warnings.append(f"{a['name']}: {exc}")
                continue
        # Only original sources and immutable Studio scene renders describe AI
        # timing. Arbitrary mixed exports still need their own script.
        scene_id = a['ref'].split(':')[1] if a['ref'].startswith('stickman:') else None
        if a['ref'] != 'original' and not scene_id and not output_timing:
            continue
        owner = vid if output_timing else a['owner']
        if owner not in sources:
            sources[owner] = (speech_store.load_document(owner), await imported_video.timeline(owner),
                speech_store.read_json(media_jobs.directory(owner)/'stickman'/'plan.json', {}))
        script, scenes, plan = sources[owner]
        scene = next((s for s in scenes if str(s['id']) == scene_id), None) if scene_id and not output_timing else None
        if scene_id and not scene and not output_timing:
            warnings.append(f"{a['name']}: cảnh nguồn không còn; chưa nạp kết quả AI.")
            continue
        if source != 'scenes' and script.cues and script.timeline != speech_store.timeline_key(scenes):
            warnings.append(f"{a['name']}: kịch bản có mốc cũ; chưa nạp phụ đề/giọng.")
            continue
        if scene and source != 'scenes' and script.cues and abs(a['duration']-float(scene.get('duration') or 8)) > 1/document.fps+.001:
            warnings.append(f"{a['name']}: bản cảnh có thời lượng cũ; chưa nạp kịch bản SRT mới.")
            continue
        synchronized.add(str(c.id))
        offset = 0
        if scene:
            for s in scenes:
                if str(s['id']) == scene_id:
                    break
                offset += float(s.get('duration') or 8)
        source_cues = [(str(q.id), q.start, q.end, q.text, q) for q in script.cues] if source != 'scenes' else []
        if not source_cues and source != 'speech':
            cursor = offset if scene else 0
            for s in [scene] if scene else scenes:
                span = a['duration'] if scene else float(s.get('duration') or 8)
                if (s.get('narrator_text') or '').strip():
                    source_cues.append((s['id'], cursor, cursor+span, s['narrator_text'], s))
                cursor += span
        if scene and captions and source_cues:
            from agent.services.stickman import caption_parts
            story_captions.add(str(c.id))
            # Old Studio drafts did not tag generated captions. Adopt only the
            # original phrases linked to this scene, keeping manual text intact.
            planned = next((p for s, p in zip(scenes, plan.get('scenes', [])) if str(s['id']) == scene_id), {})
            for index, (_, _, text) in enumerate(caption_parts(planned.get('narration', ''), a['duration'])):
                origin = f'caption:{c.id}:{scene_id}' + (f':{index}' if index else '')
                if origin not in origins:
                    previous = next((q for q in document.clips if not q.origin and q.anchor == c.id
                        and tracks[q.track_id].kind == 'caption' and q.text == text), None)
                    if previous:
                        origins[origin] = previous
        timing_speed = 1. if output_timing else c.speed
        source_start = c.start if output_timing else offset+c.source_in
        source_stop = source_start+c.duration*timing_speed
        for cid, begin, end, text, cue in source_cues:
            left, right = max(begin, source_start), min(end, source_stop)
            if right <= left:
                continue
            start = frame(c.start+(left-source_start)/timing_speed, document.fps)
            stop = frame(c.start+(right-source_start)/timing_speed, document.fps)
            if stop <= start:
                continue
            asset, path, voice_rate, blocked = None, None, 1., False
            if voices and cue:
                if isinstance(cue, dict):
                    item = await crud.get_video(owner)
                    project = await crud.get_project(item['project_id'])
                    directory = OUTPUT_DIR / slugify(project['name']) / 'tts'
                    path = narration_path_for_scene(directory, cue)
                    if not path or not path.resolve().is_relative_to(directory.resolve()) or not narration_matches_text(path, text):
                        warnings.append(f"Cảnh {cid[:8]}: chưa có giọng khớp lời đã lưu.")
                        path = None
                    else:
                        path = await snapshot_narration(vid, path)
                else:
                    status = speech.cue_status(owner, cue)
                    if status['status'] != 'READY':
                        warnings.append(f"Câu {cid[:8]}: chưa có giọng hợp lệ ({status['status']}).")
                    else:
                        path, _, _, _ = speech.cue_audio(owner, cue)
                if path:
                    asset = await timeline.register(vid, owner, f'voice:{cid}', text[:60], path, 'voice')
                if asset and asset['duration'] > end-begin:
                    slot = frame(end-begin, document.fps) if isinstance(cue, dict) else end-begin
                    rate = max(1., asset['duration'] / slot)
                    # Fit small scene overages with playback speed; the immutable
                    # WAV remains intact for undo and future timing changes.
                    if isinstance(cue, dict) and rate <= 1.15 and timing_speed*rate <= 2:
                        voice_rate = rate
                        report['fitted'] += 1
                    elif asset['duration'] > end-begin+.04:
                        wanted.add(f'voice:{c.id}:{cid}')
                        label = f'Cảnh {scenes.index(cue)+1}' if isinstance(cue, dict) else f'Câu {cid[:8]}'
                        warnings.append(f"{label}: giọng mới {asset['duration']:.2f}s / khung {end-begin:.2f}s; giữ lời đọc cũ nếu có. Kéo dài cảnh hoặc rút gọn lời bằng AI rồi tạo lại.")
                        report['blocked'].append({'scene_id': cid, 'name': label, 'duration': asset['duration'], 'frame': end-begin})
                        blocked = True
                        asset = None
            origin = f'caption:{c.id}:{cid}'
            if blocked and f'voice:{c.id}:{cid}' in origins:
                wanted.update(key for key in origins if key == origin or key.startswith(origin+':'))
            if captions and not (blocked and f'voice:{c.id}:{cid}' in origins):
                caption = {}
                if legacy_settings:
                    style = legacy_settings['captions']
                    caption = {**style, 'y': {'top': style['margin']/100, 'middle': .5, 'bottom': 1-style['margin']/100}[style['position']]}
                # Studio phrases follow the regenerated voice, while SRT cues
                # retain their explicitly saved timing.
                length = min(end-begin, asset['duration']/voice_rate) if asset else end-begin
                parts = caption_parts(text, length, document.fps) if scene and isinstance(cue, dict) else [(0, end-begin, text)]
                for index, (part_start, span, part_text) in enumerate(parts):
                    part_left, part_right = max(left, begin+part_start), min(right, begin+part_start+span)
                    part_start = frame(c.start+(part_left-source_start)/timing_speed, document.fps)
                    part_stop = frame(c.start+(part_right-source_start)/timing_speed, document.fps)
                    if part_stop > part_start:
                        merge(Clip(id=uuid.uuid4(), track_id=track('caption', 'original', 'Phụ đề'), text=part_text,
                            caption=caption, origin=origin+(f':{index}' if index else ''),
                            start=part_start, duration=part_stop-part_start, anchor=c.id))
            origin = f'voice:{c.id}:{cid}'
            if asset:
                source_in = math.floor(max(0, left-begin)*voice_rate*document.fps+1e-6)/document.fps
                voice_speed = timing_speed*voice_rate
                whole_scene_voice = isinstance(cue, dict) and left == begin and right == end
                span = min(stop-start if whole_scene_voice else (right-left)/timing_speed,
                           (asset['duration']-source_in)/voice_speed)
                span = math.floor(span*document.fps+1e-6)/document.fps
                if span > 0:
                    if whole_scene_voice and asset['duration']/span <= 2:
                        # PCM length rarely lands on a video frame. Consume the
                        # complete sentence in the quantized clip, including its tail.
                        voice_speed = asset['duration']/span
                    report['voices'] += 1
                    merge(Clip(id=uuid.uuid4(), track_id=track('audio', 'voice', 'Giọng đọc'), asset_id=asset['id'],
                        start=start, duration=span, source_in=source_in, speed=voice_speed, anchor=c.id, origin=origin))
        if effects and not output_timing:
            settings = editing.load(owner)['settings']
            from agent.models.editing import ColorSettings, EditRegion
            color = ColorSettings.model_validate(settings['colors'])
            origin = f'colors:{c.id}'
            if color != ColorSettings() and origin not in origins:
                added.append(Clip(id=uuid.uuid4(), track_id=track('effect', 'original', 'Hiệu ứng hình'),
                    start=c.start, duration=c.duration, colors=color, anchor=c.id, origin=origin))
            for r in settings['regions']:
                left, right = max(r['start'], source_start), min(r['end'], source_stop)
                origin = f"effect:{c.id}:{r['id']}"
                if right <= left or origin in origins:
                    continue
                r = dict(r)
                if r['image_id'] and owner != vid:
                    image_source = editing.asset_path(owner, r['image_id'])
                    target = editing.asset_path(vid, r['image_id'])
                    if not image_source.is_file():
                        warnings.append('Ảnh/logo nguồn không còn.'); continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    await asyncio.to_thread(shutil.copyfile, image_source, target)
                start = frame(c.start+(left-source_start)/c.speed, document.fps)
                stop = frame(c.start+(right-source_start)/c.speed, document.fps)
                if stop > start:
                    added.append(Clip(id=uuid.uuid4(), track_id=track('effect', 'original', 'Hiệu ứng hình'),
                        start=start, duration=stop-start, anchor=c.id, effect=EditRegion.model_validate(r), origin=origin))
    removed = set()
    for origin, c in origins.items():
        if origin.startswith('caption:') and str(c.anchor) in story_captions and origin not in wanted:
            if tracks[c.track_id].locked:
                raise ValueError('Mở khóa lớp có phụ đề AI cũ trước khi đồng bộ.')
            removed.add(c.id)
    if source != 'auto':
        for c in document.clips:
            managed = any(c.origin.startswith(f'{kind}:{pid}:') for pid in synchronized
                for kind, enabled in (('caption', captions), ('voice', voices)) if enabled)
            if managed and c.origin not in wanted:
                if tracks[c.track_id].locked:
                    raise ValueError('Mở khóa lớp có kết quả AI cũ trước khi đồng bộ.')
                removed.add(c.id)
    data['clips'] = [replacements[uuid.UUID(c['id'])].model_dump(mode='json') if uuid.UUID(c['id']) in replacements else c for c in data['clips'] if uuid.UUID(c['id']) not in removed]
    data['clips'].extend(c.model_dump(mode='json') for c in added)
    if legacy_settings:
        for t in data['tracks']:
            if t['kind'] == 'caption':
                t['muted'] = legacy_settings['mode'] in {'original', 'voice'}
                style = legacy_settings['captions']
                for c in data['clips']:
                    if c['track_id'] == t['id']:
                        c['caption'] = {**c['caption'], **style, 'y': {'top': style['margin']/100, 'middle': .5, 'bottom': 1-style['margin']/100}[style['position']]}
            elif t['kind'] == 'audio' and t.get('purpose') != 'overlay' and t['role'] == 'voice':
                t['muted'] = legacy_settings['mode'] in {'original', 'subtitles'}
                for c in data['clips']:
                    if c['track_id'] == t['id']:
                        c['gain'] = legacy_settings['narration_volume']
    result = TimelineDocument.model_validate(data)
    if legacy:
        result, extra_warnings, count = await import_legacy_audio_pacing(vid, result, assets, legacy_settings)
        warnings.extend(extra_warnings)
    else:
        count = 0
    timeline.validate_assets(vid, result)
    return result.model_dump(mode='json'), list(dict.fromkeys(warnings)), len(added)+len(replacements)+len(removed)+count+int(legacy)


async def import_legacy_audio_pacing(vid, document, assets, settings):
    """Migrate saved old audio decisions once, without reapplying old cuts."""
    warnings, count = [], 0
    draft = pacing.load(vid)['draft']
    pictures = [c for c in document.clips if next(t for t in document.tracks if t.id == c.track_id).kind == 'video']
    c = pictures[0] if pictures else None
    a = assets.get(str(c.asset_id)) if c else None
    untouched = c and len(pictures) == 1 and c.start == c.source_in == 0 and c.speed == 1 and a['owner'] == vid and a['ref'] == 'original' and abs(c.duration-a['duration']) < .05
    use_pacing = draft['source'] == 'original' and (untouched or not draft['cut_ids'])
    if draft['cut_ids']:
        if not untouched or draft['source'] != 'original':
            warnings.append('Bản dựng đã cắt hoặc nhịp dựng cũ dùng bản xuất: giữ mốc hiện tại, chưa nạp các khoảng cắt cũ.')
            use_pacing = False
        else:
            from agent.models.pacing import PacingDraft
            try:
                _, cuts = pacing.selected_plan(vid, PacingDraft.model_validate(draft), Path(a['path']))
                chunks, offset = [], 0
                for kept in pacing.keep_ranges(c.duration, cuts, document.fps):
                    start, end = kept['start'], kept['end']
                    part = timeline_layers.range_document(document, start, end).model_dump(mode='json')
                    ids = {q['id']: str(uuid.uuid4()) for q in part['clips']}
                    groups = {q['group']: str(uuid.uuid4()) for q in part['clips'] if q['group']}
                    for q in part['clips']:
                        if q['anchor']:
                            q['origin'] = q['origin'].replace(q['anchor'], ids[q['anchor']])
                            q['anchor'] = ids[q['anchor']]
                        q['id'] = ids[q['id']]; q['group'] = groups.get(q['group'])
                        q['start'] = frame(q['start']+offset, document.fps)
                    chunks.extend(part['clips']); offset += end-start
                document = TimelineDocument.model_validate({**document.model_dump(mode='json'), 'clips': chunks})
                count += len(cuts)
            except (ValueError, OSError) as exc:
                warnings.append(f'Chưa nạp khoảng cắt cũ: {exc}')
                use_pacing = False
    data = document.model_dump(mode='json')
    def add_sound(asset, start, gain, role, name, origin):
        nonlocal count
        existing = next((q for q in data['tracks'] if q['kind'] == 'audio' and q.get('purpose') != 'overlay' and q['role'] == role), None)
        if not existing:
            existing = Track(id=uuid.uuid4(), kind='audio', role=role, name=name).model_dump(mode='json')
            data['tracks'].append(existing)
        span = int(min(asset['duration'], timeline.duration(document)-start)*document.fps)/document.fps
        if span <= 0:
            warnings.append(f'{name}: nằm ngoài bản dựng, chưa nạp.'); return
        data['clips'].append(Clip(id=uuid.uuid4(), asset_id=asset['id'], track_id=existing['id'],
            start=frame(start, document.fps), duration=span, gain=gain, origin=origin).model_dump(mode='json'))
        count += 1
    if use_pacing:
        for event in draft['effects']:
            try:
                info = pacing.asset_info(vid, event['asset_id'])
                asset = await timeline.register(vid, vid, event['asset_id'], info['name'], pacing.asset_path(vid, event['asset_id']), 'sfx')
                add_sound(asset, event['start'], event['volume'], 'effects', 'Hiệu ứng âm thanh', f"legacy-sfx:{event['id']}")
            except (ValueError, OSError) as exc:
                warnings.append(f'Hiệu ứng cũ chưa nạp: {exc}')
    directory, audio = await legacy_audio(vid)
    music = audio.get('music')
    if music and settings['use_music']:
        try:
            path = resolve_music_file(directory / 'music', music['filename'])
            asset = await timeline.register(vid, vid, f"music:{music['filename']}", music['filename'], path, 'music')
            if any(q['asset_id'] == asset['id'] for q in data['clips']):
                warnings.append('Nhạc đã có trên timeline: giữ lớp nhạc hiện tại, không thêm bản trùng.')
            else:
                add_sound(asset, 0, music.get('volume', .25), 'music', 'Nhạc nền', f'legacy-music:{vid}')
        except (ValueError, OSError, KeyError) as exc:
            warnings.append(f'Nhạc đã chọn trước đây chưa nạp: {exc}')
    return TimelineDocument.model_validate(data), warnings, count


async def handoff(vid, export_id):
    async with _handoff_lock:
        item = await crud.get_video(vid)
        exports = editing.load(vid)['exports']
        result = next((e for e in exports if e.get('export_id') == export_id and e.get('operation') == 'timeline'), None)
        if not result:
            raise ValueError('Chọn bản dựng timeline đã xuất hoàn tất.')
        if result.get('range_start', 0) or result.get('range_end') is not None:
            raise ValueError('Dùng bản xuất toàn bộ để chuyển sang hậu kỳ.')
        existing = result.get('handoff_video_id')
        if existing and await crud.get_video(existing) and imported_video.source_path(existing).is_file():
            return {'video_id': existing}
        source = editing.directory(vid) / 'exports' / f'{export_id}.mp4'
        if not source.is_file():
            raise ValueError('File bản xuất đã bị xóa.')
        info = await imported_video.probe(source)
        rows = await crud.list_videos(item['project_id'])
        created = await crud.create_video(item['project_id'], title=f"{item['title'][:65]} · bản dựng {result['revision']}",
            description=f'Bản dựng từ {vid}, phiên bản {export_id}', orientation=info['orientation'],
            display_order=max((r['display_order'] for r in rows), default=-1)+1)
        key = created['id']; target = imported_video.source_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if shutil.disk_usage(target.parent).free < source.stat().st_size + 256*1024**2:
                raise ValueError('Không đủ dung lượng để lưu nguồn hậu kỳ.')
            await asyncio.to_thread(shutil.copyfile, source, target)
            speech_store.atomic_json(media_jobs.directory(key) / 'source.json', {**info, 'status': 'READY',
                'source_id': str(uuid.uuid4()), 'filename': source.name, 'origin_video_id': vid,
                'origin_export_id': export_id, 'edit_decisions': result['timeline']})
            prefix = 'vertical' if info['orientation'] == 'VERTICAL' else 'horizontal'
            await crud.update_video(key, status='COMPLETED', duration=info['duration'], **{f'{prefix}_url': f'/api/videos/{key}/file'})
            result['handoff_video_id'] = key
            editing.record_export(vid, result)
            return {'video_id': key}
        except BaseException:
            shutil.rmtree(media_jobs.directory(key), ignore_errors=True)
            await crud.delete_video(key)
            raise
