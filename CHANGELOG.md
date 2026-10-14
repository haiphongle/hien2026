# Changelog

## 0.1.0-preview.1 — 2026-10-14

Initial public FrameWeaver source distribution, based on an existing Flow Kit
snapshot. This release does not claim that inherited features were newly
implemented by the FrameWeaver maintainer.

- Introduce the FrameWeaver name, banner, favicon, dashboard branding, and
  extension display name (0.3.5).
- Update repository links and default downloaded image/video/prompt filenames.
- Add English and Vietnamese onboarding, publication notes, contribution
  guidance, private security reporting, and CI configuration.
- Add source-only packaging with credential-pattern checks, metadata exclusions,
  file hashes, and accompanying third-party notices.
- Align two ASR test fixtures with the source-audio probe and add a silent-source
  regression check.

### Known limitations

Live generation has not been re-verified for this renamed distribution. The
source still carries the documented Veo transport gaps and external-volume
metadata/cache issues. Optional engines require separate setup and licensing
review. See the README for the current scope and verification limits.
