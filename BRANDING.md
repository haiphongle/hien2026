# FrameWeaver

**Name:** FrameWeaver  
**Repository slug:** `frameweaver`  
**GitHub repository:** `phucnguyentvp1/frameweaver`  
**Tagline:** Weave scenes, voices, and sound.

The name reflects assembling individual scenes, narration, audio, and editing
into a complete video. The banner is `docs/images/frameweaver_banner.svg`.

FrameWeaver is developed and maintained in `phucnguyentvp1/frameweaver`, a standalone
GitHub repository. Its workspace includes scene production, timeline editing,
media management, speech analysis, narration, and audio export.

Some code is adapted from Flow Kit. The required notices for that code remain in
`LICENSE` and `THIRD_PARTY_NOTICES.md`; they do not describe ownership of all
FrameWeaver features. Historical generated-media examples retain their source attribution.

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
