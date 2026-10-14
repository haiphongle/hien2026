"""Selective scene production. Immutable renders keep old edits/exports playable."""
import json
import math
import sys
import uuid
from pathlib import Path

from agent.db import crud
from agent.services import media_jobs, pacing, speech_store
from agent.services import stickman_direction as direction


def image_signature(body, scene):
    from agent.services.stickman import digest, PRESETS
    parts = [body.style, body.orientation, str(body.character_id), str(body.second_character_id), body.dialogue, scene.action]
    rich = body.story_assets or scene.extras or scene.shot_kind != 'story' or any(c.role not in {'A', 'B'} for c in body.characters)
    if rich:
        parts = ['story-world-v1', body.style, body.orientation, scene.action, scene.camera,
                 [(role, str(direction.selected_reference(body, role)), direction.reference_tag(body, role)) for role in direction.cast(body, scene)],
                 str(body.style_profile_id)]
    elif body.style_profile_id or body.characters or scene.cast is not None or scene.camera:
        parts += [str(body.style_profile_id), [c.model_dump(mode='json', exclude_defaults=True, exclude={'reference_status', 'reference_note'}) for c in body.characters], scene.cast, scene.camera]
    if scene.assets or scene.extras or scene.shot_kind != 'story':
        parts += [scene.assets, scene.extras, scene.shot_kind,
                  [a.model_dump(mode='json') for a in direction.assets(body) if a.key in scene.assets]]
    locks = body.visual_locks.model_dump(exclude_defaults=True, exclude={'context'})
    if locks:
        parts.append(locks)
    version = next(p for p in PRESETS if p['id'] == body.style).get('reference_version')
    if version and (not body.character_id or body.dialogue and not body.second_character_id):
        parts.append(version)
    context = scene.model_dump(include={'environment', 'staging', 'lighting', 'continuity'}, exclude_defaults=True)
    analysis = body.analysis.model_dump(exclude_defaults=True)
    if context or analysis:
        parts += [context, analysis]
    return digest(parts)


def video_prompt(scene, body=None, pid=None):
    if scene.motion_beats:
        motion = (' '.join(f'{start}-{end}s: {action}' for (start, end), action in zip([(0, 3), (3, 6), (6, 8)], scene.motion_beats))
                  + f' {scene.camera} Preserve the starting frame visual style and character identity. No spoken dialogue, no written text.')
    else:
        motion = (f'0-3s: {scene.action} 3-6s: Continue the same action smoothly, with a {scene.emotion} reaction and '
            f'{scene.gesture} gesture. 6-8s: Settle into a stable ending pose. '
            f'{scene.camera or "Steady camera"} No spoken dialogue, no written text.')
    if body is None:
        return motion
    style = direction.material(body, pid)
    visible = direction.cast(body, scene)
    exact_cast = f'{len(visible)} characters: ' + (', '.join(f'Character {r}' for r in visible) or 'none; objects/environment only')
    if scene.extras:
        exact_cast += f'. Background people: {scene.extras}. Preserve everyone already visible in the approved frame.'
    return direction.sections(('VIDEO ANIMATION PROMPT', 'Animate the approved storyboard starting frame.'),
        ('DURATION AND ASPECT RATIO', '8 seconds; ' + ('9:16' if body.orientation == 'VERTICAL' else '16:9')),
        ('VISUAL STYLE AND ANATOMY', style['scene_prefix']),
        ('EXACT VISIBLE CAST', exact_cast),
        *direction.shot_context(body, scene), ('TIMED ACTION AND CAMERA', motion),
        ('MOTION AND CONSISTENCY', 'Smooth readable acting with connected limbs and stable proportions. '
         'Preserve the approved identities, hair, clothing, screen direction, background and props. '
         'No new characters, identity morphing, gender swaps, flicker or style transformation.'),
        ('AUDIO', 'No spoken dialogue or narration; voice is added separately in postproduction.'),
        ('NEGATIVE PROMPT', style['negative_prompt']))


def preview_versions(vid, body, scenes):
    """Only first-shot variants compatible with the current reference/shot plan."""
    if not scenes or not body.preview_first or body.motion == 'talk':
        return []
    from agent.services import stickman
    prefix = 'vertical' if body.orientation == 'VERTICAL' else 'horizontal'
    scene = scenes[0]
    signature = image_signature(body, body.scenes[0])
    items = speech_store.read_json(stickman.directory(vid)/'preview_versions.json', [])
    current = scene.get(f'{prefix}_image_media_id')
    # Existing previews remain selectable after this upgrade.
    if current and not any(v['media_id'] == current for v in items):
        items.append({'media_id': current, 'signature': signature, 'scene_id': scene['id'],
                      'source_url': scene.get(f'{prefix}_image_url')})
    return [{**item, 'url': f"/api/videos/{vid}/stickman/preview/{item['media_id']}", 'selected': item['media_id'] == current}
            for item in items if item['signature'] == signature and item['scene_id'] == scene['id']
            and (stickman.directory(vid)/f"{item['media_id']}.image").is_file()][-8:]


