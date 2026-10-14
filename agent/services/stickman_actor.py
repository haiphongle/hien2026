"""Original procedural cartoon people; audio energy drives mouths, not phoneme alignment.

Runs in the existing cancellable media subprocess group. Frames stream into
FFmpeg; no per-frame PNG directory or model downloads. No Synctoon code/assets.
"""
import argparse
import array
import json
import math
import subprocess
import wave
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter
from agent.materials import STORY_PRESETS

PALETTES = {p['id']: p for p in STORY_PRESETS if p.get('local_actor', True)}


def solid_body(draw, x, neck, shoulder, hip, floor, scale, left, right, fill, outline):
    """Closed silhouettes and rounded limbs share the existing gesture endpoints."""
    edge = max(1, round(2*scale))

    def limb(points, width):
        for color, size in [(outline, width+2*edge), (fill, width)]:
            draw.line(points, fill=color, width=size, joint='curve')
            r = size/2
            for px, py in points:
                draw.ellipse((px-r, py-r, px+r, py+r), fill=color)

    for side in [-1, 1]:
        ankle = (x+side*25*scale, floor-8*scale)
        limb([(x+side*13*scale, hip), ankle], max(4, round(21*scale)))
        draw.ellipse((ankle[0]-15*scale, floor-14*scale, ankle[0]+18*scale, floor+3*scale), fill=fill, outline=outline, width=edge)
    torso = [(x-14*scale, neck-4*scale), (x+14*scale, neck-4*scale),
             (x+29*scale, shoulder), (x+25*scale, hip+4*scale),
             (x-25*scale, hip+4*scale), (x-29*scale, shoulder)]
    draw.polygon(torso, fill=fill)
    draw.line(torso+[torso[0]], fill=outline, width=edge, joint='curve')
    for side, hand in [(-1, left), (1, right)]:
        start = (x+side*25*scale, shoulder+4*scale)
        elbow = ((start[0]+hand[0])/2, (start[1]+hand[1])/2+9*scale)
        limb([start, elbow, hand], max(4, round(15*scale)))
        # A short thumb keeps the simple hand readable at small preview sizes.
        draw.line((hand[0], hand[1], hand[0]-side*6*scale, hand[1]-5*scale), fill=outline, width=edge)


def mouth_levels(path, fps=30):
    """One bounded PCM window per frame; silence and padded frames stay closed."""
    levels = []
    with wave.open(str(path), 'rb') as wav:
        if wav.getsampwidth() != 2 or wav.getcomptype() != 'NONE':
            raise ValueError('Giọng thử nghiệm cần WAV PCM 16 bit.')
        rate = wav.getframerate()
        count = math.ceil(wav.getnframes()/rate*fps)
        for index in range(count):
            a, b = round(index*rate/fps), round((index+1)*rate/fps)
            samples = array.array('h', wav.readframes(b-a))
            levels.append(math.sqrt(sum(float(v)**2 for v in samples)/max(1, len(samples)))/32768)
    ceiling = max(max(levels, default=0), .03)
    return [0 if value < max(.004, ceiling*.035) else min(1, math.sqrt(value/ceiling)) for value in levels]


