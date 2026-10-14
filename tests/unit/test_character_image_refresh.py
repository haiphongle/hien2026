"""Refreshing previews must re-sign existing references and preserve failed ones."""
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from agent.api import projects as projects_api
from agent.db import crud, schema
from agent.services.flow_batch import MediaUrls
from agent.services.flow_client import FlowClient

MEDIA = "22222222-2222-2222-2222-222222222222"
FRESH_URL = f"https://lh3.googleusercontent.com/image/{MEDIA}?sig=fresh"


@pytest.fixture
async def references(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "references.db")
    await schema.init_db()
    try:
        project = await crud.create_project(name="References", material="realistic")
        character = await crud.create_character(name="Existing", media_id=MEDIA)
        old_url = f"/api/characters/{character['id']}/image"
        await crud.update_character(character["id"], reference_image_url=old_url)
        await crud.link_character_to_project(project["id"], character["id"])
        client = FlowClient()
        client._extensions = {object(): {}}
        client._batch_media_urls = AsyncMock(return_value=MediaUrls(MEDIA, image=FRESH_URL))
        monkeypatch.setattr(projects_api, "get_flow_client", lambda: client)
        yield project["id"], character["id"], old_url, client
    finally:
        await schema.close_db()


async def test_single_refresh_replaces_local_preview_with_fresh_link(references):
    pid, cid, _, client = references
    result = await projects_api.refresh_character_image(pid, cid)
    assert result["refreshed"] == 1
    assert result["characters"][0]["reference_image_url"] == FRESH_URL
    assert result["characters"][0]["media_id"] == MEDIA
    client._batch_media_urls.assert_awaited_once_with(MEDIA)


@pytest.mark.parametrize("failure", [RuntimeError("Flow unavailable"), MediaUrls(MEDIA, image="https://untrusted.example/image")])
async def test_failed_refresh_preserves_previous_reference(references, failure):
    pid, cid, old_url, client = references
    if isinstance(failure, Exception):
        client._batch_media_urls.side_effect = failure
    else:
        client._batch_media_urls.return_value = failure
    with pytest.raises(HTTPException) as error:
        await projects_api.refresh_character_image(pid, cid)
    assert error.value.status_code == 502
    assert (await crud.get_character(cid))["reference_image_url"] == old_url


async def test_disconnected_extension_reports_failure_instead_of_zero_success(references):
    pid, _, _, client = references
    client._extensions.clear()
    with pytest.raises(HTTPException) as error:
        await projects_api.refresh_character_images(pid)
    assert error.value.status_code == 503
    client._batch_media_urls.assert_not_awaited()


async def test_character_from_another_project_is_not_refreshed(references):
    pid, _, _, client = references
    other = await crud.create_character(name="Other", media_id=MEDIA)
    with pytest.raises(HTTPException) as error:
        await projects_api.refresh_character_image(pid, other["id"])
    assert error.value.status_code == 404
    client._batch_media_urls.assert_not_awaited()


async def test_bulk_refresh_reports_partial_results_and_skips_no_image(references):
    pid, _, _, client = references
    failed = await crud.create_character(name="Unavailable", media_id="33333333-3333-3333-3333-333333333333", reference_image_url="old")
    empty = await crud.create_character(name="Not generated")
    await crud.link_character_to_project(pid, failed["id"])
    await crud.link_character_to_project(pid, empty["id"])

    async def media_urls(media_id):
        if media_id == MEDIA:
            return MediaUrls(MEDIA, image=FRESH_URL)
        raise RuntimeError("Not found")

    client._batch_media_urls.side_effect = media_urls
    result = await projects_api.refresh_character_images(pid)
    assert (result["refreshed"], result["failed"], result["skipped"]) == (1, 1, 1)
    assert (await crud.get_character(failed["id"]))["reference_image_url"] == "old"


async def test_regenerated_reference_is_not_overwritten_by_late_refresh(references):
    pid, cid, _, client = references

    async def changed_during_refresh(media_id):
        await crud.update_character(cid, media_id="44444444-4444-4444-4444-444444444444", reference_image_url="new-generation")
        return MediaUrls(media_id, image=FRESH_URL)

    client._batch_media_urls.side_effect = changed_during_refresh
    with pytest.raises(HTTPException):
        await projects_api.refresh_character_image(pid, cid)
    assert (await crud.get_character(cid))["reference_image_url"] == "new-generation"
