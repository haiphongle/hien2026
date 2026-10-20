"""Create a source-only archive without depending on a Git checkout.

Run from any directory: python tools/package_source.py --output releases/source.zip
This never calls Flow, uploads files, or modifies local credentials/media.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import zipfile

ROOT_FILES = {
    ".gitignore", ".gitattributes", ".env.example", "README.md", "LICENSE",
    "AGENTS.md", "CLAUDE.md", "ARCHITECTURE.md", "CONTRIBUTING.md",
    "THIRD_PARTY_NOTICES.md", "BRANDING.md", "README.vi.md", "SECURITY.md",
    "CHANGELOG.md", "pytest.ini", "setup.py", "setup.sh",
}
SOURCE_DIRS = {".github", "agent", "dashboard", "docs", "extension", "scripts", "skills", "tests", "tools"}
EXCLUDED_DIRS = {
    ".git", ".flowkit", ".omc", ".claude", ".codex", ".gemini", ".firebase",
    "node_modules", "venv", ".venv", ".venvs", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "dist-ssr", "output", "releases",
    "_metadata", "__MACOSX", ".idea", ".vscode", "htmlcov",
}
EXCLUDED_FILES = (
    "._*", ".DS_Store", "*.pem", "*.key", "*.p12", "*.pfx", "*.crx",
    "*.db", "*.db-*", "*.sqlite*", "*.ses", "*.har", "*.log", "*.pyc",
    "*.pyo", "*.tsbuildinfo", "*credentials*.json", "*secret*.json",
    "token*.json", "cookies*", ".coverage", "auth.json", ".firebaserc",
)
SECRET_PATTERNS = {
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "GitHub token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})"),
    "provider key": re.compile(rb"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{30,}"),
    "Google OAuth token": re.compile(rb"ya29\.[A-Za-z0-9_-]{30,}"),
    "AWS access key": re.compile(rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}
MAX_FILE_SIZE = 20 * 1024 * 1024


def excluded(relative: Path) -> bool:
    if relative.as_posix() == "docs/images/flowkit_banner.svg":
        return True  # Superseded by the FrameWeaver banner; keep the original only locally.
    if any(part in EXCLUDED_DIRS or part.startswith("._") for part in relative.parts):
        return True
    if relative.name.startswith(".env") and relative.name != ".env.example":
        return True
    if any(fnmatch.fnmatch(relative.name.lower(), pattern.lower()) for pattern in EXCLUDED_FILES):
        return True
    if len(relative.parts) > 2 and relative.parts[:2] == ("docs", "reviews"):
        return relative.as_posix() != "docs/reviews/vieneu-runtime-2026-09-28.txt"
    return False


def source_files(root: Path):
    for base, dirs, files in os.walk(root, followlinks=False):
        base = Path(base)
        dirs[:] = sorted(d for d in dirs if not (base / d).is_symlink()
                         and not excluded((base / d).relative_to(root))
                         and (base != root or d in SOURCE_DIRS))
        for name in sorted(files):
            path = base / name
            relative = path.relative_to(root)
            if base == root and name not in ROOT_FILES and not fnmatch.fnmatch(name, "requirements*.txt"):
                continue
            if not path.is_symlink() and not excluded(relative):
                yield path, relative.as_posix()


def package(root: Path, output: Path) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root) and output.relative_to(root).parts[0] in SOURCE_DIRS:
        raise ValueError("Archive output must be outside the included source directories")
    contents = {}
    for path, name in source_files(root):
        if path.resolve() == output:
            raise ValueError("Archive output must be outside the included source directories")
        if path.stat().st_size > MAX_FILE_SIZE:
            raise ValueError(f"Review oversized source file before publishing: {name}")
        raw = path.read_bytes()
        for kind, pattern in SECRET_PATTERNS.items():
            if pattern.search(raw):
                raise ValueError(f"Possible {kind} in {name}; remove it before packaging")
        contents[name] = raw
    if "LICENSE" not in contents or "README.md" not in contents:
        raise ValueError("Source root must contain LICENSE and README.md")
    manifest = {
        "format": 1,
        "files": [{"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                  for name, raw in sorted(contents.items())],
    }
    contents["SOURCE_MANIFEST.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidentally replacing an earlier release.
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(contents.items()):
            info = zipfile.ZipInfo(f"frameweaver-source/{name}", date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            mode = 0o755 if name.endswith(".sh") else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, raw)
    return {"archive": str(output), "source_files": len(manifest["files"]),
            "bytes": output.stat().st_size, "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = package(Path(__file__).resolve().parents[1], args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Packaging stopped: {error}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
