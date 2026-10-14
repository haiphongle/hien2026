import json
import stat

import httpx
import pytest
from fastapi import FastAPI

from agent import config
from agent.api.music import router
from agent.services import suno


@pytest.fixture
def suno_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "SUNO_SETTINGS_FILE", tmp_path / ".flowkit" / "suno.json")
    monkeypatch.setattr(config, "SUNO_API_KEY", "old-test-key")
    monkeypatch.setenv("SUNO_API_KEY", "environment-test-key")
    monkeypatch.setattr(suno, "_suno_client", None)
    app = FastAPI()
    app.include_router(router, prefix="/api")
    return app


async def test_saved_key_is_private_persistent_and_applied_to_cached_http_client(suno_settings):
    sent_headers = []

    def provider(request):
        sent_headers.append(request.headers["authorization"])
        return httpx.Response(200, json={"code": 200, "data": 50})

    client = suno.get_suno_client()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(provider), headers=client._headers,
    ) as provider_http:
        client._client = provider_http
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=suno_settings), base_url="http://test",
        ) as api:
            result = await api.put("/api/music/settings", json={"api_key": "  new-test-key  "})
            assert result.status_code == 200
            assert result.json() == {"configured": True, "model": config.SUNO_MODEL}
            assert sent_headers == []  # Saving never spends credits or calls the provider.
            status = await api.get("/api/music/status")
            assert "new-test-key" not in result.text + status.text
            assert "api_key" not in result.json()

        assert await client.get_credits() == 50
        assert sent_headers == ["Bearer new-test-key"]

    assert config._load_suno_key() == "new-test-key"  # Wins over the older env key after restart.
    assert json.loads(config.SUNO_SETTINGS_FILE.read_text()) == {"api_key": "new-test-key"}
    assert stat.S_IMODE(config.SUNO_SETTINGS_FILE.stat().st_mode) == 0o600
    assert stat.S_IMODE(config.SUNO_SETTINGS_FILE.parent.stat().st_mode) == 0o700
    assert suno.SunoClient().api_key == "new-test-key"


@pytest.mark.parametrize("key", ["", " \n", "secret with spaces", "secret\nheader", "x" * 4097],
                         ids=["empty", "blank", "spaces", "newline", "too-long"])
async def test_invalid_key_does_not_replace_existing_key_or_echo_secret(suno_settings, key):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=suno_settings), base_url="http://test",
    ) as api:
        response = await api.put("/api/music/settings", json={"api_key": key})
    assert response.status_code == 400
    assert config.SUNO_API_KEY == "old-test-key"
    assert not config.SUNO_SETTINGS_FILE.exists()
    if key.strip():
        assert key not in response.text


async def test_failed_save_keeps_previous_key_and_cleans_temporary_file(suno_settings, monkeypatch):
    config.save_suno_key("old-test-key")
    client = suno.get_suno_client()

    def fail_replace(*args):
        raise OSError("disk failure")

    monkeypatch.setattr(config.os, "replace", fail_replace)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=suno_settings), base_url="http://test",
    ) as api:
        response = await api.put("/api/music/settings", json={"api_key": "new-test-key"})
    assert response.status_code == 500
    assert "new-test-key" not in response.text
    assert config.SUNO_API_KEY == client.api_key == config._load_suno_key() == "old-test-key"
    assert list(config.SUNO_SETTINGS_FILE.parent.iterdir()) == [config.SUNO_SETTINGS_FILE]


def test_existing_environment_configuration_still_works(suno_settings):
    assert config._load_suno_key() == "environment-test-key"