def record_preview(vid, body, scene):
    from agent.services import stickman
    root = stickman.directory(vid)
    prefix = 'vertical' if body.orientation == 'VERTICAL' else 'horizontal'
    ident = str(uuid.UUID(scene[f'{prefix}_image_media_id']))
    items = speech_store.read_json(root/'preview_versions.json', [])
    if not any(item['media_id'] == ident for item in items):
        items.append({'media_id': ident, 'signature': image_signature(body, body.scenes[0]),
                      'scene_id': scene['id'], 'source_url': scene.get(f'{prefix}_image_url'),
                      'prompt': scene.get('image_prompt'), 'style': body.style, 'orientation': body.orientation})
        speech_store.atomic_json(root/'preview_versions.json', items[-8:])


async def estimate(pid, body, vid=None):
    previous = speech_store.read_json(media_jobs.directory(vid)/'stickman'/'plan.json') if vid else None
    from agent.models.stickman import CreateRequest
    old = CreateRequest.model_validate(previous) if previous else None
    refs = await crud.get_project_characters(pid)
    reference_count = 0
    if body.motion != 'talk':
        for selected, _, description, _, _ in direction.reference_specs(body, pid).values():
            existing = next((c for c in refs if c['id'] == str(selected)), None) if selected else next((c for c in refs if c.get('description') == description), None)
            reference_count += not bool(existing and existing.get('media_id'))
    rows = await crud.list_scenes(vid) if vid else []
    generation = speech_store.read_json(media_jobs.directory(vid)/'stickman'/'generation.json', {}) if vid else {}
    prefix = 'vertical' if body.orientation == 'VERTICAL' else 'horizontal'
    images, videos = 0, 0
    for i, scene in enumerate(body.scenes):
        row = rows[i] if i < len(rows) else {}
        changed = not old or i >= len(old.scenes) or image_signature(body, scene) != image_signature(old, old.scenes[i])
        signature = generation.get(row.get('id'), {}).get('image')
        changed = changed or bool(signature and signature != image_signature(body, scene))
        images += body.motion != 'talk' and (changed or not row.get(f'{prefix}_image_media_id') or row.get(f'{prefix}_image_status') != 'COMPLETED')
        videos += body.motion == 'ai' and (changed or not old or old.motion != 'ai' or not row.get(f'{prefix}_video_media_id') or video_prompt(scene, body, pid) != video_prompt(old.scenes[i], old, pid))
    return {'reference_images': int(reference_count), 'scene_images': images, 'flow_clips': videos,
            'local_clips': len(body.scenes), 'reused_scenes': len(body.scenes)-images if old and body.motion != 'talk' else 0}


