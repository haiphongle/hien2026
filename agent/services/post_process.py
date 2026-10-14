"""Post-processing: trim, merge, add music via ffmpeg."""
import json
import subprocess
import logging
import math
import tempfile
from pathlib import Path
from collections.abc import Callable

from agent.services.ffmpeg_progress import processing_timeout, run_ffmpeg

logger = logging.getLogger(__name__)

_SUBTITLE_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
)

_FLOAT_MIN = 0.0
_FLOAT_MAX = 2.0


def _clamp_float(value: float, name: str, lo: float = _FLOAT_MIN, hi: float = _FLOAT_MAX) -> float:
    """Clamp float to [lo, hi] and warn if out of bounds."""
    if value < lo or value > hi:
        logger.warning("Parameter %s=%s out of bounds [%s, %s], clamping", name, value, lo, hi)
        return max(lo, min(hi, value))
    return value


def _media_duration(path: str) -> float | None:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        logger.error("Could not read audio duration for %s: %s", path, result.stderr[-200:] if result.stderr else "no output")
        return None


def _video_audio_info(path: str) -> tuple[float, bool] | None:
    """Read duration and original-audio availability consistently for both mixers."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        metadata = json.loads(probe.stdout)
        duration = float(metadata["format"]["duration"])
        if probe.returncode or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid video duration")
        return duration, any(stream.get("codec_type") == "audio" for stream in metadata.get("streams", []))
    except (ValueError, AttributeError, KeyError, TypeError):
        logger.error("ffprobe failed for %s: %s", path, probe.stderr[-200:] if probe.stderr else "invalid metadata")
        return None


def _atempo_chain(speed: float) -> str:
    """Build legal FFmpeg atempo filters for a speed-up ratio."""
    filters = []
    while speed > 2.0:
        filters.append("atempo=2.0")
        speed /= 2.0
    if speed > 1.01:
        filters.append(f"atempo={speed:.5f}")
    return ",".join(filters)


def trim_video(input_path: str, output_path: str, start: float, end: float) -> bool:
    """Trim video to [start, end] seconds."""
    if not Path(input_path).exists():
        logger.error("trim_video: input file not found: %s", input_path)
        return False
    duration = end - start
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-ss", str(start), "-t", str(duration),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-force_key_frames", "expr:gte(t,0)",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error("Trim failed: %s", result.stderr[-200:])
        return False
    return True


def merge_videos(video_paths: list[str], output_path: str,
                 progress_callback: Callable[[float], None] | None = None) -> bool:
    """Remux Flow clips to one MP4 time base before lossless concatenation.

    Flow can return H.264 clips with 1/12288 and 1/90000 time bases in the
    same project. Concatenating their packets directly stretches timestamps.
    Remuxing changes container timestamps without recompressing the images.
    """
    if not video_paths:
        return False
    with tempfile.TemporaryDirectory(prefix="frameweaver-concat-") as directory:
        work = Path(directory)
        expected_duration = 0.0
        for index, path in enumerate(video_paths):
            duration = _media_duration(path)
            if duration is None or not math.isfinite(duration) or duration <= 0:
                return False
            expected_duration += duration
            result = subprocess.run([
                "ffmpeg", "-v", "error", "-y", "-i", path, "-map", "0:v:0", "-map", "0:a?",
                "-c", "copy", "-video_track_timescale", "90000", str(work / f"{index}.mp4"),
            ], capture_output=True, text=True, timeout=120)
            if result.returncode:
                logger.error("Clip timestamp normalization failed: %s", result.stderr[-300:])
                return False
            if progress_callback:
                progress_callback(0.6 * (index + 1) / len(video_paths))
        concat_file = work / "clips.ffconcat"
        concat_file.write_text("".join(f"file '{index}.mp4'\n" for index in range(len(video_paths))))
        command = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
            "-c:v", "copy", "-c:a", "copy", "-movflags", "+faststart", output_path,
        ]
        result = run_ffmpeg(command, expected_duration, 120,
                            (lambda value: progress_callback(0.6 + value * 0.4)) if progress_callback else None)
        if result.returncode:
            logger.error("Merge failed: %s", result.stderr[-300:])
            return False
        actual_duration = _media_duration(output_path)
        if actual_duration is None or not math.isfinite(actual_duration) or abs(actual_duration - expected_duration) > max(0.5, len(video_paths) * 0.1):
            logger.error("Merged clip duration mismatch: expected %.3fs, got %s", expected_duration, actual_duration)
            return False
        return True


def add_narration(video_path: str, narration_path: str, output_path: str,
                  narration_volume: float = 1.0, sfx_volume: float = 0.4,
                  fade_in: float = 0.5, fade_out: float = 0.5) -> bool:
    """Overlay narration audio on video, ducking the existing SFX track."""
    if not Path(video_path).exists():
        logger.error("add_narration: video file not found: %s", video_path)
        return False
    if not Path(narration_path).exists():
        logger.error("add_narration: narration file not found: %s", narration_path)
        return False

    # Clamp float params to prevent filter injection
    narration_volume = _clamp_float(narration_volume, "narration_volume")
    sfx_volume = _clamp_float(sfx_volume, "sfx_volume")
    fade_in = _clamp_float(fade_in, "fade_in")
    fade_out = _clamp_float(fade_out, "fade_out")

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", video_path],
        capture_output=True, text=True, timeout=30,
    )
    try:
        duration = float(probe.stdout.strip())
    except (ValueError, AttributeError):
        logger.error("ffprobe failed for %s: %s", video_path, probe.stderr[-200:] if probe.stderr else "no output")
        return False
    fade_start = max(0, duration - fade_out)

    cmd = [
        "ffmpeg", "-y", "-i", video_path, "-i", narration_path,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-filter_complex",
        f"[0:a]volume={sfx_volume}[sfx];[1:a]volume={narration_volume},afade=t=in:st=0:d={fade_in},afade=t=out:st={fade_start}:d={fade_out}[narr];[sfx][narr]amerge=inputs=2,pan=stereo|c0=c0+c2|c1=c1+c3[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error("Add narration failed: %s", result.stderr[-200:])
        return False
    return True


def add_music(video_path: str, music_path: str, output_path: str,
              music_volume: float = 0.3, fade_in: float = 2.0, fade_out: float = 3.0,
              progress_callback: Callable[[float], None] | None = None) -> bool:
    """Overlay background music on video, with or without original clip audio."""
    if not Path(video_path).exists():
        logger.error("add_music: video file not found: %s", video_path)
        return False
    if not Path(music_path).exists():
        logger.error("add_music: music file not found: %s", music_path)
        return False

    info = _video_audio_info(video_path)
    if info is None:
        return False
    duration, has_audio = info
    music_volume = max(0.0, min(2.0, float(music_volume)))
    fade_in = max(0.0, min(30.0, float(fade_in)))
    fade_out = max(0.0, min(min(30.0, duration), float(fade_out)))
    fade_start = max(0.0, duration - fade_out)
    music_filter = f"volume={music_volume},afade=t=in:st=0:d={fade_in}"
    if fade_out > 0:
        music_filter += f",afade=t=out:st={fade_start}:d={fade_out}"

    if has_audio:
        filter_complex = (
            f"[0:a]volume=1.0,apad,atrim=0:{duration}[base];"
            f"[1:a]{music_filter}[music];"
            "[base][music]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[aout]"
        )
    else:
        filter_complex = f"[1:a]{music_filter}[aout]"

    cmd = ["ffmpeg", "-y", "-i", video_path, "-stream_loop", "-1", "-i", music_path,
           "-filter_complex", filter_complex, "-map", "0:v:0", "-map", "[aout]",
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", str(duration),
           "-shortest", "-movflags", "+faststart", output_path]
    result = run_ffmpeg(cmd, duration, processing_timeout(duration, minimum=120, multiplier=2), progress_callback)
    if result.returncode != 0:
        logger.error("Add music failed: %s", result.stderr[-200:])
        return False
    return True


def add_scene_narrations(video_path: str, narration_tracks: list[tuple], output_path: str,
                         narration_volume: float = 1.0, source_volume: float = 0.4,
                         progress_callback: Callable[[float], None] | None = None) -> bool:
    """Mix narration at scene offsets and speed up long reads to fit each scene."""
    if not Path(video_path).is_file() or not narration_tracks:
        logger.error("add_scene_narrations: video and at least one narration are required")
        return False
    if any(len(track) not in {2, 3} or not Path(track[0]).is_file() or track[1] < 0 or (len(track) == 3 and track[2] <= 0) for track in narration_tracks):
        logger.error("add_scene_narrations: invalid narration input")
        return False

    info = _video_audio_info(video_path)
    if info is None:
        return False
    duration, has_audio = info

    normalized_tracks = [
        (track[0], track[1], float(track[2]) if len(track) == 3 else max(0.1, duration - float(track[1])))
        for track in narration_tracks
    ]

    narration_volume = max(0.0, min(2.0, float(narration_volume)))
    source_volume = max(0.0, min(2.0, float(source_volume)))
    filters = []
    if has_audio:
        filters.append(f"[0:a]volume={source_volume},apad,atrim=0:{duration}[base]")
    else:
        filters.append(f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:{duration}[base]")

    labels = ["[base]"]
    for index, (path, offset, clip_duration) in enumerate(normalized_tracks, start=1):
        delay_ms = int(max(0.0, offset) * 1000)
        label = f"[narr{index}]"
        voice_duration = _media_duration(path)
        if voice_duration is None:
            return False
        tempo = _atempo_chain(voice_duration / clip_duration) if voice_duration > clip_duration else ""
        speed_filter = f"{tempo}," if tempo else ""
        filters.append(
            f"[{index}:a]aresample=48000,{speed_filter}volume={narration_volume},"
            f"atrim=0:{clip_duration},apad,adelay={delay_ms}:all=1,atrim=0:{duration}{label}"
        )
        labels.append(label)
    filters.append(
        "".join(labels) + f"amix=inputs={len(labels)}:duration=first:dropout_transition=2:normalize=0[aout]"
    )

    cmd = ["ffmpeg", "-y", "-i", video_path]
    for path, _, _ in normalized_tracks:
        cmd.extend(["-i", path])
    cmd.extend([
        "-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", str(duration),
        "-shortest", "-movflags", "+faststart", output_path,
    ])
    result = run_ffmpeg(cmd, duration, processing_timeout(duration, minimum=120, multiplier=2), progress_callback)
    if result.returncode != 0:
        logger.error("Narration mix failed: %s", result.stderr[-300:])
        return False
    return True


def _wrap_caption_lines(text: str, font, draw, max_width: int) -> list[str]:
    """Wrap caption text by rendered width so Vietnamese diacritics fit safely."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        # A long URL or unbroken token must not run off the edge of the frame.
        if draw.textlength(word, font=font) > max_width:
            if current:
                lines.append(current)
                current = ""
            for letter in word:
                if current and draw.textlength(current + letter, font=font) > max_width:
                    lines.append(current)
                    current = ""
                current += letter
            continue
        candidate = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _caption_font_size(width: int, height: int) -> int:
    return max(8, min(76, round(width * 0.036), round(height * 0.055)))


