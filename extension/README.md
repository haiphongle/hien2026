# FrameWeaver Chrome extension

Display name: **FrameWeaver**. This source preview's manifest is **0.3.5**.
The macOS **desktop beta 2** includes **0.3.6** with connection diagnostics;
use its [matching extension ZIP](https://github.com/phucnguyentvp1/frameweaver/releases/download/v1.3.1-beta.2/FrameWeaver-1.3.1-beta.2-extension.zip)
or the app's extension installation menu when running that desktop version.

## Check the desktop connection

In desktop beta 2, open **Settings → Google Flow connection → Check connection**
(`Cài đặt → Kết nối Google Flow → Kiểm tra kết nối`). The check asks the extension
for a fresh reply and distinguishes backend access, extension version/reachability,
and the Flow tab's session state. It does not generate media or consume credits.
If it reports an old extension, load the beta 2 folder, then reload the Flow tab.
A connected bridge alone does not confirm a usable Flow session.

Muse requests use a separate gateway; see the [Muse guide](../docs/MUSE.md).
New project creation in this desktop beta still needs the Flow connection.

## Install or update

1. Extract the standalone extension ZIP, or use `extension/` in the full source
   package. Select the directory containing `manifest.json`.
2. Open Chrome's extension manager, enable Developer mode, and choose **Load
   unpacked**. For an existing unpacked installation pointing to this same
   directory, use **Reload**.
3. Open Google Flow and connect to the local FrameWeaver backend.

An extension loaded from a different copy of the project continues to use that
copy's files. Reloading it will not switch its source directory. Keep only one
Flow/FrameWeaver bridge enabled for the same local backend.

The popup, side panel, toolbar title, and manifest use the new display name.
The full source package includes the backend changes for downloaded filenames;
updating only the extension does not update the backend.

Original copyright and third-party notices accompany the distribution in
`LICENSE` and `THIRD_PARTY_NOTICES.md`.
