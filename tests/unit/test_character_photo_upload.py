"""A browser-selected portrait becomes a project reference image."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.api import characters as characters_api
from agent.api import projects as projects_api
from agent.db import crud, schema


@pytest.mark.asyncio
async def test_photo_upload_links_character_and_keeps_local_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "photo.db")
    monkeypatch.setattr(projects_api, "_REFERENCE_DIR", tmp_path / "character_refs")
    monkeypatch.setattr(characters_api, "OUTPUT_DIR", tmp_path)
    client = SimpleNamespace(
        connected=True,
        upload_image=AsyncMock(return_value={"_mediaId": "22222222-2222-2222-2222-222222222222"}),
    )
    monkeypatch.setattr(projects_api, "get_flow_client", lambda: client)
    await schema.init_db()
    try:
        project = await crud.create_project(
            name="Test", id="11111111-1111-1111-1111-111111111111", material="realistic",
        )
        photo = b"\x89PNG\r\n\x1a\n" + b"sample photo"
        character = await projects_api.upload_character_photo(
            project["id"], projects_api.CharacterPhotoUpload(
                name="A", image_base64=base64.b64encode(photo).decode(),
                voice_description="Warm voice",
            ),
        )
        linked = await crud.get_project_characters(project["id"])
        assert len(linked) == 1
        assert character.id == linked[0]["id"]
        assert character.media_id == "22222222-2222-2222-2222-222222222222"
        assert character.reference_image_url == f"/api/characters/{character.id}/image"
        assert (tmp_path / "character_refs" / f"{character.id}.png").read_bytes() == photo
        assert (await characters_api.character_image(character.id)).path.name == f"{character.id}.png"
        client.upload_image.assert_awaited_once()
        assert client.upload_image.await_args.kwargs["project_id"] == project["id"]
    finally:
        await schema.close_db()
