from pathlib import Path

import pytest
from fastapi import HTTPException

from agent.api import videos as video_api
from agent.db import crud, schema
from agent.services import video_assembly


@pytest.mark.asyncio
async def test_scene_order_is_saved_and_marks_cut_stale(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "sequence.db")
    await schema.init_db()
    try:
        project = await crud.create_project(
            name="Sequence project", id="11111111-1111-1111-1111-111111111111",
            material="realistic",
        )
        video = await crud.create_video(project_id=project["id"], title="Four scenes")
        scenes = [
            await crud.create_scene(video["id"], index, f"Scene {index + 1}")
            for index in range(4)
        ]

        await video_api.set_scene_order(video["id"], video_api.SceneOrderRequest(
            scene_ids=[scene["id"] for scene in reversed(scenes)],
        ))

        ordered = await crud.list_scenes(video["id"])
        updated_video = await crud.get_video(video["id"])
        assert [scene["id"] for scene in ordered] == [scene["id"] for scene in reversed(scenes)]
        assert [scene["display_order"] for scene in ordered] == [0, 1, 2, 3]
        assert updated_video["status"] == "PROCESSING"

        with pytest.raises(HTTPException) as error:
            await video_api.set_scene_order(video["id"], video_api.SceneOrderRequest(
                scene_ids=[scenes[0]["id"]] * len(scenes),
            ))
        assert error.value.status_code == 400
    finally:
        await schema.close_db()


@pytest.mark.asyncio
async def test_assembly_uses_saved_scene_order(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "DB_PATH", tmp_path / "assemble.db")
    monkeypatch.setattr(video_assembly, "OUTPUT_DIR", tmp_path / "output")
    await schema.init_db()
    try:
        project = await crud.create_project(
            name="Assembly project", id="22222222-2222-2222-2222-222222222222",
            material="realistic",
        )
        video = await crud.create_video(
            project_id=project["id"], title="Ordered cut", orientation="VERTICAL",
        )
        await crud.update_video(video["id"], thumbnail_url="/api/projects/custom-thumbnail.jpg")
        scenes = [
            await crud.create_scene(video["id"], index, f"Scene {index + 1}")
            for index in range(3)
        ]
        scene_order = [scenes[2], scenes[0], scenes[1]]
        await crud.reorder_scenes(video["id"], [scene["id"] for scene in scene_order])
        for index, scene in enumerate(scenes):
            await crud.update_scene(
                scene["id"], vertical_video_status="COMPLETED",
                vertical_video_url=f"https://clips.example/clip-{index}.mp4",
            )

        async def fake_download(url: str, target: Path) -> None:
            target.write_text(url.rsplit("/", 1)[-1])

        async def fake_merge(paths: list[str], output: str) -> bool:
            Path(output).write_text("\n".join(Path(path).read_text() for path in paths))
            return True

        monkeypatch.setattr(video_assembly, "_download_clip", fake_download)
        monkeypatch.setattr(video_assembly, "merge_videos", fake_merge)

        result = await video_assembly.assemble_video(video["id"], "VERTICAL")

        assert result["ok"] is True
        assert result["scene_count"] == 3
        assert video_assembly.assembled_path(video["id"]).read_text() == "clip-2.mp4\nclip-0.mp4\nclip-1.mp4"
        updated = await crud.get_video(video["id"])
        assert updated["status"] == "COMPLETED"
        assert updated["thumbnail_url"] == "/api/projects/custom-thumbnail.jpg"
        assert updated["vertical_url"] == result["url"]
        assert "?v=" in result["url"]
    finally:
        await schema.close_db()
