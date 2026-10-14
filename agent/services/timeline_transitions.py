"""Picture-only joins: hold the outgoing final frame, without moving audio or captions."""
from agent.services import editing, timeline_overlay

FILTERS = {'dissolve': 'fade', 'blur': 'fade', 'black': 'fadeblack', 'slide': 'slideleft', 'light': 'fadewhite'}


def joins(document):
    track = next(t for t in document.tracks if t.kind == 'video')
    if track.muted:
        return {}
    clips = sorted((c for c in document.clips if c.track_id == track.id), key=lambda c: c.start)
    return {right.id: (left, max(1, int(min(right.transition.duration, left.duration, right.duration)*document.fps+1e-6))/document.fps)
            for left, right in zip(clips, clips[1:]) if right.transition and abs(left.start+left.duration-right.start) < 1e-5}


def compose(previous, source, value, duration, elapsed, frame, fps, width, height, split=False):
    """Only two decoders per segment. Cropped exports continue the same transition phase."""
    at = min(source['duration'] - 1/fps, previous.source_in + (previous.duration-1/fps)*previous.speed)
    args = ['-ss', str(max(0, at)), '-i', source['path']]
    pane = frame.model_copy(update={'width': timeline_overlay.pane_width(width)}) if split else frame
    layout, _ = editing.layout_filter(pane, source['width'], source['height'])
    layout = layout.replace('[0:v]', '[heldsource]').replace('[vout]', '[heldlayout]')
    padding = f'pad={width}:{height}:0:0:color={frame.color},' if split else ''
    blur = f'gblur=sigma={4*height/720},' if value.kind == 'blur' else ''
    # Padding trimmed-away input frames makes seek/range/PiP boundaries share the full join phase.
    new_padding = f'tpad=start_mode=clone:start_duration={elapsed},' if elapsed > 1e-6 else ''
    graph = f'[1:v]trim=end_frame=1,setpts=PTS-STARTPTS[heldsource];{layout};'
    graph += f'[heldlayout]{padding}{blur}fps={fps},setsar=1,tpad=stop_mode=clone:stop_duration={duration},trim=duration={duration+1/fps},format=yuv444p,settb=AVTB[held];'
    graph += f'[vout]{new_padding}setsar=1,format=yuv444p,settb=AVTB[incoming];'
    graph += f'[held][incoming]xfade=transition={FILTERS[value.kind]}:duration={duration}:offset=0,trim=start={elapsed},setpts=PTS-STARTPTS,format=yuv420p[transitioned]'
    return args, graph
