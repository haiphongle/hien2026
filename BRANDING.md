# FrameWeaver

- Product name: **FrameWeaver**
- Repository: <https://github.com/phucnguyentvp1/frameweaver>
- Tagline: Weave scenes, voices, and sound.

The dashboard, Chrome extension, setup messages, documentation and suggested
download names use FrameWeaver. New default image, video, subtitle and prompt
downloads use the `frameweaver-` prefix. Existing media paths and user titles
remain valid; renaming the product does not rewrite saved projects or files.

## Compatibility with existing installations

The following identifiers keep their original spelling so existing installations
can continue using their saved settings, models, browser drafts and media:

- The private `.flowkit/` directory, `flow_agent.db`, and `.fk-setup.json`.
- `FLOWKIT_*` environment variables, `/fk-*` commands and skill filenames.
- Browser storage keys, UUID namespaces, extension bridge identifiers,
  `flowkitPolling`, and the local authentication header.
- Muse accounts labelled `FlowKit local Muse` are reused when reconnecting;
  new accounts use `FrameWeaver local Muse`.

These identifiers are compatibility details, not product names. Keep them stable
unless a separate data migration is implemented and verified. The current
checkout directory may keep its existing name, including `flowkit-main`.

The public model catalog defaults to
`https://raw.githubusercontent.com/phucnguyentvp1/frameweaver/main/agent/models.json`.
Set `VITE_MODEL_CATALOG_URL` before building the dashboard to use another catalog.

The Chrome extension is FrameWeaver 0.3.5. Reload the installed unpacked extension
from `chrome://extensions` to refresh its name and version.

Original source attribution and license notices remain in `LICENSE` and
`THIRD_PARTY_NOTICES.md`.
