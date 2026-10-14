"""Music generation routes — Suno API integration (sunoapi.org)."""
import json
import logging
from pathlib import Path
import re
from typing import Optional
from uuid import uuid4

import httpx
import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, SecretStr

from agent import config
from agent.config import MUSIC_OUTPUT_DIR, SUNO_MODEL
from agent.sdk.persistence.sqlite_repository import SQLiteRepository
from agent.services.suno import get_suno_client
from agent.services.music_library import MUSIC_FILENAME, music_filename, resolve_music_file
from agent.utils.paths import project_dir
from agent.utils.slugify import slugify

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/music", tags=["music"])
_MAX_MUSIC_UPLOAD_BYTES = 100 * 1024 * 1024

TEMPLATES_DIR = Path(__file__).parent.parent.parent / "skills" / "song-templates"


# ─── Request Models ──────────────────────────────────────────


class SunoKeyUpdate(BaseModel):
    api_key: SecretStr


class GenerateRequest(BaseModel):
    """Generate music via Suno."""
    prompt: str = ""  # lyrics with [Verse]/[Chorus] tags (custom mode)
    style: str = ""  # musical style (e.g. "lo-fi hip hop, chill, piano")
    title: str = ""
    instrumental: bool = False
    model: str = ""  # V4, V4_5, V4_5PLUS, V4_5ALL, V5, V5_5
    custom_mode: bool = True  # False = description mode (AI writes lyrics)
    template_id: Optional[str] = None
    poll: bool = False  # if True, poll until complete before returning


class GenerateLyricsRequest(BaseModel):
    """Generate lyrics from a prompt."""
    prompt: str
    template_id: Optional[str] = None
    poll: bool = False


class ExtendRequest(BaseModel):
    """Extend/continue an existing track."""
    audio_id: str
    prompt: str = ""
    continue_at: Optional[float] = None
    model: str = ""
    default_param_flag: bool = True
    poll: bool = False


class VocalRemovalRequest(BaseModel):
    """Separate vocals from instrumental."""
    task_id: str
    audio_id: str
    poll: bool = False


class ConvertToWavRequest(BaseModel):
    """Convert a clip to WAV format."""
    task_id: str
    audio_id: str
    poll: bool = False


# ─── Helpers ─────────────────────────────────────────────────


def _load_template(template_id: str) -> dict:
    path = TEMPLATES_DIR / f"{template_id}.json"
    if not path.exists():
        raise HTTPException(404, f"Template '{template_id}' not found")
    return json.loads(path.read_text())


async def _handle_suno_call(coro):
    """Run a suno client coroutine with standard error handling."""
    try:
        return await coro
    except TimeoutError as e:
        raise HTTPException(504, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, f"Suno API error: {e.response.text[:500]}")


# ─── Templates ───────────────────────────────────────────────


@router.get("/templates")
async def list_templates():
    """List available song templates."""
    index_path = TEMPLATES_DIR / "index.json"
    if not index_path.exists():
        raise HTTPException(404, "Song templates index not found")
    return json.loads(index_path.read_text())


@router.get("/status")
async def music_status():
    """Report readiness without returning or validating the user's API key."""
    return {"configured": bool(config.SUNO_API_KEY), "model": SUNO_MODEL}


@router.put("/settings")
async def update_music_settings(body: SunoKeyUpdate):
    """Save the key locally; never return it or contact the provider when saving."""
    api_key = body.api_key.get_secret_value().strip()
    # Validate here so validation responses cannot echo a submitted secret.
    if not re.fullmatch(r"[!-~]{1,4096}", api_key):
        raise HTTPException(400, "Enter a non-empty API key without spaces (up to 4096 characters).")
    try:
        config.save_suno_key(api_key)
    except OSError:
        raise HTTPException(500, "Could not save the API key on this machine.") from None
    # No await between publishing config and updating the cached HTTP headers.
    get_suno_client().set_api_key(api_key)
    return await music_status()


@router.get("/templates/{template_id}")
async def get_template(template_id: str):
    """Get a specific song template."""
    return _load_template(template_id)


# ─── Generate ────────────────────────────────────────────────


@router.post("/generate")
async def generate_music(body: GenerateRequest):
    """Generate music. Returns taskId (or full task with clips if poll=True).

    Two modes:
    - Custom (custom_mode=True): prompt = lyrics + style + title
    - Description (custom_mode=False): prompt = natural language, AI writes everything

    Optional: pass template_id to auto-fill style from a song template.
    """
    client = get_suno_client()

    style = body.style
    prompt = body.prompt

    if body.template_id:
        tpl = _load_template(body.template_id)
        if not style:
            style = tpl.get("suno_tags", "")
        if not prompt:
            prompt = tpl.get("example_lyrics", "")

    if not prompt:
        raise HTTPException(400, "Provide prompt (lyrics or description) or template_id")

    task_id = await _handle_suno_call(client.generate(
        prompt=prompt,
        style=style,
        title=body.title,
        instrumental=body.instrumental,
        model=body.model,
        custom_mode=body.custom_mode,
    ))

    if not body.poll:
        return {"task_id": task_id}

    task = await _handle_suno_call(client.poll_task(task_id))
    return {"task_id": task_id, "task": task}


