# ChatGPT connection · macOS desktop beta 3

[Tiếng Việt](CHATGPT-DESKTOP.vi.md) · [Download](releases/v1.3.1-beta.3-macos.md)

For both Apple Silicon and Intel desktop apps, macOS 13+. This guide applies to
the packaged beta 3 apps, not the repository's older source preview.

## Connect

1. Open **Settings → ChatGPT**. If the app reports the component is missing,
   click **Cài thành phần ChatGPT** (Install ChatGPT component).
2. Wait for download, checksum verification, extraction and version check. Progress,
   cancel/retry, the exact download URL and destination are shown. No Node.js,
   Terminal command or administrator installation is required.
3. Choose **Đăng nhập bằng ChatGPT**. Complete OpenAI's login in your browser;
   use the displayed login link if the browser does not open automatically.
4. Return to FrameWeaver and refresh the account check. Choose Codex for the
   relevant AI task. Login alone does not overwrite existing provider/task settings.
5. Optionally click **Kiểm tra AI**. This sends a small text request through your
   account; quota/plan restrictions still apply. It does not test image understanding.

This connects through the official Codex CLI, not by reading a ChatGPT web tab's
cookies. A browser login by itself is not enough. Account availability and usage
limits are controlled by OpenAI. Existing CLI or ChatGPT-app executables take
priority and are not replaced. The login uses Codex's existing account store.

## Download and storage

The optional component is pinned to **Codex CLI 0.163.0**, downloaded from
[OpenAI's official release](https://github.com/openai/codex/releases/tag/rust-v0.163.0).
The architecture-specific archive's expected size and SHA256 are verified before
an executable is published. A failed/cancelled download does not install a partial file.

Managed macOS executable:

```text
~/Library/Application Support/FrameWeaver/.flowkit/tools/codex/0.163.0/<platform>/codex
```

`<platform>` is `aarch64-apple-darwin` on Apple Silicon or `x86_64-apple-darwin`
on Intel. The downloaded archive is approximately 100–109 MB; allow about
700 MB free during installation. The component is downloaded only after clicking
install, not bundled inside the DMG. No maintainer account or API key is included.

## Refresh and troubleshoot

- **Reload interface** in the top bar reloads the current page, similar to F5,
  without restarting the backend. Existing editor save/leave warnings still apply.
- The sidebar's extension status shares current data with Settings and the guide.
  **Check connection** updates it immediately; background checks track reconnects.
- A green extension transport indicator does not prove the Flow tab/account/model
  is ready. Read the detailed diagnostic; no image/video generation is submitted.
- For download errors, check the displayed source URL, network, disk space and
  destination permissions, then retry. No global npm installation is required.
- If login succeeds but Test AI fails, check your plan/usage and selected account.
  Do not paste credentials, login URLs, tokens or cookies into issues.

The installer and core routes were tested on both packaged runtimes. Intel tests
used Rosetta on Apple Silicon; physical Intel and a second clean Mac remain pending.
