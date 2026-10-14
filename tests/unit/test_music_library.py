from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from starlette.requests import ClientDisconnect

from agent.api import music
from agent.services.music_library import MUSIC_FILENAME, music_filename, resolve_music_file


@pytest.fixture
def library(tmp_path, monkeypatch):
    async def directory(project_id):
        return tmp_path

    monkeypatch.setattr(music, "_music_directory", directory)
    app = FastAPI()
    app.include_router(music.router, prefix="/api")
    return app, tmp_path


async def test_upload_can_be_listed_and_played_after_reload(library):
    app, directory = library
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post('/api/music/files/project/upload', params={"filename": "Mùa phượng đỏ.MP3"}, content=b"audio bytes")
        assert uploaded.status_code == 201
        track = uploaded.json()
        assert MUSIC_FILENAME.fullmatch(track["filename"])
        saved = (await client.get('/api/music/files/project')).json()
        assert len(saved) == 1
        assert saved[0]["url"] == track["url"]
        playback = await client.get(track["url"])
        assert playback.status_code == 200
        assert playback.content == b"audio bytes"
        assert playback.headers["content-type"] == "audio/mpeg"
    assert len(list(directory.iterdir())) == 1


@pytest.mark.parametrize("filename,body,expected", [("x.wav", b"", 400), ("x.txt", b"abc", 415), ("x.mp3", b"12345", 413)])
async def test_rejected_uploads_leave_no_partial_files(library, monkeypatch, filename, body, expected):
    app, directory = library
    monkeypatch.setattr(music, "_MAX_MUSIC_UPLOAD_BYTES", 4)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post('/api/music/files/project/upload', params={"filename": filename}, content=body)
        assert response.status_code == expected
    assert not list(directory.iterdir())


@pytest.mark.parametrize("disconnect", [False, True])
async def test_stream_failure_cleans_partial_upload(library, monkeypatch, disconnect):
    _, directory = library
    monkeypatch.setattr(music, "_MAX_MUSIC_UPLOAD_BYTES", 4)

    class Upload:
        headers = {}

        async def stream(self):
            yield b"123"
            if disconnect:
                raise ClientDisconnect()
            yield b"456"

    with pytest.raises(ClientDisconnect if disconnect else HTTPException):
        await music.upload_project_music("project", Upload(), "audio.mp3")
    assert not list(directory.iterdir())


@pytest.mark.parametrize("title", ["Nhạc nền Việt Nam", "../../../track", "🎵", "a" * 500])
def test_generated_filenames_use_the_same_rules_as_uploads(title):
    filename = music_filename(title, "../identifier", ".MP3")
    assert MUSIC_FILENAME.fullmatch(filename)
    assert Path(filename).name == filename


def test_library_does_not_serve_symlinks_outside_project(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"private")
    (library / "link.mp3").symlink_to(outside)
    with pytest.raises(FileNotFoundError):
        resolve_music_file(library, "link.mp3")
