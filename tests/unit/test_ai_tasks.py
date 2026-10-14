"""AI settings and Studio suggestions must produce reviewable, correctly routed drafts."""
import json
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException

from agent import config
from agent.api import prompt_suggestions as api, providers
from agent.models.stickman import Storyboard, SRTStoryboard
from agent.services import ai_tasks, cli_providers, subtitle_translation

PID = UUID("11111111-1111-1111-1111-111111111111")
CID = UUID("22222222-2222-2222-2222-222222222222")
PAIR = {"image_prompt": "A cat reaches for the cup.",
        "video_prompt": "0-3s: Reach for cup. 3-6s: Lift the cup. 6-8s: Set it down. No speech."}


@pytest.fixture
def project(monkeypatch, tmp_path):
    monkeypatch.setattr(api.crud, "get_project", AsyncMock(return_value={"id": str(PID), "status": "ACTIVE"}))
    monkeypatch.setattr(api.crud, "get_project_characters", AsyncMock(return_value=[
        {"id": str(CID), "name": "Cat", "description": "Reference", "media_id": str(CID)}]))
    monkeypatch.setattr(api, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(config, "CLI_PROVIDERS", {"active": "claude", "roles": {}})
    caller = AsyncMock(return_value=json.dumps(PAIR))
    monkeypatch.setattr(ai_tasks, "complete", caller)
    return caller


async def test_suggestion_text_only_never_reads_images_or_mutates_data(project, monkeypatch):
    monkeypatch.setattr(api, "reference_paths", lambda _: pytest.fail("Images were not authorized"))
    monkeypatch.setattr(api.crud, "create_scene", AsyncMock(side_effect=AssertionError("Must not create scenes")))
    result = await api.suggest_prompts(PID, api.SuggestionRequest(action="Reach for a cup", character_ids=[CID]))
    assert result["images_used"] == 0
    assert result["image_prompt"] == PAIR["image_prompt"]
    assert project.call_args.kwargs["images"] == ()
    assert '"name": "Cat"' in project.call_args.args[1]


async def test_opted_in_images_only_from_selected_project(project, tmp_path):
    root = tmp_path / "character_refs"
    root.mkdir()
    photo = root / f"{CID}.jpg"
    photo.write_bytes(b"test-reference")
    result = await api.suggest_prompts(PID, api.SuggestionRequest(action="Reach for cup", character_ids=[CID], include_images=True))
    assert result["images_used"] == 1
    assert project.call_args.kwargs["images"] == (photo,)


@pytest.mark.parametrize("body,status", [
    ({"character_ids": [PID]}, 400),
    ({"character_ids": [CID, CID]}, 400),
    ({"dialogue": "Xin chào"}, 400),
    ({"speaker_id": CID}, 400),
    ({"character_ids": [CID], "include_images": True}, 422),
    ({"expected_role": {"provider": "claude", "model": None, "effort": None}}, 409),
])
async def test_invalid_references_or_config_do_not_call_ai(project, body, status):
    with pytest.raises(HTTPException) as caught:
        await api.suggest_prompts(PID, api.SuggestionRequest(action="Reach for cup", **body))
    assert caught.value.status_code == status
    project.assert_not_awaited()


@pytest.mark.parametrize("answer", [{}, {**PAIR, "image_prompt": "   "}, {**PAIR, "video_prompt": "no timecodes"}])
async def test_malformed_prompt_not_accepted(project, answer):
    project.return_value = json.dumps(answer)
    with pytest.raises(HTTPException) as caught:
        await api.suggest_prompts(PID, api.SuggestionRequest(action="Reach for cup"))
    assert caught.value.status_code == 422


async def test_dialogue_must_be_preserved(project):
    with pytest.raises(HTTPException) as caught:
        await api.suggest_prompts(PID, api.SuggestionRequest(action="Reach for cup", character_ids=[CID], speaker_id=CID, dialogue="Xin chào"))
    assert caught.value.status_code == 422


def test_defaults_and_translation_overrides_preserve_role_settings(monkeypatch):
    monkeypatch.setattr(config, "CLI_PROVIDERS", {"active": "agy", "roles": {}})
    assert cli_providers.resolve_role("video_review")["provider"] == "agy"
    assert cli_providers.resolve_role("subtitle_translation")["provider"] == "codex"
    role = {"provider": "codex", "model": "custom-model", "effort": "high"}
    config.CLI_PROVIDERS["roles"]["subtitle_translation"] = role
    assert subtitle_translation.assignment("configured") == role
    assert subtitle_translation.assignment("codex") == role
    assert subtitle_translation.assignment("claude") == {"provider": "claude", "model": None, "effort": None}


async def test_codex_receives_model_effort_images_and_no_tools(monkeypatch, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"image")
    async def process(args, **options):
        assert args[args.index("--model") + 1] == "chosen-model"
        assert 'model_reasoning_effort="high"' in args
        assert "shell_tool" in args and "--ignore-user-config" in args
        assert Path(args[args.index("-i") + 1]).read_bytes() == b"image"
        assert str(tmp_path) != options["cwd"]
        Path(args[args.index("-o") + 1]).write_text('{"ok": true}')
    monkeypatch.setattr(ai_tasks.media_jobs, "process", process)
    raw = await ai_tasks.complete({"provider": "codex", "model": "chosen-model", "effort": "high"},
                                 "Test", schema={}, images=(photo,), binary="codex")
    assert json.loads(raw) == {"ok": True}


@pytest.mark.parametrize("contract", [Storyboard, SRTStoryboard])
async def test_codex_storyboard_schema_requires_nested_default_fields(monkeypatch, contract):
    schema = contract.model_json_schema()
    original = json.loads(json.dumps(schema))

    async def process(args, **options):
        sent = json.loads(Path(args[args.index("--output-schema") + 1]).read_text())
        assert sent["required"] == list(sent["properties"])
        assert sent["additionalProperties"] is False
        scene = sent["$defs"]["StoryScene" if contract is Storyboard else "SRTScene"]
        assert scene["required"] == list(scene["properties"])
        assert scene["additionalProperties"] is False
        for field in ("speaker", "emotion", "gesture"):
            assert "default" not in scene["properties"][field]
            assert scene["properties"][field]["enum"] == original["$defs"][scene["title"]]["properties"][field]["enum"]
        Path(args[args.index("-o") + 1]).write_text('{"ok": true}')

    monkeypatch.setattr(ai_tasks.media_jobs, "process", process)
    await ai_tasks.complete({"provider": "codex"}, "Test", schema=schema, binary="codex")
    assert schema == original
    assert original["$defs"]["StoryScene" if contract is Storyboard else "SRTScene"]["properties"]["speaker"]["default"] == "A"


def test_codex_schema_preserves_property_names_refs_and_nullable_fields():
    schema = {"type": "object", "properties": {
        "default": {"type": "string", "default": "keep this field"},
        "optional": {"anyOf": [{"$ref": "#/$defs/Item"}, {"type": "null"}], "default": None},
    }, "$defs": {"Item": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"], "additionalProperties": False}}}
    result = ai_tasks._codex_schema(schema)
    assert result["required"] == ["default", "optional"]
    assert result["properties"]["default"] == {"type": "string"}
    assert result["properties"]["optional"]["anyOf"] == schema["properties"]["optional"]["anyOf"]
    assert result["$defs"] == schema["$defs"]
    assert schema["properties"]["optional"]["default"] is None


async def test_provider_test_uses_draft_and_does_not_save(monkeypatch):
    role = {"provider": "codex", "model": "chosen-model", "effort": "low"}
    monkeypatch.setattr(providers, "validate_role_entry", AsyncMock(return_value=role))
    caller = AsyncMock(return_value='{"ok":true}')
    monkeypatch.setattr(ai_tasks, "complete", caller)
    monkeypatch.setattr(providers, "_write", lambda _: pytest.fail("Test must not save settings"))
    result = await providers.test_provider(providers.ProviderTest(role="prompt_suggestion", assignment=role))
    assert result["ok"] is True
    assert caller.call_args.args[0] == role


async def test_save_new_role_keeps_existing_review_and_returns_default_roles(monkeypatch, tmp_path):
    path = tmp_path / "providers.json"
    old = {"provider": "claude", "model": "sonnet", "effort": "high"}
    path.write_text(json.dumps({"active": "claude", "roles": {"video_review": old}}))
    monkeypatch.setattr(providers, "_PROVIDERS_FILE", path)
    monkeypatch.setattr(config, "CLI_PROVIDERS", {})
    monkeypatch.setattr(cli_providers, "executable", lambda _: "/test/codex")
    new = {"provider": "codex", "model": "chosen-model", "effort": "low"}
    result = await providers.patch_providers({"roles": {"prompt_suggestion": new}})
    assert result["roles"]["subtitle_translation"]["provider"] == "codex"
    assert result["roles"]["video_review"] == old
    assert json.loads(path.read_text())["roles"]["prompt_suggestion"] == new
