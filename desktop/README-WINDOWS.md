# FrameWeaver · Windows x64 preview

Windows 10/11 x64. The installer includes Electron, CPython 3.12, FFmpeg/ffprobe
with static x264, and bundled Vietnamese/CJK fonts. Large inference models and
provider accounts are separate. This first Windows package is **unsigned**;
no Authenticode publisher identity or SmartScreen reputation is claimed.

Install `FrameWeaver-…-Windows-x64-Setup.exe` for the current user, then open
FrameWeaver from Start/Desktop. No developer environment is required to open it.
App data stays in `%APPDATA%\FrameWeaver`; uninstall does not remove that data.
Close the older app before upgrading; source and desktop share ports 8100/9222.

For Google Flow, use Application → Install Google Flow extension, then Chrome →
chrome://extensions → Developer mode → Load unpacked. Keep a signed-in Flow tab
open and check the connection in Settings. Muse uses its own optional gateway:
Settings → Muse → Install/start, then connect your Muse session once. ChatGPT
component installation is also available in Settings. No account/credits included.

The Windows build uses the same production dashboard and application snapshot as
the Mac edition, with platform-specific process cancellation, Python executable,
and desktop shutdown handling. Model storage can be changed individually.
Relocating the output library through symlinks may require Developer Mode or
Windows symlink privileges; failed link creation leaves the source intact.
Whisper/TTS/ProPainter inference environments need separate installation and
hardware testing. Do not reuse macOS virtual environments or absolute paths.

## Build

Use a Windows x64 runner with Python 3.12, Node, NSIS packaging tools, and MSYS2
UCRT64 (`gcc`, `make`, `pkgconf`, `nasm`). All runtime archives and FFmpeg/x264
sources are checksum-pinned. The existing `requirements-macos.lock` is shared
for core desktop Python packages; native wheels resolve for the Windows target.

1. `python desktop/build-windows.py --prepare`
2. In UCRT64: `bash desktop/media-windows.sh`
3. Build the production dashboard, or supply the reviewed frozen dashboard via
   `python desktop/build-windows.py --web-dir desktop/web`.
4. Create the NSIS installer with pinned electron-builder and
   `desktop/electron-builder.windows.json`, using `--prepackaged desktop/build-windows/FrameWeaver`.
5. `python desktop/smoke-windows.py` verifies the installed app with empty test data,
   then `python desktop/build-windows.py --finalize` writes checksums and manifests.

Distribute corresponding FFmpeg/x264 sources, build instructions, notices,
manifest and verification report alongside the installer. Build/test execution
on a GitHub Windows runner is not a physical end-user PC or a GPU/model-quality
test. Read `VERIFICATION-WINDOWS.json` for the actual checked scope.
