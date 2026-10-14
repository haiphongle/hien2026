# Third-party notices for FrameWeaver

FrameWeaver is maintained as its own project with additional editing, media, and
speech workflows. It includes code adapted from
[Flow Kit](https://github.com/crisng95/flowkit) under the MIT license.

The root `LICENSE` preserves the Flow Kit MIT copyright and permission notice for
`tuannguyenhoangit-droid` for the reused code. The original README identifies that project as
[google-flow-agent](https://github.com/tuannguyenhoangit-droid/google-flow-agent).
Preserve that notice when distributing copies or substantial portions of the reused code.
At the sharing review, GitHub resolved that historical repository address to
[crisng95/flowkit](https://github.com/crisng95/flowkit). The snapshot's original
copyright notice remains unchanged. This attribution does not assign authorship
of FrameWeaver's additional features to the original project.

The root license does not replace the terms attached to these bundled assets:

| Files | Origin and notices |
| --- | --- |
| `agent/assets/vieneu/` | VieNeu 3.8.3 voice catalog. Origin, wheel checksum, and Apache-2.0 license are included in that directory. |
| `agent/assets/voice_samples/` | Six VieNeu reference voice clips and transcripts. The local README records both the upstream Apache-2.0 repository license and a separate CC BY-NC 4.0 notice for the legacy voice presets. Keep both notices; do not describe all bundled media as MIT or assume commercial voice rights. |
| `extension/recaptcha_enterprise.js`, `extension/recaptcha__en.js` | Bundled Google reCAPTCHA resources with their own embedded notices. Keep those notices. The root MIT license is not a license to Google services. |
| `docs/images/` | `frameweaver_banner.svg` is the FrameWeaver banner; `frameweaver_dashboard.jpg` and `frameweaver_extension.jpg` were captured from its local UI. The historical generated-media examples in `docs/REFERENCE.md` originate from Flow Kit and are not demonstrations of FrameWeaver's added features. |
| `docs/images/showcase/` | Five demo and workspace screenshots supplied by the FrameWeaver maintainer for the project showcase. Embedded EXIF/XMP metadata was removed without changing image pixels. These media examples are separate from the historical Flow Kit gallery; the code's MIT license does not establish rights to depicted platform branding or third-party content. |

Python and JavaScript dependencies retain their respective licenses. Optional
downloaded TTS models and ProPainter runtimes are not included in the source
package; review their code, model, and voice terms separately before use.

FrameWeaver is a community tool and is not affiliated with
Google, Anthropic, OpenAI, or the authors of its optional integrations.
