# Muse AI · desktop beta 3

[Tiếng Việt](MUSE.vi.md) · [macOS downloads](https://github.com/phucnguyentvp1/frameweaver/releases/tag/v1.3.1-beta.3)

Both **Apple Silicon and Intel** beta 3 apps include Muse integration for script
planning, reference/scene images, and video from a starting image. This guide
describes the **packaged desktop apps**, not the repository's older source preview.

Muse connects through the optional community [muse2api gateway](https://github.com/crisng95/muse2api).
The gateway, Chrome, and your Muse account are installed/configured separately.
Signing in at muse.ai alone does not connect FrameWeaver. A website URL is not
a gateway URL.

## 1. Start the local gateway

Install Chrome and move FrameWeaver into `/Applications`. The app includes an
optional launcher that installs a pinned gateway in its own Python environment.
In Terminal, run:

```sh
env -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 \
  FRAMEWEAVER_DATA_DIR="$HOME/Library/Application Support/FrameWeaver" \
  "/Applications/FrameWeaver.app/Contents/Resources/runtime/python/bin/python3" \
  "/Applications/FrameWeaver.app/Contents/Resources/runtime/app/scripts/muse_gateway.py" \
  --install --background
```

This command applies to **both Mac editions** and requires an internet connection
on first installation. The gateway revision is pinned to
[`efc06dd9741efe3b56a4bb02bb4761c0003eae30`](https://github.com/crisng95/muse2api/tree/efc06dd9741efe3b56a4bb02bb4761c0003eae30).
After rebooting, repeat the command **without `--install`**. It does not start
automatically at login. Do not start a second gateway if port 18610 is occupied.
If Chrome is not found, append `--chrome` followed by its executable path.

The launcher uses a separate browser profile and automatically saves the local
gateway key in FrameWeaver's private settings. It does not read your Chrome cookies.
State, the virtual environment, and `gateway.log` live under:

```text
~/Library/Application Support/FrameWeaver/.flowkit/muse-gateway/
```

An independently managed gateway can also be used; follow its own setup guide
and enter its URL and API key in FrameWeaver. Keep a local gateway bound to
localhost; a remote gateway requires HTTPS.

## 2. Connect your account in FrameWeaver

1. Open **Settings → Muse** (`Cài đặt → Muse`). Enable the connection. For the
   launcher above, keep the URL **`http://127.0.0.1:18610`**. Save the settings;
   its key is already stored. For another gateway, enter that gateway's API key.
2. Open **Kết nối tài khoản Muse trên máy**. In Chrome signed in at muse.ai,
   open **DevTools → Application → Cookies → https://muse.ai**. Copy only the
   values for `hatch_sess`, `hatch_gw`, `hatch_vml`, and `hatch_native_auth_device`
   into the matching password fields in FrameWeaver.
3. Confirm local session storage, then click **Lưu phiên và kiểm tra**. This
   convenience form supports only a local gateway on port 18610. It retains the
   session for later use; re-enter it if Muse expires the session.
4. Click **Lưu và kiểm tra kết nối**. A ready connection requires the gateway's
   **browser** driver and an available account. The **mock** driver is rejected.

Do not use Google/Gmail cookies or your Gmail password. Never put cookie values
or gateway keys in an issue, screenshot, chat, or repository. The gateway dashboard
at `http://127.0.0.1:18610/dashboard` uses its **admin key**, not a Muse password.
The supplied launcher sets admin and API keys to the same private value; another
gateway may use separate keys. FrameWeaver uses its API key to connect. If your
independently managed gateway has a different admin key, provision its account
through its own administration tools instead of FrameWeaver's convenience form.

## 3. Choose Muse for the production stages

In **Studio → Video người que**, choose the providers for content, images, and
video individually. You can use Muse for planning and Google Flow for media, or
Muse for all three supported stages. Keep the gateway running while using Muse.

| Stage | Muse integration in beta 3 |
| --- | --- |
| Script | Plan scenes from SRT or content; review the result before production |
| Images | Character/reference and scene images; up to four image references per request |
| Video | Animate a starting image; the integrated request uses an eight-second clip |
| Narration | Uses the existing TTS providers; not generated through Muse |
| End-frame video / upscale | Use the corresponding supported provider workflow; not supported by this Muse integration |

Typical flow: **SRT/content → script review → references → scene images → image
review → motion → timeline editing → export**. Image-effects mode renders motion
locally with FFmpeg; selecting Muse for images does not require creating AI clips.
If you choose AI video, provider usage applies.

Google Flow requests still require the FrameWeaver Chrome extension and a signed-in
Flow tab. Muse requests use the separate gateway. **New project creation in this
beta still requires the Flow connection**, even when later stages use Muse.

Default names `muse-chat`, `muse-image`, and `muse-video` are gateway model identifiers.
Other catalog names may be aliases. They do not prove which underlying branded
model ran. FrameWeaver cannot confirm your Muse token balance, image/video quota,
cost, or entitlement; check these with the provider.

## Troubleshooting and release scope

- **Gateway not ready / 503:** check that port 18610 is served by your gateway,
  the driver is `browser`, and an account is available. Check `gateway.log`.
- **401/403:** check the gateway key and Muse session; signing into the website
  does not automatically refresh the gateway's stored session.
- **Failed/timeout:** completed results are retained. Resume missing work instead
  of submitting the whole production again. A failed upstream generation still
  needs inspection in Muse/the gateway.
- **Cancel:** stops FrameWeaver's wait/queue. The gateway has no cancellation API
  for already-submitted media; that work can continue and consume provider quota.

The desktop verification report covers local startup, connection diagnostics,
editing, and export. It does **not** certify live Muse quality, speed, quota, or
successful gateway installation on every Mac. Intel desktop checks used Rosetta;
testing on a physical Intel Mac is pending. The gateway is a separate community
project, not an official Muse SDK. Use it according to the provider's terms.

Application data is under `~/Library/Application Support/FrameWeaver`. The installer
contains no maintainer account, cookies, API keys, or personal database. Keep your
`.flowkit` directory and generated media private when sharing files.
