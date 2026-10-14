# Sharing FrameWeaver

This guide covers a source snapshot for GitHub. Each user installs the software
and connects their own local browser; the package does not contain a signed-in
session, provider credentials, generated projects, or a hosted backend.

## Install a source package

Use Python 3.12, Node.js 24, Chrome, and `ffmpeg`/`ffprobe` for the checked setup.
Create a backend environment from the unpacked repository root:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env
```

Edit `.env` and set `FLOW_PROJECT_ID` to the UUID from your own Google Flow project.
Load the variables into the current shell, then start the backend:

```sh
set -a
source .env
set +a
python -m agent.main
```

The backend does not load `.env` automatically. Keep the API and extension
WebSocket bound to localhost. Load `extension/` unpacked in Chrome, open
`https://flow.google.com/`, sign in, and leave the tab open.

In another terminal:

```sh
cd dashboard
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Before a generation workflow, check:

```sh
curl -s http://127.0.0.1:8100/health
curl -s http://127.0.0.1:8100/api/flow/status
```

The health response must report `extension_connected: true`. Optional TTS engines
and music services have additional installation and credential requirements; see
the main README and the engine-specific documents. They are not preinstalled by
the source package.

## Build a clean archive

```sh
python tools/package_source.py --output releases/frameweaver-source.zip
```

The archive contains an explicit set of source directories, documentation,
bundled preview assets, tests, example configuration, and license notices.
`SOURCE_MANIFEST.json` records each included file's size and SHA-256 hash.

It excludes environments, `node_modules`, builds, `.env` files other than examples,
provider settings, private signing keys, database files, generated output,
session logs, personal Firebase deployment configuration, internal review notes,
and macOS metadata. The saved VieNeu runtime requirements remain included because
they are referenced by the optional installation notes. Symlinks are excluded.
Detected common private-key or credential patterns cause packaging to fail.

Inspect the archive and its notices before publishing. Pattern matching is a
limited check, not a complete security or licensing audit. Unused legacy web-client key material has been removed from the extension source.
Use your own environment variables for optional provider credentials; do not put
private service keys into browser bundles.

## GitHub identity and project origin

You can publish using a personal GitHub account. GitHub authentication and a
commit author's email are separate; choose your GitHub-provided `noreply` email
for commits if you prefer to keep your personal email out of public history.

The repository for this distribution is `phucnguyentvp1/frameweaver`. Original
project details and the preserved MIT copyright notice are recorded in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). This repository starts from a
source snapshot and does not reproduce the upstream commit history. Source ZIPs
do not include `.git`; clone from GitHub for the history of this distribution.

Before a public launch, add a short description of your own improvements, a
demo you made yourself, and clear feature limitations. Existing screenshots and
live-verification claims in the README come from the source snapshot; the
packaging review does not re-verify those claims.

The current documentation lists video upscale, Veo reference-to-video, and Veo
start/end chaining as unported on the migrated transport. State those limitations
in a release description. Local tests and a dashboard build do not prove that
live generation works with the current Google Flow service.

## Known filesystem limitation from the packaging review

Testing with temporary data on the external macOS volume exposed failures from
AppleDouble metadata files (`._*.json`, `._*.mp4`) being treated as real library
files. A rapid audio replacement also failed a modification-time-based cache
test on that volume. The packager filters these files from the ZIP, but that
does not fix runtime discovery or caching on such filesystems.

For a macOS trial, keep the working data and test temporary directories on a
native APFS volume. Allow at least the application's 1 GB upload reserve plus
space for the input video and processing intermediates. Native filesystem use
is a workaround, not verification of external-volume compatibility.