# ─── Task status / polling ───────────────────────────────────


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get task status and clips."""
    return await _handle_suno_call(get_suno_client().get_task(task_id))


@router.post("/tasks/{task_id}/poll")
async def poll_task(task_id: str):
    """Poll a task until SUCCESS or FAILED."""
    return await _handle_suno_call(get_suno_client().poll_task(task_id))


@router.post("/tasks/{task_id}/download")
async def download_task_clips(task_id: str, project_id: Optional[str] = None):
    """Download all completed clips from a task to local output directory.

    If project_id is provided, saves to output/{project_slug}/music/.
    Otherwise saves to output/_shared/music/.
    """
    client = get_suno_client()
    task = await _handle_suno_call(client.get_task(task_id))

    if task.get("status") != "SUCCESS":
        raise HTTPException(400, f"Task not complete (status: {task.get('status')})")

    response = task.get("response", {})
    clips = response.get("sunoData") or response.get("data", [])
    if not clips:
        raise HTTPException(400, "No clips in task response")

    out_dir = await _music_directory(project_id or "_shared")

    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = []

    for clip in clips:
        audio_url = clip.get("audioUrl") or clip.get("audio_url")
        if not audio_url:
            continue
        clip_id = str(clip.get("id") or uuid4().hex)
        filename = music_filename(str(clip.get("title") or "untitled"), clip_id[:8])
        out_path = out_dir / filename

        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.get(audio_url)
            r.raise_for_status()
            temporary = out_path.with_name(f".{out_path.name}.{uuid4().hex}.tmp")
            try:
                async with await anyio.open_file(temporary, "wb") as output:
                    await output.write(r.content)
                temporary.replace(out_path)
            finally:
                temporary.unlink(missing_ok=True)

        logger.info("Downloaded clip %s → %s (%.1f MB)", clip_id[:8], out_path, len(r.content) / 1e6)
        downloaded.append({
            "clip_id": clip_id,
            "title": clip.get("title"),
            "path": str(out_path),
            "filename": filename,
            "url": f"/api/music/files/{project_id or '_shared'}/{filename}",
            "size_bytes": len(r.content),
            "duration": clip.get("duration"),
        })

    return {"task_id": task_id, "downloaded": downloaded}


async def _music_directory(project_id: str) -> Path:
    if project_id == "_shared":
        return MUSIC_OUTPUT_DIR.resolve()
    project = await SQLiteRepository().get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return (project_dir(slugify(project.name)) / "music").resolve()


@router.get("/files/{project_id}/{filename}")
async def get_music_file(project_id: str, filename: str):
    """Serve only audio inside the selected project's music folder."""
    directory = await _music_directory(project_id)
    try:
        path = resolve_music_file(directory, filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    from agent.services.media_library import audio_record
    if project_id != '_shared' and audio_record(project_id, path).get('trashed_at'):
        raise HTTPException(404, 'Nhạc đang ở thùng rác.')
    media_type = "audio/mpeg" if path.suffix.lower() == ".mp3" else "audio/wav"
    return FileResponse(path, media_type=media_type, filename=filename,
                        headers={"Cache-Control": "private, no-cache"})


@router.post("/files/{project_id}/upload", status_code=201)
async def upload_project_music(project_id: str, request: Request, filename: str):
    """Upload an MP3 or WAV file into this project's local music library."""
    if project_id == "_shared":
        raise HTTPException(400, "Choose a project before uploading music")
    if len(filename) > 255:
        raise HTTPException(400, "Filename is too long")
    source_name = Path(filename.replace("\\", "/")).name
    suffix = Path(source_name).suffix.lower()
    if suffix not in {".mp3", ".wav"}:
        raise HTTPException(415, "Upload an MP3 or WAV audio file")

    content_length = request.headers.get("content-length", "")
    if content_length.isdigit() and int(content_length) > _MAX_MUSIC_UPLOAD_BYTES:
        raise HTTPException(413, "Music file must be 100 MB or smaller")
    music_dir = await _music_directory(project_id)
    music_dir.mkdir(parents=True, exist_ok=True)
    stored_name = music_filename(Path(source_name).stem, uuid4().hex[:10], suffix)
    target = (music_dir / stored_name).resolve()
    if not target.is_relative_to(music_dir):
        raise HTTPException(400, "Invalid music filename")
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    total = 0
    try:
        async with await anyio.open_file(temporary, "wb") as output:
            async for chunk in request.stream():
                total += len(chunk)
                if total > _MAX_MUSIC_UPLOAD_BYTES:
                    raise HTTPException(413, "Music file must be 100 MB or smaller")
                await output.write(chunk)
        if total == 0:
            raise HTTPException(400, "The uploaded file is empty")
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "filename": stored_name,
        "title": Path(source_name).stem,
        "url": f"/api/music/files/{project_id}/{stored_name}",
        "size_bytes": total,
    }