def frame(spec, level, seconds, width, height):
    palette = PALETTES[spec['style']]
    background, first = palette['background'], palette['ink']
    second = palette.get('second_ink', first)
    solid = palette['shape'] == 'solid'
    surface = Image.new('RGB', (width, height), background)
    draw = ImageDraw.Draw(surface)
    scale = min(width/360, height/520) if solid else min(width, height)/360
    stroke = max(2, round(3*scale))
    floor = height*.79
    draw.line((width*.12, floor+12*scale, width*.88, floor+12*scale), fill=first, width=max(1, stroke//2))
    dialogue = spec.get('dialogue', False)
    for i, x in enumerate([width*.29, width*.71] if dialogue else [width*.5]):
        speaking = ('A' if i == 0 else 'B') == spec.get('speaker', 'A')
        ink = first if i == 0 else second
        emotion = spec['emotion'] if speaking else 'thinking'
        gesture = spec['gesture'] if speaking else 'rest'
        bob = math.sin(seconds*2.4)*1.5*scale if speaking else 0
        radius = (40 if solid else 29)*scale
        cy = floor-275*scale+bob if solid else height*.34+bob
        neck = cy+radius
        hip = floor-(115 if solid else 70)*scale
        shoulder = neck+28*scale
        if not solid:
            draw.ellipse((x-radius, cy-radius, x+radius, cy+radius), outline=ink, width=stroke)
            draw.line((x, neck, x, hip), fill=ink, width=stroke)
            draw.line((x, hip, x-32*scale, floor), fill=ink, width=stroke)
            draw.line((x, hip, x+32*scale, floor), fill=ink, width=stroke)
        left, right = (x-34*scale, shoulder+52*scale), (x+34*scale, shoulder+52*scale)
        if gesture == 'explain':
            right = (x+65*scale, shoulder-10*scale+math.sin(seconds*3)*7*scale)
        elif gesture == 'wave':
            right = (x+42*scale+math.sin(seconds*5)*14*scale, shoulder-61*scale)
        elif gesture == 'point':
            right = (x+72*scale, shoulder-15*scale)
        elif gesture == 'think':
            right = (x+24*scale, cy+18*scale)
        if solid:
            solid_body(draw, x, neck, shoulder, hip, floor, scale, left, right, palette['fill'], palette['outline'])
            draw.ellipse((x-radius, cy-radius, x+radius, cy+radius), fill=palette['fill'], outline=palette['outline'], width=stroke)
            ink = '#222222'
            if i == 1:
                draw.polygon([(x-9*scale, cy-radius+2*scale), (x, cy-radius-7*scale), (x+9*scale, cy-radius+2*scale)], fill=palette['fill'], outline=palette['outline'], width=stroke)
        else:
            draw.line((*left, x, shoulder, *right), fill=ink, width=stroke)
        # Blink replaces eye shapes on the current frame, never inserts time.
        blink = (seconds+i*.71) % 3.2 > 3.07
        for direction in [-1, 1]:
            ex = x+direction*10*scale
            if blink:
                draw.line((ex-3*scale, cy-5*scale, ex+3*scale, cy-5*scale), fill=ink, width=stroke)
            else:
                eye = (4 if emotion == 'surprised' else 2.5)*scale
                ey = eye*1.5 if solid else eye
                draw.ellipse((ex-eye, cy-6*scale-ey, ex+eye, cy-6*scale+ey), fill=ink)
        my = cy+10*scale
        opening = level if speaking else 0
        if opening:
            ry = (2+opening*9)*scale
            draw.ellipse((x-8*scale, my-ry, x+8*scale, my+ry), fill=ink if solid else None, outline=ink, width=max(1, stroke//2))
        elif emotion == 'happy':
            draw.arc((x-10*scale, my-10*scale, x+10*scale, my+5*scale), 0, 180, fill=ink, width=stroke)
        elif emotion == 'sad':
            draw.arc((x-10*scale, my, x+10*scale, my+12*scale), 180, 360, fill=ink, width=stroke)
        else:
            draw.line((x-8*scale, my, x+8*scale, my), fill=ink, width=stroke)
        if emotion == 'thinking':
            draw.line((x-15*scale, cy-17*scale, x-5*scale, cy-19*scale), fill=ink, width=max(1, stroke//2))
        elif solid and emotion in {'happy', 'sad'}:
            for side in [-1, 1]:
                outer = cy-(20 if emotion == 'happy' else 14)*scale
                inner = cy-(18 if emotion == 'happy' else 21)*scale
                draw.line((x+side*17*scale, outer, x+side*5*scale, inner), fill=ink, width=max(1, stroke//2))
        if emotion == 'surprised':
            for dx in [-16, 0, 16]:
                draw.line((x+dx*scale, cy-radius-9*scale, x+dx*scale, cy-radius-21*scale), fill=ink, width=max(1, stroke//2))
    if solid and spec['style'] == 'cartoon_chalk':
        backdrop = Image.new('RGB', surface.size, background)
        mask = ImageChops.difference(surface, backdrop).convert('L').point(lambda value: 255 if value else 0)
        halo = mask.filter(ImageFilter.GaussianBlur(5*scale)).point(lambda value: round(value*.3))
        glow = Image.composite(Image.new('RGB', surface.size, first), backdrop, halo)
        glow.paste(surface, mask=mask)
        return glow
    return surface


def render(spec, audio, output, thumbnail):
    levels = mouth_levels(audio)
    width, height = (720, 1280) if spec['orientation'] == 'VERTICAL' else (1280, 720)
    # Draw at half size, upscale once in FFmpeg. Memory stays one frame.
    sw, sh = width//2, height//2
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f'.{output.stem}.rendering.mp4')
    args = ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-s', f'{sw}x{sh}', '-r', '30', '-i', 'pipe:0', '-an', '-vf', f'scale={width}:{height},format=yuv420p',
            '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-movflags', '+faststart', str(temporary)]
    child = subprocess.Popen(args, stdin=subprocess.PIPE)
    try:
        for index, level in enumerate(levels):
            image = frame(spec, level, index/30, sw, sh)
            if index == 0:
                image.save(thumbnail)
            child.stdin.write(image.tobytes())
            if index % 30 == 0:
                print(f'frame={index}/{len(levels)}', flush=True)
        child.stdin.close()
        if child.wait(timeout=60):
            raise RuntimeError('FFmpeg chưa tạo được nhân vật nói chuyện.')
        temporary.replace(output)
    finally:
        if child.poll() is None:
            child.kill()
        child.wait()
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('spec', type=Path)
    parser.add_argument('audio', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('thumbnail', type=Path)
    args = parser.parse_args()
    render(json.loads(args.spec.read_text()), args.audio, args.output, args.thumbnail)
