"""Small, deterministic still-image recipes for the existing FFmpeg renderer."""
from agent.models.timeline import ImageEffect

MOVEMENTS = {'auto': 'Tự luân phiên', 'still': 'Giữ ảnh tĩnh', 'zoom_in': 'Tiến gần', 'zoom_out': 'Lùi xa',
             'pan_left': 'Lia sang trái', 'pan_right': 'Lia sang phải', 'drift_up': 'Lia lên', 'drift_down': 'Lia xuống'}
INTENSITIES = {'light': {'name': 'Nhẹ', 'amount': .05}, 'medium': {'name': 'Vừa', 'amount': .10}, 'strong': {'name': 'Mạnh', 'amount': .16}}
PRESETS = {
    'gentle': {'name': 'Nhẹ lòng, yên bình', 'description': 'Máy chậm, giữ màu ảnh, chuyển mờ.',
               'movements': ['zoom_in', 'zoom_out', 'pan_right', 'pan_left'], 'contrast': 1, 'saturation': 1, 'vignette': False,
               'transition': {'kind': 'dissolve', 'duration': .45}},
    'reflective': {'name': 'Tâm trạng, suy ngẫm', 'description': 'Lùi xa, giảm màu nhẹ, viền tối.',
                   'movements': ['zoom_out', 'drift_up', 'pan_left'], 'contrast': 1.02, 'saturation': .9, 'vignette': True,
                   'transition': {'kind': 'black', 'duration': .35}},
    'story': {'name': 'Kể chuyện sinh động', 'description': 'Luân phiên hướng máy, màu tươi nhẹ.',
              'movements': ['pan_right', 'zoom_in', 'pan_left', 'drift_down'], 'contrast': 1.03, 'saturation': 1.05, 'vignette': False,
              'transition': {'kind': 'dissolve', 'duration': .3}},
    'review': {'name': 'Review nhanh', 'description': 'Nhấn gần rõ hơn, chuyển cảnh ngắn.',
               'movements': ['zoom_in', 'pan_left', 'zoom_out', 'pan_right'], 'contrast': 1.06, 'saturation': 1.02, 'vignette': False,
               'transition': {'kind': 'slide', 'duration': .2}},
    'none': {'name': 'Không hiệu ứng', 'description': 'Ảnh tĩnh, giữ màu, không chuyển cảnh.',
             'movements': ['still'], 'contrast': 1, 'saturation': 1, 'vignette': False, 'transition': None},
}


def catalog():
    return {'presets': [{'id': key, **value} for key, value in PRESETS.items()],
            'movements': [{'id': key, 'name': name} for key, name in MOVEMENTS.items()],
            'intensities': [{'id': key, **value} for key, value in INTENSITIES.items()]}


def selected(body, scene):
    return scene.image_effect or body.image_effect


def camera(effect, index=0):
    preset = PRESETS[effect.preset]
    movement = effect.movement
    if effect.preset == 'none':
        movement = 'still'
    elif movement == 'auto':
        movement = preset['movements'][index % len(preset['movements'])]
    amount = INTENSITIES[effect.intensity]['amount'] * (1.25 if effect.preset == 'review' else 1)
    zoom = [1, 1 + amount] if movement == 'zoom_in' else [1 + amount, 1] if movement == 'zoom_out' else [1, 1] if movement == 'still' else [1 + amount] * 2
    x, y = [effect.focus_x] * 2, [effect.focus_y] * 2
    if movement.startswith('pan_'):
        x = [max(0, effect.focus_x-.5), min(1, effect.focus_x+.5)]
        if movement == 'pan_left':
            x.reverse()
    if movement.startswith('drift_'):
        y = [max(0, effect.focus_y-.5), min(1, effect.focus_y+.5)]
        if movement == 'drift_up':
            y.reverse()
    return {'movement': movement, 'zoom': zoom, 'x': x, 'y': y}


def filter_graph(effect: ImageEffect, index, frames, width, height, *, fps=30, offset=0, speed=1, source_frames=None):
    recipe, motion = PRESETS[effect.preset], camera(effect, index)
    progress = f'min(1,max(0,({offset*fps:.8f}+on*{speed})/{max(1, (source_frames or frames)-1)}))'
    eased = f'({progress}*{progress}*(3-2*{progress}))'
    def interpolate(values):
        return f'({values[0]:.6f}+({values[1]-values[0]:.6f})*{eased})'
    zoom, x, y = (interpolate(motion[key]) for key in ('zoom', 'x', 'y'))
    fitted = (f'scale={width*2}:{height*2}:force_original_aspect_ratio=decrease,'
              f'pad={width*2}:{height*2}:(ow-iw)/2:(oh-ih)/2:color=black,' if effect.fit == 'contain' else
              f'scale={width*2}:{height*2}:force_original_aspect_ratio=increase,'
              f'crop={width*2}:{height*2}:(iw-ow)*{effect.focus_x}:(ih-oh)*{effect.focus_y},')
    graph = fitted + f"zoompan=z='{zoom}':x='(iw-iw/zoom)*{x}':y='(ih-ih/zoom)*{y}':d={frames}:s={width}x{height}:fps={fps}"
    if recipe['contrast'] != 1 or recipe['saturation'] != 1:
        graph += f",eq=contrast={recipe['contrast']}:saturation={recipe['saturation']}"
    if recipe['vignette']:
        graph += f",vignette=angle={.28 + INTENSITIES[effect.intensity]['amount']}"
    return graph + ',format=yuv420p'


def mode(body, scene):
    # The still-only choice is a hard boundary, including stale per-scene choices.
    if body.motion == 'mixed':
        return 'ai' if scene.render_mode == 'ai' else 'effects'
    if body.motion == 'ai' and scene.render_mode == 'effects':
        return 'effects'
    return body.motion


def uses_ai(body):
    return any(mode(body, scene) == 'ai' for scene in body.scenes)


def keep_clip(body, scene, row, state, pid):
    if body.motion != 'mixed' or mode(body, scene) != 'effects' or scene.render_mode == 'effects' or not body.keep_completed_clips:
        return False
    from agent.services.stickman import digest
    from agent.services.stickman_production import video_prompt
    prefix = 'vertical' if body.orientation == 'VERTICAL' else 'horizontal'
    return bool(row.get(f'{prefix}_video_media_id') and row.get(f'{prefix}_video_status') == 'COMPLETED'
                and state.get('video') == digest([video_prompt(scene, body, pid), row.get(f'{prefix}_image_media_id')]))


def clip_filter(clip, source, span, fps, width, height):
    """Source-time animation keeps split, speed and range exports on the same frame."""
    effect = clip.image_effect or ImageEffect(preset='none', movement='still')
    return filter_graph(effect, 0, max(1, round(span*fps)), width, height, fps=fps,
                        offset=clip.source_in, speed=clip.speed,
                        source_frames=max(1, round((clip.image_span or clip.duration*clip.speed)*fps)))
