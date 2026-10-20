# Third-party notices

FrameWeaver is developed in <https://github.com/phucnguyentvp1/frameweaver>.
Parts of the scene-generation backend, SDK, Chrome bridge and workflow recipes
are adapted from Flow Kit and its Google Flow Agent base:

- <https://github.com/crisng95/flowkit>
- <https://github.com/tuannguyenhoangit-droid/google-flow-agent>
- Original MIT copyright: 2026 tuannguyenhoangit-droid. The full notice remains
  in `LICENSE`.

The historical showcase in the inherited README comes from that source project;
it is not a claim that those examples were produced by the FrameWeaver maintainer.

Optional integrations such as Muse gateway, VieNeu-TTS, Kokoro, Qwen3-TTS,
Whisper and ProPainter retain their own code/model licenses. Installing or using
them does not change their licenses. Their setup documentation and source links
are in `docs/`; this product rename does not alter those third-party notices.

## macOS core bundle

The macOS bundle additionally redistributes Electron (MIT and Chromium notices
in `Contents/Resources`), CPython from python-build-standalone (Python and
component licenses within `runtime/python`), and the Python packages listed in
`runtime/BUILD-MANIFEST.json` (individual licenses in their `.dist-info` folders).

`runtime/media/bin/ffmpeg` and `ffprobe` are built from FFmpeg 8.1 with x264,
under the GPL. Their license files are in `runtime/media`. Exact source archives,
checksums and the build recipe accompany each DMG as `*-media-sources.zip`.
The pkgconf source in that archive is an ISC-licensed build tool, not a runtime
dependency. See `desktop/README.md` and `desktop/media-build.py` in the source
repository for upstream locations and pinned revisions.

## Windows core bundle

The Windows x64 bundle redistributes the same Electron/CPython components and
Python package licenses under `resources/runtime`. FFmpeg 8.1 and x264 are built
from the same pinned source revisions using MSYS2 UCRT64, with static libraries.
Their GPL notices, source archives and Windows build recipe accompany the
installer as `*-Windows-x64-media-sources.zip`. MSYS2/compiler tools and NSIS are
build dependencies; they are not installed as a development environment for users.
