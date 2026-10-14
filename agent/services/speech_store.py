"""Atomic speech documents and strict parsers. No model loads or network calls."""
import hashlib
import json
import re
import unicodedata
import uuid
from pathlib import Path

from agent.config import SHARED_OUTPUT_DIR
from agent.models.speech import Cue, SpeechDocument

ROOT = SHARED_OUTPUT_DIR / "speech"


def identifier(value: str) -> str:
    return str(uuid.UUID(str(value)))


def video_dir(vid: str) -> Path:
    return ROOT / "videos" / identifier(vid)


def atomic_json(path: Path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        staged.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def read_json(path: Path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def name_key(name: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFD", name.casefold().replace("đ", "d")) if not unicodedata.combining(c)).split())


def timeline_key(scenes: list[dict]) -> str:
    fields = ("id", "display_order", "duration", "trim_start", "trim_end", "vertical_video_media_id", "horizontal_video_media_id")
    return hashlib.sha256(json.dumps([{k: s.get(k) for k in fields} for s in scenes], sort_keys=True).encode()).hexdigest()


def load_document(vid: str) -> SpeechDocument:
    data = read_json(video_dir(vid) / "script.json", {})
    return SpeechDocument.model_validate(data)


def timestamp(value: str) -> float:
    match = re.fullmatch(r"(\d{2,}):([0-5]\d):([0-5]\d)[,.](\d{3})", value)
    if not match:
        raise ValueError(f"Mốc thời gian SRT không hợp lệ: {value}")
    h, m, s, ms = map(int, match.groups())
    return h * 3600 + m * 60 + s + ms / 1000


def split_speaker(text: str) -> tuple[str, str]:
    match = re.match(r"^\s*(?:\[([^\]\n]{1,80})\]|([^:\n]{1,80}):)\s*(.+)$", text, re.S)
    if match:
        return (match[1] or match[2]).strip(), match[3].strip()
    return "Người kể", text.strip()


def parse_srt(text: str) -> list[Cue]:
    blocks = re.split(r"\n\s*\n", text.lstrip("\ufeff").replace("\r\n", "\n").strip())
    cues = []
    for i, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if lines and lines[0].strip().isdigit():
            lines = lines[1:]
        if len(lines) < 2 or " --> " not in lines[0]:
            raise ValueError(f"Đoạn SRT {i} thiếu mốc thời gian hoặc nội dung.")
        start, end = lines[0].strip().split(" --> ", 1)
        clean = re.sub(r"</?(?:b|i|u|font)(?:\s[^>]*)?>", "", "\n".join(lines[1:]), flags=re.I)
        speaker, content = split_speaker(clean)
        cues.append(Cue(start=timestamp(start), end=timestamp(end), text=content, speaker=speaker))
    return SpeechDocument(cues=cues).cues


def parse_dialogue(text: str, scenes: list[dict]) -> list[Cue]:
    """Explicit Cảnh N headers use that scene; otherwise spread across the video."""
    groups: dict[int | None, list[tuple[str, str]]] = {}
    scene_number = None
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        header = re.fullmatch(r"(?:#{1,4}\s*)?(?:Cảnh|Canh|Scene)\s*(\d+)\s*[:.\-–]?\s*", line, re.I)
        if header:
            scene_number = int(header[1]) - 1
            if scene_number < 0 or scene_number >= len(scenes):
                raise ValueError(f"Không tìm thấy cảnh {scene_number + 1} trong video.")
            continue
        groups.setdefault(scene_number, []).append(split_speaker(line))
    if None in groups and len(groups) > 1:
        raise ValueError("Hãy đặt tiêu đề Cảnh 1 trước các câu đầu tiên, hoặc bỏ toàn bộ tiêu đề cảnh.")
    durations = [float(s.get("duration") or 8) for s in scenes]
    cues = []
    for index, lines in groups.items():
        start = sum(durations[:index]) if index is not None else 0
        duration = durations[index] if index is not None else sum(durations)
        weight = sum(max(1, len(content.split())) for _, content in lines)
        for speaker, content in lines:
            end = start + duration * max(1, len(content.split())) / weight
            cues.append(Cue(start=round(start, 3), end=round(end, 3), text=content, speaker=speaker))
            start = end
    if not cues:
        raise ValueError("Chưa có câu thoại để phân loại.")
    return SpeechDocument(cues=sorted(cues, key=lambda c: c.start)).cues


def srt_text(cues: list[Cue]) -> str:
    def stamp(seconds):
        milliseconds = round(seconds * 1000)
        seconds, ms = divmod(milliseconds, 1000)
        minutes, second = divmod(seconds, 60)
        hours, minute = divmod(minutes, 60)
        return f"{hours:02}:{minute:02}:{second:02},{ms:03}"
    return "\n\n".join(f"{i}\n{stamp(c.start)} --> {stamp(c.end)}\n{c.text}" for i, c in enumerate(cues, 1)) + "\n"