async def prepare(vid, body, project, scenes, voices, update):
    from agent.services import stickman as studio
    root = studio.directory(vid)
    generation = speech_store.read_json(root/'generation.json', {})
    prefix = 'vertical' if body.orientation == 'VERTICAL' else 'horizontal'
    if body.motion != 'talk':
        specs = direction.reference_specs(body, project['id'])
        refs = {key: await studio.reference(project, body, key) for key in specs}
        for orientation in ('VERTICAL', 'HORIZONTAL'):
            missing = [r['id'] for key, r in refs.items() if not r.get('media_id')
                       and ('VERTICAL' if specs[key][-1] == 'character' else 'HORIZONTAL') == orientation]
            if missing:
                await studio.generate_images(vid, project['id'], missing, 'GENERATE_CHARACTER_IMAGE', orientation, update, 25, 5)
        refs = {key: await crud.get_character(r['id']) for key, r in refs.items()}
        for char in refs.values():
            uuid.UUID(char.get('media_id') or '')
        speech_store.atomic_json(root/'reference.json', {key: r['id'] for key, r in refs.items()})
        if body.style_profile_id:
            ref = await crud.get_character(direction.profile(project['id'], body.style_profile_id)['reference_id'])
            if not ref or not ref.get('media_id'):
                raise ValueError('Ảnh phong cách chưa sẵn sàng. Tải lại mẫu phong cách.')
            uuid.UUID(ref['media_id'])
            refs['style'] = ref
        batches = {'GENERATE_IMAGE': [], 'REGENERATE_IMAGE': []}
        image_targets = {r['id']: image_signature(body, s) for r, s in zip(scenes, body.scenes)}
        async def image_done(request):
            generation.setdefault(request['scene_id'], {})['image'] = image_targets[request['scene_id']]
            speech_store.atomic_json(root/'generation.json', generation)
        for row, scene in zip(scenes, body.scenes):
            prompt, names = direction.scene_prompt(body, scene, project['id'], refs)
            await crud.update_scene(row['id'], character_names=json.dumps(names), video_prompt=video_prompt(scene, body, project['id']),
                                    image_prompt=prompt)
            state = generation.get(row['id'], {})
            current = row.get(f'{prefix}_image_media_id')
            # A pre-existing steps 1–3 image has no signature yet; adopt it once.
            changed = state.get('image') and state['image'] != image_signature(body, scene)
            if changed or not current or row.get(f'{prefix}_image_status') != 'COMPLETED':
                batches['REGENERATE_IMAGE' if current else 'GENERATE_IMAGE'].append(row['id'])
        preview_state = speech_store.read_json(root/'preview.json', {})
        preview = body.preview_first and not preview_state.get('approved')
        if preview and preview_state.get('regenerate'):
            first = preview_state.get('scene_id') if body.preview_all else scenes[0]['id']
            first = first or scenes[0]['id']
            batches['GENERATE_IMAGE'] = [sid for sid in batches['GENERATE_IMAGE'] if sid != first]
            if first not in batches['REGENERATE_IMAGE']:
                batches['REGENERATE_IMAGE'].append(first)
        for kind, ids in batches.items():
            if preview and not body.preview_all:
                ids = [sid for sid in ids if sid == scenes[0]['id']]
            if ids:
                await studio.generate_images(vid, project['id'], ids, kind, body.orientation, update, 30, 15, image_done)
        scenes = await crud.list_scenes(vid)
        if preview:
            # A failed local download must not spend another image generation on retry.
            speech_store.atomic_json(root/'preview.json', {'approved': False})
            for row in scenes if body.preview_all else scenes[:1]:
                await studio.image_file(vid, row[f'{prefix}_image_media_id'])
            record_preview(vid, body, scenes[0])
            update(result={'preview_url': f"/api/videos/{vid}/stickman/scenes/{scenes[0]['id']}/image"})
            return scenes, None, None
        for row, scene in zip(scenes, body.scenes):
            generation.setdefault(row['id'], {})['image'] = image_signature(body, scene)
        speech_store.atomic_json(root/'generation.json', generation)
        if body.motion == 'ai':
            batches = {'GENERATE_VIDEO': [], 'REGENERATE_VIDEO': []}
            video_targets = {r['id']: studio.digest([video_prompt(s, body, project['id']), r[f'{prefix}_image_media_id']]) for r, s in zip(scenes, body.scenes)}
            async def video_done(request):
                generation[request['scene_id']]['video'] = video_targets[request['scene_id']]
                speech_store.atomic_json(root/'generation.json', generation)
            for row, scene in zip(scenes, body.scenes):
                signature = studio.digest([video_prompt(scene, body, project['id']), row[f'{prefix}_image_media_id']])
                state = generation[row['id']]
                current = row.get(f'{prefix}_video_media_id')
                if not current or state.get('video') != signature:
                    batches['REGENERATE_VIDEO' if current else 'GENERATE_VIDEO'].append(row['id'])
                    # Local illustrated clips also have COMPLETED status; they aren't Flow clips.
                    if not current:
                        await crud.update_scene(row['id'], **{f'{prefix}_video_status': 'PENDING'})
            for kind, ids in batches.items():
                if ids:
                    from agent.services.stickman_models import selected
                    await studio.generate_images(vid, project['id'], ids, kind, body.orientation, update, 45, 15, video_done,
                                                 video_model=selected(body, project))
            scenes = await crud.list_scenes(vid)
            for row, scene in zip(scenes, body.scenes):
                generation[row['id']]['video'] = studio.digest([video_prompt(scene, body, project['id']), row[f'{prefix}_image_media_id']])
            speech_store.atomic_json(root/'generation.json', generation)
    renders = speech_store.read_json(root/'renders.json', {})
    paths, lengths, checks = [], [], []
    for index, (row, scene, voice) in enumerate(zip(scenes, body.scenes, voices)):
        update(progress=60+round(15*index/len(scenes)), stage=f'Dựng cảnh · {index+1}/{len(scenes)}')
        audio = Path(voice['audio_path'])
        settings = {'style': body.style, 'orientation': body.orientation, 'motion': body.motion, 'dialogue': body.dialogue,
                    'speaker': scene.speaker, 'emotion': scene.emotion, 'gesture': scene.gesture}
        if body.motion == 'ai' and scene.motion_beats:
            settings['timing'] = 'full-motion-v1'
        source_id = row.get(f'{prefix}_video_media_id') if body.motion == 'ai' else row.get(f'{prefix}_image_media_id')
        key = studio.digest([settings, source_id if body.motion != 'talk' else None, pacing.fingerprint(audio)])
        target = root/'renders'/f'{key}.mp4'
        target.parent.mkdir(parents=True, exist_ok=True)
        length = math.ceil(voice['duration']*30-1e-6)/30
        thumbnail = target.with_suffix('.png')
        if not target.is_file():
            if body.motion == 'talk':
                spec = target.with_suffix('.json')
                speech_store.atomic_json(spec, settings)
                try:
                    await media_jobs.process([sys.executable, '-m', 'agent.services.stickman_actor', str(spec), str(audio), str(target), str(thumbnail)], timeout=300)
                finally:
                    target.with_name(f'.{key}.rendering.mp4').unlink(missing_ok=True)
            elif body.motion == 'ai':
                raw = root/f'flow-{uuid.UUID(source_id)}.mp4'
                if not raw.is_file():
                    from agent.services.video_reviewer import _download_via_get_media
                    temporary = raw.with_suffix('.download')
                    try:
                        await _download_via_get_media(source_id, temporary)
                        await pacing.probe(temporary)
                        temporary.replace(raw)
                    finally:
                        temporary.unlink(missing_ok=True)
                width, height = (720, 1280) if body.orientation == 'VERTICAL' else (1280, 720)
                temporary = target.with_name(f'.{key}.rendering.mp4')
                raw_info = await pacing.probe(raw)
                # New directed clips retain all three beats: map the full motion
                # to the measured voice rather than truncating or freezing its end.
                timing = f"setpts={length/raw_info['duration']:.8f}*(PTS-STARTPTS)," if scene.motion_beats else ''
                tail = '' if scene.motion_beats else ',tpad=stop_mode=clone:stop_duration=90'
                try:
                    await media_jobs.process(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(raw), '-an', '-vf',
                        f'{timing}scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},fps=30{tail},format=yuv420p',
                        '-t', str(length), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '21', '-movflags', '+faststart', str(temporary)], timeout=300)
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)
            else:
                image = await studio.image_file(vid, row[f'{prefix}_image_media_id'])
                await studio.image_clip(image, target, voice['duration'], body.orientation, body.motion)
        info = await pacing.probe(target)
        if abs(info['duration']-length) > .06:
            raise ValueError(f'Cảnh {index+1} bị lệch thời lượng. Chưa xuất bản.')
        renders[row['id']] = {'key': key, 'path': str(target), 'audio': str(audio), 'thumbnail': str(thumbnail), 'settings': settings}
        speech_store.atomic_json(root/'renders.json', renders)
        speech_store.atomic_json(target.with_suffix('.metadata.json'), renders[row['id']])
        paths.append(str(target)); lengths.append(length)
        checks.append({'scene_id': row['id'], 'scene': index+1, 'voice_seconds': voice['duration'], 'picture_seconds': length,
                       'caption_cps': round(len(scene.narration)/voice['duration'], 1),
                       'warning': 'Phụ đề đọc nhanh; có thể giảm tốc độ giọng.' if len(scene.narration)/voice['duration'] > 18 else None})
        await crud.update_scene(row['id'], duration=length, **{f'{prefix}_video_status': 'COMPLETED',
            f'{prefix}_video_url': f'http://127.0.0.1:{studio.API_PORT}/api/videos/{vid}/stickman/scenes/{row["id"]}/file'})
    speech_store.atomic_json(root/'quality.json', {'scenes': checks, 'mouth_alignment': 'audio_energy' if body.motion == 'talk' else 'offscreen',
        'caption_alignment': 'phrase_proportional', 'character_check': 'deterministic_vector' if body.motion == 'talk' else 'needs_visual_review'})
    return scenes, paths, lengths