def _make_caption_image(text: str, width: int, height: int, font_path: str, output_path: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font_size = _caption_font_size(width, height)
    font = ImageFont.truetype(font_path, font_size)
    max_text_width = round(width * 0.86)
    lines = _wrap_caption_lines(text, font, draw, max_text_width)
    line_height = round(font_size * 1.25)
    padding_x = round(font_size * 0.58)
    padding_y = round(font_size * 0.38)
    measured = [draw.textbbox((0, 0), line, font=font, stroke_width=1) for line in lines]
    text_width = max((bbox[2] - bbox[0] for bbox in measured), default=0)
    block_width = min(width - round(width * 0.06), text_width + 2 * padding_x)
    block_height = len(lines) * line_height + 2 * padding_y
    left = (width - block_width) // 2
    bottom_margin = round(height * 0.075)
    top = max(round(height * 0.55), height - bottom_margin - block_height)
    draw.rounded_rectangle(
        (left, top, left + block_width, top + block_height),
        radius=max(8, round(font_size * 0.22)),
        fill=(0, 0, 0, 178),
    )
    for index, line in enumerate(lines):
        y = top + padding_y + index * line_height
        draw.text(
            (width // 2, y), line, anchor="mt", font=font,
            fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 255),
        )
    image.save(output_path, format="PNG")


def add_scene_subtitles(video_path: str, scenes: list[dict], output_path: str,
                        narration_durations: dict[str, float] | None = None,
                        progress_callback: Callable[[float], None] | None = None,
                        caption_style: dict | None = None) -> bool:
    """Burn each scene's narration onto the video using transparent PNG overlays.

    The local FFmpeg build on macOS does not include libass/drawtext. Pillow renders
    the Vietnamese text and FFmpeg's built-in concat + overlay filters composite it.
    Text advances in short pages within each scene; timing is estimated by text length.
    """
    if not Path(video_path).is_file():
        logger.error("add_scene_subtitles: video file not found: %s", video_path)
        return False
    if Path(video_path).resolve() == Path(output_path).resolve():
        logger.error("Subtitle output must be different from the source video")
        return False

    font_path = next((path for path in _SUBTITLE_FONT_CANDIDATES if Path(path).is_file()), None)
    from agent.services.editing import cjk_text, font_path as selected_font
    style = None
    if caption_style is not None:
        from agent.models.editing import CaptionStyle
        style = CaptionStyle.model_validate(caption_style)
        font_path = selected_font(style.font_id)
    if not font_path:
        logger.error("Subtitle font not found; install a Vietnamese-capable system font")
        return False

    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.error("Subtitle rendering requires Pillow; install requirements.txt")
        return False

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-show_entries", "format=duration", "-of", "json", video_path],
            capture_output=True, text=True, timeout=30,
        )
        metadata = json.loads(probe.stdout)
        duration = float(metadata["format"]["duration"])
        width = int(metadata["streams"][0]["width"])
        height = int(metadata["streams"][0]["height"])
        if probe.returncode or not math.isfinite(duration) or duration <= 0 or width <= 0 or height <= 0:
            raise ValueError("invalid video metadata")
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        logger.error("Could not inspect video for subtitles: %s", exc)
        return False

    try:
        with tempfile.TemporaryDirectory(prefix="frameweaver-captions-") as temp_dir:
            work_dir = Path(temp_dir)
            blank_path = work_dir / "caption_blank.png"
            Image.new("RGBA", (width, height), (0, 0, 0, 0)).save(blank_path, format="PNG")

            events: list[tuple[float, int, Path]] = [(0.0, 0, blank_path)]
            offset = 0.0
            sequence = 1
            font_size = max(8, round(height * style.size / 100)) if style else _caption_font_size(width, height)
            measure_image = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
            measure_draw = ImageDraw.Draw(measure_image)
            font = ImageFont.truetype(font_path, font_size)
            active_font_path = font_path

            for scene in sorted(scenes, key=lambda item: item.get("display_order", 0)):
                scene_start = min(offset, duration)
                scene_duration = max(0.0, float(scene.get("duration") or 8))
                if not math.isfinite(scene_duration):
                    raise ValueError("Invalid scene duration")
                scene_end = min(scene_start + scene_duration, duration)
                events.append((scene_start, sequence, blank_path))
                sequence += 1

                text = " ".join(str(scene.get("narrator_text") or "").split())
                # Long reads are sped up by add_scene_narrations; short reads end early.
                voice_duration = (narration_durations or {}).get(scene.get("id"), scene_duration)
                if not math.isfinite(voice_duration) or voice_duration <= 0:
                    raise ValueError("Invalid narration duration")
                caption_end = min(scene_end, scene_start + voice_duration)
                if text and scene_end > scene_start:
                    caption_font_path = selected_font(style.font_id, text) if style else selected_font("cjk") if cjk_text(text) else font_path
                    if caption_font_path != active_font_path:
                        font = ImageFont.truetype(caption_font_path, font_size)
                        active_font_path = caption_font_path
                    max_width = round(width * 0.86) - (2 * style.outline + font_size if style else 0)
                    lines = _wrap_caption_lines(text, font, measure_draw, max(20, max_width))
                    pages = [lines[index:index + 2] for index in range(0, len(lines), 2)]
                    page_texts = [" ".join(page) for page in pages]
                    weights = [max(1, len(page_text)) for page_text in page_texts]
                    total_weight = sum(weights)
                    cursor = scene_start
                    for page_index, (page_text, weight) in enumerate(zip(page_texts, weights)):
                        page_end = caption_end if page_index == len(page_texts) - 1 else cursor + (caption_end - scene_start) * weight / total_weight
                        frame_path = work_dir / f"caption_{sequence:04d}.png"
                        if style:
                            from agent.services.editing import caption_image
                            caption_image(page_text, width, height, style, frame_path)
                        else:
                            _make_caption_image(page_text, width, height, caption_font_path, frame_path)
                        events.append((cursor, sequence, frame_path))
                        sequence += 1
                        cursor = page_end
                    events.append((caption_end, sequence, blank_path))
                    sequence += 1

                offset += scene_duration
                if offset >= duration:
                    break

            events.append((duration, sequence, blank_path))
            events.sort(key=lambda event: (event[0], event[1]))
            # Keep the last state at an identical timestamp (caption after scene-clear).
            unique_events: list[tuple[float, Path]] = []
            for event_time, _order, frame_path in events:
                if unique_events and abs(unique_events[-1][0] - event_time) < 0.0001:
                    unique_events[-1] = (event_time, frame_path)
                else:
                    unique_events.append((event_time, frame_path))

            concat_file = work_dir / "captions.ffconcat"
            lines = ["ffconcat version 1.0"]
            for index, (event_time, frame_path) in enumerate(unique_events):
                lines.append(f"file '{frame_path.name}'")
                if index + 1 < len(unique_events):
                    frame_duration = unique_events[index + 1][0] - event_time
                    if frame_duration > 0:
                        lines.append(f"duration {frame_duration:.6f}")
            lines.append(f"file '{unique_events[-1][1].name}'")
            concat_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

            command = [
                "ffmpeg", "-y", "-v", "error", "-i", video_path,
                "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-filter_complex", "[0:v:0][1:v:0]overlay=eof_action=pass:format=auto[vout]",
                "-map", "[vout]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-t", f"{duration:.6f}",
                "-movflags", "+faststart", output_path,
            ]
            result = run_ffmpeg(command, duration, processing_timeout(duration), progress_callback)
            if result.returncode != 0:
                logger.error("Burning subtitles failed: %s", result.stderr[-500:])
                Path(output_path).unlink(missing_ok=True)
                return False
        return True
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        logger.exception("Could not render scene subtitles: %s", exc)
        Path(output_path).unlink(missing_ok=True)
        return False
