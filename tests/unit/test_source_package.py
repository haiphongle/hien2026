"""Release boundaries: never ship local credentials, data, or symlink targets."""
import hashlib
import importlib.util
import json
from pathlib import Path
import zipfile

import pytest

spec = importlib.util.spec_from_file_location(
    "source_packager", Path(__file__).resolve().parents[2] / "tools/package_source.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "LICENSE").write_text("Preserved original license")
    (root / "README.md").write_text("Source package")
    return root


def put(root, name, data="local private data"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data)
    return path


def test_archive_excludes_credentials_media_caches_and_symlink_targets(source, tmp_path):
    included = put(source, "agent/main.py", "print('app')")
    put(source, "dashboard/.env.example", "VITE_API_BASE_URL=")
    for name in (
        "extension.pem", "flow_agent.db", "output/video.mp4", ".flowkit/suno.json",
        "dashboard/.env.production", "dashboard/node_modules/dep/index.js",
        "agent/__pycache__/main.pyc", "agent/credentials.json", "agent/._main.py",
        "docs/reviews/private-session.md", "extension/_metadata/rules",
    ):
        put(source, name)
    outside = put(tmp_path, "outside.txt")
    (source / "agent/leaked.txt").symlink_to(outside)
    (source / "agent/linked-dir").symlink_to(tmp_path, target_is_directory=True)
    result = packager.package(source, tmp_path / "source.zip")
    with zipfile.ZipFile(result["archive"]) as archive:
        names = {name.removeprefix("frameweaver-source/") for name in archive.namelist()}
        assert names == {"LICENSE", "README.md", "agent/main.py", "dashboard/.env.example", "SOURCE_MANIFEST.json"}
        manifest = json.loads(archive.read("frameweaver-source/SOURCE_MANIFEST.json"))
        entry = next(row for row in manifest["files"] if row["path"] == "agent/main.py")
        assert entry["sha256"] == hashlib.sha256(included.read_bytes()).hexdigest()
        assert archive.getinfo("frameweaver-source/agent/main.py").external_attr >> 16 & 0o777 == 0o644


def test_secret_in_source_blocks_archive_without_echoing_its_value(source, tmp_path):
    token = "ghp_" + "a" * 36
    put(source, "agent/main.py", f"token = '{token}'")
    archive = tmp_path / "source.zip"
    with pytest.raises(ValueError, match="Possible GitHub token") as error:
        packager.package(source, archive)
    assert token not in str(error.value)
    assert not archive.exists()


def test_saved_runtime_requirements_are_kept_and_previous_release_is_not_overwritten(source, tmp_path):
    name = "docs/reviews/vieneu-runtime-2026-09-28.txt"
    put(source, name, "vieneu==3.8.3")
    archive = tmp_path / "source.zip"
    packager.package(source, archive)
    before = archive.read_bytes()
    with pytest.raises(FileExistsError):
        packager.package(source, archive)
    assert archive.read_bytes() == before
    with zipfile.ZipFile(archive) as output:
        assert f"frameweaver-source/{name}" in output.namelist()
