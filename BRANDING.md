# FrameWeaver

**Name:** FrameWeaver  
**Repository slug:** `frameweaver`  
**GitHub repository:** `phucnguyentvp1/frameweaver`  
**Tagline:** Weave scenes, voices, and sound.

The name reflects assembling individual scenes, narration, audio, and editing
into a complete video. The banner is `docs/images/frameweaver_banner.svg`.

This naming change updates the product's display name, dashboard, extension,
documentation, repository links, and source archive. The underlying project's
original authorship is preserved in the accompanying license notices.

Original authorship is retained in `LICENSE` and `THIRD_PARTY_NOTICES.md`.
Historical screenshots remain examples from the inherited source snapshot.

Existing `.flowkit/` paths, `flow_agent.db`, `FLOWKIT_*` environment variables,
browser storage keys, authentication headers, and `/fk-*` commands remain
compatible with installations and saved projects from the base code.

The dashboard checks models at the repository's `main/agent/models.json`.
A check may report that the catalog is missing if the configured repository is unavailable.
Set `VITE_MODEL_CATALOG_URL` before building to use another published catalog.

The extension's display name is FrameWeaver, version 0.3.5. Default image,
high-resolution image, video export, and prompt download names use the
`frameweaver-` prefix. User-supplied titles and original uploaded filenames
keep their saved names. Existing database content is not part of source packages.
