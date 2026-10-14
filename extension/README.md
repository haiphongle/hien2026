# FrameWeaver Chrome extension

Display name: **FrameWeaver**. Version: **0.3.5**.

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
