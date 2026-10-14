"""Quick creation should queue a durable image → Omni video pipeline."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from pathlib import Path

import pytest

from agent.api import videos as video_api
from agent.db import crud, schema
from agent.worker import processor
from agent.services import video_assembly


@pytest.mark.asyncio
async def test_quick_create_queues_styled_scenes_and_omni_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "quick-create.db")
    monkeypatch.setattr(video_api, "get_flow_client", lambda: SimpleNamespace(connected=True))
    await schema.init_db()
    try:
        project = await crud.create_project(
            name="Test project", id="11111111-1111-1111-1111-111111111111",
            material="realistic",
        )
        result = await video_api.quick_create(video_api.QuickCreateRequest(
            project_id=project["id"], idea="A cat makes coffee", orientation="HORIZONTAL",
            material="anime", scene_count=2,
        ))

        video = await crud.get_video(result["video_id"])
        scenes = await crud.list_scenes(video["id"])
        jobs = await crud.list_requests(video_id=video["id"])
        assert video["status"] == "PROCESSING"
        assert video["orientation"] == "HORIZONTAL"
        assert len(scenes) == 2
        assert len(jobs) == 4
        assert {job["type"] for job in jobs} == {"GENERATE_IMAGE", "GENERATE_VIDEO_OMNI"}
        assert all(job["orientation"] == "HORIZONTAL" for job in jobs)
        assert all(scene["image_prompt"].startswith("Anime style") for scene in scenes)
        assert all(scene["prompt"].startswith(("An opening", "A closing")) for scene in scenes)
        assert all("0-3s:" in scene["video_prompt"] and "6-8s:" in scene["video_prompt"] for scene in scenes)

        omni_job = next(job for job in jobs if job["type"] == "GENERATE_VIDEO_OMNI")
        mock_ops = SimpleNamespace(generate_scene_video=AsyncMock(return_value={"data": {"operations": []}}))
        monkeypatch.setattr("agent.sdk.services.operations.get_operations", lambda: mock_ops)
        await processor._dispatch(omni_job, "HORIZONTAL")
        mock_ops.generate_scene_video.assert_awaited_once()
        assert mock_ops.generate_scene_video.await_args.kwargs["omni"] is True

        async def fake_download(_url, target):
            target.write_bytes(b"clip")

        async def fake_merge(_paths, output):
            Path(output).write_bytes(b"joined")
            return True

        monkeypatch.setattr(video_assembly, "OUTPUT_DIR", tmp_path)
        monkeypatch.setattr(video_assembly, "_download_clip", fake_download)
        monkeypatch.setattr(video_assembly, "merge_videos", fake_merge)
        for scene in scenes:
            await crud.update_scene(
                scene["id"], horizontal_video_status="COMPLETED",
                horizontal_video_url=f"https://example.test/{scene['id']}.mp4",
            )
        await video_assembly.maybe_finish_quick_video(video["id"], "HORIZONTAL")
        completed = await crud.get_video(video["id"])
        assert completed["status"] == "COMPLETED"
        assert completed["horizontal_url"].startswith(f"/api/videos/{video['id']}/file?v=")
        assert video_assembly.assembled_path(video["id"]).read_bytes() == b"joined"
    finally:
        await schema.close_db()


@pytest.mark.asyncio
async def test_storyboard_uses_each_scenes_character_refs_and_dialogue(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "storyboard.db")
    monkeypatch.setattr(video_api, "get_flow_client", lambda: SimpleNamespace(connected=True))
    await schema.init_db()
    try:
        project = await crud.create_project(
            name="Story", id="11111111-1111-1111-1111-111111111111", material="realistic",
        )
        a = await crud.create_character(name="A", media_id="22222222-2222-2222-2222-222222222222",
                                        voice_description="Warm low voice")
        b = await crud.create_character(name="B", media_id="33333333-3333-3333-3333-333333333333")
        await crud.link_character_to_project(project["id"], a["id"])
        await crud.link_character_to_project(project["id"], b["id"])
        result = await video_api.quick_create(video_api.QuickCreateRequest(
            project_id=project["id"], idea="A and B meet", scenes=[
                video_api.StoryboardScene(action="A walks through a cafe", character_ids=[a["id"]],
                                          speaker_id=a["id"], dialogue="Xin chào B"),
                video_api.StoryboardScene(action="A and B sit at the table", character_ids=[a["id"], b["id"]],
                    image_prompt="A and B sit at the table, overhead framing.",
                    video_prompt="0-3s: Sit. 3-6s: Exchange glances. 6-8s: Hold the shot. No speech."),
            ],
        ))
        scenes = await crud.list_scenes(result["video_id"])
        assert len(scenes) == 2
        assert scenes[0]["character_names"] == '["A"]'
        assert scenes[1]["character_names"] == '["A", "B"]'
        assert 'A says "Xin chào B"' in scenes[0]["video_prompt"]
        assert "Audio: keep character dialogue" in scenes[0]["video_prompt"]
        assert "A and B sit at the table" in scenes[1]["image_prompt"]
        assert "overhead framing" in scenes[1]["image_prompt"]
        assert scenes[1]["video_prompt"] == "0-3s: Sit. 3-6s: Exchange glances. 6-8s: Hold the shot. No speech."
    finally:
        await schema.close_db()