async def storyboard_images(vid, body):
    """Read persisted image variants; URLs change with the actual media UUID."""
    from agent.services import stickman
    rows = await crud.list_scenes(vid)
    prefix = 'vertical' if body.orientation == 'VERTICAL' else 'horizontal'
    return [{'scene_id': row['id'], 'scene': index+1, 'narration': planned.narration,
             'cast': direction.cast(body, planned), 'extras': planned.extras, 'shot_kind': planned.shot_kind,
             'media_id': row.get(f'{prefix}_image_media_id'),
             'url': f"/api/videos/{vid}/stickman/scenes/{row['id']}/image?v={row.get(f'{prefix}_image_media_id')}"}
            for index, (row, planned) in enumerate(zip(rows, body.scenes))
            if row.get(f'{prefix}_image_status') == 'COMPLETED' and row.get(f'{prefix}_image_media_id')
            and (stickman.directory(vid)/f"{row[f'{prefix}_image_media_id']}.image").is_file()]


async def visual_review(vid, pid, body, scenes, update):
    """Reuse FrameWeaver's light vision review, cached per generated clip. No automatic credit spending."""
    from agent.services import stickman as studio
    from agent.services.video_reviewer import review_scene_video
    root = studio.directory(vid)
    cached = speech_store.read_json(root/'vision.json', {})
    names = {name for scene in scenes for name in json.loads(scene.get('character_names') or '[]')}
    characters = [c for c in await crud.get_project_characters(pid) if c['name'] in names and c.get('entity_type', 'character') == 'character']
    prefix = 'vertical' if body.orientation == 'VERTICAL' else 'horizontal'
    checks = []
    for index, scene in enumerate(scenes):
        update(progress=73, stage=f'Kiểm tra hình và nhân vật · {index+1}/{len(scenes)}')
        key = studio.digest([scene[f'{prefix}_video_media_id'], scene.get('video_prompt'), [c.get('media_id') for c in characters]])
        if key not in cached:
            try:
                result = await review_scene_video(scene, characters, mode='light', orientation=body.orientation, project_id=pid)
                cached[key] = {'scene': index+1, 'score': result.overall_score,
                    'warning': result.fix_guide if result.overall_score < 7.5 or result.has_critical_errors else None,
                    'character_score': result.dimensions.character_consistency,
                    'errors': [e.model_dump() for e in result.errors]}
                speech_store.atomic_json(root/'vision.json', cached)
            except Exception as exc:
                checks.append({'scene': index+1, 'warning': f'Chưa kiểm tra được hình bằng AI: {str(exc)[:240]}'} )
                continue
        checks.append(cached[key])
    report = speech_store.read_json(root/'quality.json', {})
    report.update(vision=checks, character_check='vision_review')
    speech_store.atomic_json(root/'quality.json', report)


