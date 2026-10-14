"""PiP composition: source timing is independent from placement on the canvas."""
import math
import hashlib

from PIL import Image, ImageChops, ImageDraw


def pane_width(width):
    return max(2, width // 4 * 2)


def geometry(value, width, height):
    if value.layout == 'split':
        left = pane_width(width)
        return left, 0, width - left, height
    w = max(2, math.floor(value.width * width / 2) * 2)
    h = max(2, math.floor(value.height * height / 2) * 2)
    return min(width-w, round(value.x*width)), min(height-h, round(value.y*height)), w, h


def main_segments(segments, overlays):
    """Reframe the original source in each pane, before any letterbox is baked in."""
    splits = [(c.start, c.start + c.duration) for c in overlays if c.overlay.layout == 'split']
    result = []
    for begin, span, clip in segments:
        end = begin + span
        cuts = sorted({begin, end, *(p for a, b in splits for p in (a, b) if begin < p < end)})
        for a, b in zip(cuts, cuts[1:]):
            result.append((a, b-a, clip, any(start <= a + 1e-6 and a < stop - 1e-6 for start, stop in splits)))
    return result


def shape_images(value, width, height, border, work):
    """Cache static antialiased masks; never calculate rounded corners per video frame."""
    key = hashlib.sha256(f'{value.shape}:{value.radius}:{width}:{height}:{border}:{value.border_color}'.encode()).hexdigest()[:20]
    mask_file, border_file = work / f'{key}-mask.png', work / f'{key}-border.png'
    if mask_file.is_file():
        return mask_file, border_file if border else None
    scale = 2
    size = width*scale, height*scale
    outer = Image.new('L', size)
    inner = Image.new('L', size)
    radius = min(width, height)*value.radius/100*scale
    for image, inset in ((outer, 0), (inner, border*scale)):
        box = (inset, inset, size[0]-1-inset, size[1]-1-inset)
        if box[2] <= box[0] or box[3] <= box[1]:
            continue
        draw = ImageDraw.Draw(image)
        if value.shape == 'circle':
            side = min(size)-2*inset
            x, y = (size[0]-side)/2, (size[1]-side)/2
            draw.ellipse((x, y, x+side-1, y+side-1), fill=255)
        else:
            draw.rounded_rectangle(box, radius=max(0, radius-inset), fill=255)
    outer.resize((width, height), Image.Resampling.LANCZOS).save(mask_file)
    if border:
        ring = ImageChops.subtract(outer, inner).resize((width, height), Image.Resampling.LANCZOS)
        image = Image.new('RGBA', (width, height), value.border_color)
        image.putalpha(ring)
        image.save(border_file)
    return mask_file, border_file if border else None


def compose(clips, assets, width, height, fps, length, input_index, current, work):
    """Return extra inputs/graph. Voices are mixed exclusively by audio tracks."""
    args, graph = [], []
    index = input_index
    for i, clip in enumerate(clips):
        value = clip.overlay
        x, y, w, h = geometry(value, width, height)
        span = min(clip.duration, length-clip.start)
        args += ['-threads', '2', '-ss', str(clip.source_in), '-t', str(span*clip.speed), '-i', assets[str(clip.asset_id)]['path']]
        scale = (f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}' if value.fit == 'cover'
                 else f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black')
        border = min(round(value.border*height/100), w//2, h//2)
        filters = [f'setpts=(PTS-STARTPTS)/{clip.speed}', f'fps={fps}', 'setsar=1', scale]
        if value.flip:
            filters.append('hflip')
        shaped = value.layout != 'split' and value.shape != 'rectangle'
        if border and not shaped:
            filters.append(f'drawbox=x=0:y=0:w=iw:h=ih:color={value.border_color}:t={border}')
        graph.append(f'[{index}:v:0]{",".join(filters)},format=rgba[pipraw{i}]')
        index += 1
        picture = f'pipraw{i}'
        if shaped:
            mask_file, border_file = shape_images(value, w, h, border, work)
            args += ['-loop', '1', '-framerate', str(fps), '-i', str(mask_file)]
            graph.append(f'[{picture}][{index}:v]alphamerge[masked{i}]')
            index += 1
            picture = f'masked{i}'
            if border_file:
                args += ['-loop', '1', '-framerate', str(fps), '-i', str(border_file)]
                graph.append(f'[{picture}][{index}:v]overlay=0:0:format=auto[bordered{i}]')
                index += 1
                picture = f'bordered{i}'
        timing = [f'trim=duration={span}']
        if value.entrance and value.entrance_offset < value.entrance:
            timing += [f'setpts=PTS-STARTPTS+{value.entrance_offset}/TB',
                       f'fade=t=in:st=0:d={value.entrance}:alpha=1']
        timing.append(f'setpts=PTS-STARTPTS+{clip.start}/TB')
        graph += [f'[{picture}]{",".join(timing)}[pip{i}]',
                  f"[{current}][pip{i}]overlay={x}:{y}:eof_action=pass:repeatlast=0:enable='gte(t,{clip.start})*lt(t,{clip.start+span})'[pipped{i}]"]
        current = f'pipped{i}'
    return args, graph, current, index-input_index