@router.get("/files/{project_id}")
async def list_project_music(project_id: str):
    from agent.services.media_library import audio_record, audio_records
    music_dir = await _music_directory(project_id)
    if not music_dir.is_dir():
        return []
    records = audio_records(project_id)
    return [
        {"filename": path.name, "url": f"/api/music/files/{project_id}/{path.name}",
         "size_bytes": path.stat().st_size, "modified_at": path.stat().st_mtime}
        for path in sorted(music_dir.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True)
        if path.is_file() and not path.is_symlink() and MUSIC_FILENAME.fullmatch(path.name)
        and not audio_record(project_id, path, records).get('trashed_at')
    ]


# ─── Lyrics ──────────────────────────────────────────────────


@router.post("/generate-lyrics")
async def generate_lyrics(body: GenerateLyricsRequest):
    """Generate lyrics from a natural language prompt. Returns taskId."""
    client = get_suno_client()

    prompt = body.prompt
    if body.template_id:
        tpl = _load_template(body.template_id)
        guidelines = tpl.get("lyrics_guidelines", {})
        tips = guidelines.get("tips", [])
        if tips:
            prompt += f"\n\nStyle guidelines: {'; '.join(tips)}"

    task_id = await _handle_suno_call(client.generate_lyrics(prompt))

    if not body.poll:
        return {"task_id": task_id}

    task = await _handle_suno_call(client.poll_task(task_id))
    return {"task_id": task_id, "task": task}


# ─── Extend ──────────────────────────────────────────────────


@router.post("/extend")
async def extend_music(body: ExtendRequest):
    """Extend/continue an existing track. Returns taskId."""
    client = get_suno_client()
    task_id = await _handle_suno_call(client.extend(
        audio_id=body.audio_id,
        prompt=body.prompt,
        continue_at=body.continue_at,
        model=body.model,
        default_param_flag=body.default_param_flag,
    ))

    if not body.poll:
        return {"task_id": task_id}

    task = await _handle_suno_call(client.poll_task(task_id))
    return {"task_id": task_id, "task": task}


# ─── Vocal Removal ───────────────────────────────────────────


@router.post("/vocal-removal")
async def vocal_removal(body: VocalRemovalRequest):
    """Separate vocals from instrumental. Returns taskId."""
    client = get_suno_client()
    new_task_id = await _handle_suno_call(client.vocal_removal(
        task_id=body.task_id,
        audio_id=body.audio_id,
    ))

    if not body.poll:
        return {"task_id": new_task_id}

    task = await _handle_suno_call(client.poll_task(new_task_id))
    return {"task_id": new_task_id, "task": task}


# ─── Convert to WAV ─────────────────────────────────────────


@router.post("/convert-to-wav")
async def convert_to_wav(body: ConvertToWavRequest):
    """Convert a clip to WAV format. Returns taskId."""
    client = get_suno_client()
    new_task_id = await _handle_suno_call(client.convert_to_wav(
        task_id=body.task_id,
        audio_id=body.audio_id,
    ))

    if not body.poll:
        return {"task_id": new_task_id}

    task = await _handle_suno_call(client.poll_task(new_task_id))
    return {"task_id": new_task_id, "task": task}


# ─── Callback (webhook from Suno) ────────────────────────────


@router.post("/callback")
async def suno_callback(body: dict):
    """Receive webhook from Suno when a task completes.

    Suno POSTs: {code: 200, data: {data: [{title, audio_url, ...}]}, msg: "success"}
    Logged for now — extend with DB persistence or WS push as needed.
    """
    code = body.get("code")
    msg = body.get("msg", "")
    clips = body.get("data", {}).get("data", [])
    logger.info("Suno callback: code=%s msg=%s clips=%d", code, msg, len(clips))
    for clip in clips:
        logger.info("  clip: %s — %s", clip.get("id", "?")[:12], clip.get("title", "untitled"))
    return {"received": True}


# ─── Credits ─────────────────────────────────────────────────


@router.get("/credits")
async def get_credits():
    """Get Suno credits/quota."""
    return await _handle_suno_call(get_suno_client().get_credits())