async def speech_review(vid, body, scenes, voices, update):
    """Reuse the existing Whisper batch comparison; only listen again to changed WAVs."""
    from agent.services import stickman as studio, speech_analysis, transcription
    from agent.services.tts_runtime import TTS_SEMAPHORE
    root = studio.directory(vid)/'checks'
    root.mkdir(parents=True, exist_ok=True)
    available = [m['id'] for m in transcription.models() if m['ready']]
    model = 'small' if 'small' in available else available[0]
    cached = speech_store.read_json(root/'words.json', {})
    missing, keys, positions, cursor = [], [], [], 0
    for row, scene, voice in zip(scenes, body.scenes, voices):
        key = studio.digest([pacing.fingerprint(Path(voice['audio_path'])), scene.narration, model, body.language])
        keys.append(key)
        positions.append((cursor, cursor+voice['duration']))
        if key not in cached:
            pcm = root/f'{key}.wav'
            await media_jobs.process(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', voice['audio_path'], '-ac', '1', '-ar', '16000', str(pcm)], timeout=90)
            missing.append({'id': key, 'path': str(pcm), 'duration': voice['duration'], 'start': cursor, 'end': cursor+voice['duration'], 'text': scene.narration})
        cursor += math.ceil(voice['duration']*30)/30
    if missing:
        async with TTS_SEMAPHORE:
            recognized = await speech_analysis.recognize(missing, model, body.language, root,
                lambda **changes: update(progress=73, stage=changes.get('stage', 'Nghe lại lời đọc')))
        for row in recognized:
            cached[row['id']] = row
        speech_store.atomic_json(root/'words.json', cached)
    report = speech_store.read_json(studio.directory(vid)/'quality.json', {})
    report['words'] = [{**cached[key], 'scene': i+1, 'start': positions[i][0], 'end': positions[i][1],
        'needs_review': cached[key].get('needs_review', False) or cached[key].get('difference', 0) > .1} for i, key in enumerate(keys)]
    speech_store.atomic_json(studio.directory(vid)/'quality.json', report)
