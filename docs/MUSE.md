# Muse AI · desktop beta 4

[Tiếng Việt](MUSE.vi.md) · [macOS downloads](https://github.com/phucnguyentvp1/frameweaver/releases/tag/v1.3.1-beta.4)

Both **Apple Silicon and Intel** beta 4 apps include Muse integration for script
planning, reference/scene images, and video from a starting image. This guide
describes the **packaged desktop apps**, not the repository's older source preview.

Muse connects through the optional community [muse2api gateway](https://github.com/crisng95/muse2api).
The gateway installs through Settings; Chrome and your Muse account are required separately.
Signing in at muse.ai alone does not connect FrameWeaver. A website URL is not
a gateway URL.

## 1. Install and start in Settings

1. Install Chrome and move FrameWeaver into `/Applications`.
2. Open **Settings → Muse → Cài và khởi động Muse**. The first installation needs
   internet access and at least 512 MB free. The app uses its bundled Python to
   install the gateway in an isolated environment, then starts a private Chrome
   profile and saves the local key. No Node.js, Terminal or admin key entry is needed.
3. Keep **Tự khởi động gateway đã cài** enabled to start it on later app launches
   while Muse is enabled. Existing users opt in; simply opening the app never
   downloads the gateway. Installing it through this button enables autostart.
4. Connect your Muse session below. **Gateway Muse đã chạy** confirms the local
   service only; it does not validate an account or media entitlement.

Settings shows installation/startup progress, cancel, **Khởi động Muse**,
**Khởi động lại**, **Dừng gateway**, and **Xem nhật ký**. Exiting FrameWeaver stops
only the gateway/browser opened by that app session. An existing external gateway
is reused and never stopped by this session. A failed browser startup is cleaned
up so it cannot leave the private browser's port occupied.

**Mở trang quản lý gateway** is an optional advanced link, available once the
service runs. URL/key settings are under **Nâng cao** for independently managed
gateways. Keep local gateways bound to localhost; remote gateways require HTTPS.
If startup fails, inspect the log and retry. The first Intel-under-Rosetta Chrome
launch in our test exceeded upstream's browser timeout; retry succeeded. This has
not been tested on a physical Intel Mac.

The source revision is pinned to
[`efc06dd9741efe3b56a4bb02bb4761c0003eae30`](https://github.com/crisng95/muse2api/tree/efc06dd9741efe3b56a4bb02bb4761c0003eae30),
and its archive SHA256 is verified. Download source, state path and log path are
shown in Settings. The gateway, environment and private profile are stored in:

```text
~/Library/Application Support/FrameWeaver/.flowkit/muse-gateway/
```

The gateway is an optional download, not bundled in the DMG. Its private browser
does not read cookies from your normal Chrome profile. The older command-line
launcher remains available for advanced self-managed use; do not start it beside
an app-managed gateway on the same ports.

## 2. Connect your account in FrameWeaver

1. After installation, Muse is enabled and its local key is already saved.
   The default service is **`http://127.0.0.1:18610`**. For another gateway,
   configure its URL/key under **Nâng cao**.
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
The app-managed gateway sets admin and API keys to the same private value; another
gateway may use separate keys. FrameWeaver uses its API key to connect. If your
independently managed gateway has a different admin key, provision its account
through its own administration tools instead of FrameWeaver's convenience form.

## 3. Choose Muse for the production stages

In **Studio → Video người que**, choose the providers for content, images, and
video individually. You can use Muse for planning and Google Flow for media, or
Muse for all three supported stages. Keep the gateway running while using Muse.

| Stage | Muse integration in beta 4 |
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
editing, export, and fresh gateway installation/lifecycle on both bundled
Python runtimes (empty accounts, no paid generation). It does **not** certify live Muse quality, speed, quota, or
successful gateway installation on every Mac. Intel desktop checks used Rosetta;
testing on a physical Intel Mac is pending. The gateway is a separate community
project, not an official Muse SDK. Use it according to the provider's terms.

Application data is under `~/Library/Application Support/FrameWeaver`. The installer
contains no maintainer account, cookies, API keys, or personal database. Keep your
`.flowkit` directory and generated media private when sharing files.
