"""Filename rules shared by uploaded, generated and mixed music."""

import re
from pathlib import Path

MUSIC_FILENAME = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,119}\.(?:mp3|wav)$", re.I)


def music_filename(title: str, identifier: str, suffix: str = ".mp3") -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", title).strip("_-")[:70] or "track"
    token = re.sub(r"[^A-Za-z0-9_-]", "", identifier)[:32] or "track"
    return f"{stem}_{token}{suffix.lower()}"


def resolve_music_file(directory: Path, filename: str) -> Path:
    if not MUSIC_FILENAME.fullmatch(filename):
        raise ValueError("Invalid music filename")
    directory = directory.resolve()
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise FileNotFoundError("Music file not found in this project")
    return path
