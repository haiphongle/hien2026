"""Versioned per-video editing settings and FFmpeg layout helpers, no model loads."""
import json
import math
import shutil
import subprocess
import time
from uuid import UUID
from typing import BinaryIO
from pathlib import Path

from agent.config import SHARED_OUTPUT_DIR
from agent.models.editing import CaptionStyle, ColorSettings, EditingSettings, FrameSettings
from agent.services.ffmpeg_progress import processing_timeout, run_ffmpeg
from agent.services.speech_store import atomic_json, identifier, read_json

ROOT = SHARED_OUTPUT_DIR / "editing"
PRESETS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080), "4:5": (1080, 1350)}
CJK_FONT_PATHS = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/Library/Fonts/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/arialuni.ttf", "C:/Windows/Fonts/NotoSansCJK-Regular.ttc",
)
FONT_PATHS = {
    "sans": ("Sans", ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf")),
    "sans-bold": ("Sans đậm", ("/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf")),
    "serif": ("Serif", ("/System/Library/Fonts/Supplemental/Times New Roman.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", "C:/Windows/Fonts/times.ttf")),
    "mono": ("Mono", ("/System/Library/Fonts/Supplemental/Courier New.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "C:/Windows/Fonts/cour.ttf")),
    "cjk": ("Trung / Nhật / Hàn", CJK_FONT_PATHS),
}


def directory(vid: str) -> Path:
    return ROOT / identifier(vid)


def cjk_text(text: str) -> bool:
    return any('\u2e80' <= c <= '\u9fff' or '\uac00' <= c <= '\ud7af' or '\uf900' <= c <= '\ufaff'
               or '\u1100' <= c <= '\u11ff' or '\uff65' <= c <= '\uffdc' or '\U00020000' <= c <= '\U000323af'
               for c in text)


def font_path(font_id: str, text: str = "") -> str:
    if font_id not in FONT_PATHS:
        raise ValueError("Font không nằm trong danh sách được hỗ trợ.")
    paths = CJK_FONT_PATHS if cjk_text(text) else FONT_PATHS[font_id][1]
    path = next((p for p in paths if Path(p).is_file()), None)
    if not path:
        if cjk_text(text) or font_id == "cjk":
            raise ValueError("Thiếu font chữ Trung / Nhật / Hàn. Cài Noto Sans CJK rồi thử lại.")
        raise ValueError("Font đã chọn không có trên máy này.")
    return path


def fonts() -> list[dict]:
    result = []
    for key, (name, _paths) in FONT_PATHS.items():
        try:
            font_path(key)
            result.append({"id": key, "name": name})
        except ValueError:
            pass
    return result


def load(vid: str) -> dict:
    data = read_json(directory(vid) / "settings.json", {"revision": 0, "settings": EditingSettings().model_dump(mode='json'), "last_export": None})
    data["settings"] = EditingSettings.model_validate(data["settings"]).model_dump(mode='json')
    if 'exports' not in data:
        data['exports'] = [data['last_export']] if data.get('last_export') else []
    if (directory(vid) / 'deleting-exports.json').is_file():
        from agent.services.editing_exports import finish_delete
        finish_delete(vid, data)
    return data


def save(vid: str, revision: int, settings: EditingSettings) -> dict:
    # No await between the read and atomic rename: one server event loop serializes writers.
    current = load(vid)
    if current["revision"] != revision:
        raise ValueError("Thiết lập đã đổi ở tab khác. Nạp lại trước khi lưu.")
    font_path(settings.captions.font_id)
    current.update(revision=revision + 1, settings=settings.model_dump(mode='json'))
    atomic_json(directory(vid) / "settings.json", current)
    return current


def record_export(vid: str, result: dict):
    current = load(vid)  # Keep edits made while this export was running.
    result = {**result, 'created_at': result.get('created_at', time.time())}
    current["last_export"] = result
    current['exports'] = [result, *(e for e in current['exports'] if e.get('url') != result.get('url'))]
    atomic_json(directory(vid) / "settings.json", current)


def asset_path(vid: str, image_id: str) -> Path:
    return directory(vid) / 'assets' / f'{UUID(str(image_id))}.png'


def validate_effects(vid: str, settings: EditingSettings, duration: float):
    for region in settings.regions:
        if region.start >= duration or region.end > duration + .05:
            raise ValueError('Khoảng thời gian vùng che vượt video nguồn.')
        if region.kind == 'image' and (not region.image_id or not asset_path(vid, str(region.image_id)).is_file()):
            raise ValueError('Tải ảnh/logo cho vùng phủ hình trước khi xuất.')


def region_pixels(region, width, height):
    x, y = min(width - 2, round(region.x * width)), min(height - 2, round(region.y * height))
    w, h = max(2, min(width - x, round(region.width * width))), max(2, min(height - y, round(region.height * height)))
    return x, y, w, h


def processing_filter(settings: EditingSettings, width: int, height: int, *, video_id=None, time_offset=0):
    graph, (ow, oh) = layout_filter(settings.frame, width, height)
    graph = graph.replace('[vout]', '[layout]')
    current, inputs = 'layout', []
    color = settings.colors
    if color != ColorSettings():
        graph += f';[{current}]eq=brightness={color.brightness}:contrast={color.contrast}:saturation={color.saturation}[colored]'
        current = 'colored'
    for index, region in enumerate(settings.regions):
        x, y, w, h = region_pixels(region, ow, oh)
        target = f'region{index}'
        enable = f"gte(t+{time_offset},{region.start})*lt(t+{time_offset},{region.end})"
        if region.kind == 'color':
            graph += f";[{current}]drawbox=x={x}:y={y}:w={w}:h={h}:color=0x{region.color[1:]}@{region.opacity}:t=fill:enable='{enable}'[{target}]"
        elif region.kind == 'delogo':
            # delogo needs one pixel of known surrounding area on every side.
            x, y = max(1, min(x, ow - 4)), max(1, min(y, oh - 4))
            w, h = max(2, min(w, ow - x - 1)), max(2, min(h, oh - y - 1))
            graph += f";[{current}]delogo=x={x}:y={y}:w={w}:h={h}:enable='{enable}'[{target}]"
        else:
            overlay = f'overlay{index}'
            if region.kind == 'blur':
                radius = min(region.strength, max(1, min(w, h) // 2 - 1))
                chroma_radius = min(radius, max(0, min(w, h) // 4 - 1))
                graph += f';[{current}]split[keep{index}][crop{index}];[crop{index}]crop={w}:{h}:{x}:{y},boxblur={radius}:2:{chroma_radius}:2[{overlay}]'
                current = f'keep{index}'
            else:
                if not video_id or not region.image_id:
                    raise ValueError('Vùng phủ hình chưa có ảnh/logo.')
                path = asset_path(video_id, str(region.image_id))
                if not path.is_file():
                    raise ValueError('Ảnh/logo của vùng không còn trên máy.')
                inputs.append(path)
                graph += f';[{len(inputs)}:v]format=rgba,scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black@0,colorchannelmixer=aa={region.opacity}[{overlay}]'
            graph += f";[{current}][{overlay}]overlay={x}:{y}:shortest=1:enable='{enable}'[{target}]"
        current = target
    graph += f';[{current}]null[vout]'
    return graph, inputs, (ow, oh)


def image_inputs(paths):
    return [argument for path in paths for argument in ['-loop', '1', '-i', str(path)]]


def probe(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_type:format=duration", "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    data = json.loads(result.stdout)
    stream = next(s for s in data["streams"] if s["codec_type"] == "video")
    duration = float(data["format"]["duration"])
    if result.returncode or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Không đọc được thông tin video.")
    return {"width": int(stream["width"]), "height": int(stream["height"]), "duration": duration,
            "has_audio": any(s["codec_type"] == "audio" for s in data["streams"])}


def dimensions(frame: FrameSettings, width: int, height: int) -> tuple[int, int]:
    if frame.preset == "source":
        return width - width % 2, height - height % 2
    if frame.preset == "custom":
        return frame.width, frame.height
    return PRESETS[frame.preset]


def layout_filter(frame: FrameSettings, width: int, height: int) -> tuple[str, tuple[int, int]]:
    """Exact pixel geometry, shared by still preview and video export."""
    ow, oh = dimensions(frame, width, height)
    ratio = (max if frame.fit == "cover" else min)(ow / width, oh / height) * frame.zoom
    sw, sh = max(2, round(width * ratio / 2) * 2), max(2, round(height * ratio / 2) * 2)
    cw, ch = min(sw, ow), min(sh, oh)
    cx, cy = round((sw - cw) * frame.x), round((sh - ch) * frame.y)
    px, py = round((ow - cw) * frame.x), round((oh - ch) * frame.y)
    foreground = f"scale={sw}:{sh},setsar=1,crop={cw}:{ch}:{cx}:{cy}"
    if frame.background == "blur" and (cw < ow or ch < oh):
        graph = (f"[0:v]split[bg][fg];[bg]scale={ow}:{oh}:force_original_aspect_ratio=increase,"
                 f"crop={ow}:{oh},gblur=sigma=25[base];[fg]{foreground}[front];"
                 f"[base][front]overlay={px}:{py},setsar=1[vout]")
    else:
        graph = f"[0:v]{foreground},pad={ow}:{oh}:{px}:{py}:color={frame.color},setsar=1[vout]"
    return graph, (ow, oh)


def transform(source: Path, target: Path, settings: EditingSettings, progress_callback=None, *, video_id=None):
    info = probe(source)
    graph, images, size = processing_filter(settings, info['width'], info['height'], video_id=video_id)
    if size == (info["width"], info["height"]) and settings.frame.zoom == 1 and settings.colors == ColorSettings() and not settings.regions:
        shutil.copyfile(source, target)
        if progress_callback:
            progress_callback(1)
        return size
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(source), *image_inputs(images), "-filter_complex", graph,
           "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(target)]
    result = run_ffmpeg(cmd, info["duration"], processing_timeout(info["duration"]), progress_callback)
    if result.returncode:
        raise RuntimeError(f"Không đổi được khung video: {result.stderr[-400:]}")
    return size


def source_volume(source: Path, target: Path, volume: float, progress_callback=None):
    info = probe(source)
    if not info["has_audio"] or volume == 1:
        return source
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(source), "-map", "0:v:0", "-map", "0:a:0",
           "-c:v", "copy", "-af", f"volume={volume}", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target)]
    result = run_ffmpeg(cmd, info["duration"], processing_timeout(info["duration"]), progress_callback)
    if result.returncode:
        raise RuntimeError("Không điều chỉnh được âm lượng gốc.")
    return target


def caption_image(text: str, width: int, height: int, style: CaptionStyle, output: Path | BinaryIO):
    from PIL import Image, ImageDraw, ImageFont
    from agent.services.post_process import _wrap_caption_lines

    image = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(image)
    size = max(8, round(height * style.size / 100))
    safe_x, safe_y = round(width * .07), round(height * style.margin / 100)
    available = max(20, width - 2 * safe_x)
    font_file = font_path(style.font_id, text)
    # Auto shrink unusually long demo/caption pages so the block stays inside the frame.
    while True:
        font = ImageFont.truetype(font_file, size)
        stroke = min(style.outline, max(0, size // 4))
        lines = [line for paragraph in text.split('\n')
                 for line in (_wrap_caption_lines(paragraph, font, draw, available - 2 * stroke - size) or [''])]
        boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=stroke) for line in lines]
        line_height = max([round(size * 1.3), *(b[3] - b[1] for b in boxes)])
        padding = round(size * .45) + stroke
        block_height = len(lines) * line_height + 2 * padding
        if block_height <= height - 2 * safe_y or size <= 8:
            break
        size -= 1
    block_width = min(available, max((b[2] - b[0] for b in boxes), default=0) + 2 * padding)
    left = {"left": safe_x, "center": (width - block_width) // 2, "right": width - safe_x - block_width}[style.align]
    top = {"top": safe_y, "middle": (height - block_height) // 2, "bottom": height - safe_y - block_height}[style.position]
    def rgba(color, alpha=255):
        return (*tuple(int(color[i:i + 2], 16) for i in (1, 3, 5)), alpha)
    draw.rounded_rectangle((left, top, left + block_width, top + block_height), radius=max(2, round(size * .22)), fill=rgba(style.background_color, round(style.background_opacity * 255)))
    for index, (line, box) in enumerate(zip(lines, boxes)):
        lw = box[2] - box[0]
        x = {"left": left + padding, "center": left + (block_width - lw) // 2, "right": left + block_width - padding - lw}[style.align]
        y = top + padding + index * line_height
        draw.text((x - box[0], y - box[1]), line, font=font, fill=rgba(style.color), stroke_width=stroke, stroke_fill=rgba(style.outline_color))
    image.save(output, "PNG")


def still_preview(source: Path, target: Path, settings: EditingSettings, time: float, text: str, *, video_id=None):
    from PIL import Image
    info = probe(source)
    time = min(time, max(0, info['duration'] - .1))
    graph, images, (width, height) = processing_filter(settings, info['width'], info['height'], video_id=video_id, time_offset=time)
    cmd = ["ffmpeg", "-y", "-v", "error", "-ss", str(time), "-i", str(source), *image_inputs(images),
           "-filter_complex", graph, "-map", "[vout]", "-frames:v", "1", str(target)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode or not target.is_file():
        raise ValueError("Không dựng được ảnh xem thử.")
    if settings.mode in {"subtitles", "voice_subtitles"} and text.strip():
        overlay = target.with_name("caption.png")
        caption_image(text, width, height, settings.captions, overlay)
        with Image.open(target) as background, Image.open(overlay) as caption:
            Image.alpha_composite(background.convert("RGBA"), caption).convert("RGB").save(target)
