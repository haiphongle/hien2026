# Changelog

## Unreleased

- Add maintainer-supplied previews for illustrated moral stories, stickman videos,
  and 2D family stories, alongside timeline and style-library screenshots.
- Highlight the drag-and-drop editing workspace and short-form storytelling in
  the English and Vietnamese READMEs; add a full-size showcase guide.
- Remove embedded EXIF/XMP metadata from the five new screenshots while preserving
  their original pixels.

## 0.1.0-preview.2 — 2026-10-14

- Correct the project description: FrameWeaver is independently maintained with
  additional editing, media, and speech tools, rather than only a renamed distribution.
- Expand the feature overview and place reused-code attribution in the license
  section. Preserve the original MIT notice and bundled asset terms.
- Clarify the origin of historical media examples and FrameWeaver UI screenshots.

## 0.1.0-preview.1 — 2026-10-14

Initial public FrameWeaver release, including its editing and speech workspace
and MIT-licensed code adapted from Flow Kit.

- Introduce the FrameWeaver name, banner, favicon, dashboard branding, and
  extension display name (0.3.5).
- Update repository links and default downloaded image/video/prompt filenames.
- Add English and Vietnamese onboarding, publication notes, contribution
  guidance, private security reporting, and CI configuration.
- Add source-only packaging with credential-pattern checks, metadata exclusions,
  file hashes, and accompanying third-party notices.
- Align two ASR test fixtures with the source-audio probe and add a silent-source
  regression check.
- Declare the multipart runtime dependency required by upload routes on a fresh
  installation.
- Declare the memory-monitor dependency used by ProPainter jobs and preserve all
  source frames when mixing music and narration with Linux FFmpeg.

### Known limitations

Live generation has not been re-verified for this FrameWeaver preview. The
source still carries the documented Veo transport gaps and external-volume
metadata/cache issues. Optional engines require separate setup and licensing
review. See the README for the current scope and verification limits.
