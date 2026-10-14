# Third-party notices for FrameWeaver

FrameWeaver is a renamed distribution based on Flow Kit. The product name and
distribution repository are described in [BRANDING.md](BRANDING.md).

The root `LICENSE` contains the original Flow Kit MIT copyright notice for
`tuannguyenhoangit-droid`. Its README identifies the source project as
[google-flow-agent](https://github.com/tuannguyenhoangit-droid/google-flow-agent).
Preserve this attribution when sharing this snapshot or a modified version.
At the sharing review, GitHub resolved that historical repository address to
[crisng95/flowkit](https://github.com/crisng95/flowkit). The snapshot's original
copyright notice remains unchanged.

The root license does not replace the terms attached to these bundled assets:

| Files | Origin and notices |
| --- | --- |
| `agent/assets/vieneu/` | VieNeu 3.8.3 voice catalog. Origin, wheel checksum, and Apache-2.0 license are included in that directory. |
| `agent/assets/voice_samples/` | Six VieNeu reference voice clips and transcripts. The local README records both the upstream Apache-2.0 repository license and a separate CC BY-NC 4.0 notice for the legacy voice presets. Keep both notices; do not describe all bundled media as MIT or assume commercial voice rights. |
| `extension/recaptcha_enterprise.js`, `extension/recaptcha__en.js` | Bundled Google reCAPTCHA resources with their own embedded notices. Keep those notices. The root MIT license is not a license to Google services. |
| `docs/images/` | Screenshots and generated illustrations already present in this source snapshot. They demonstrate the existing system; they are not evidence of a new maintainer's own work. Confirm rights and attribution before reusing them in promotion. |

Python and JavaScript dependencies retain their respective licenses. Optional
downloaded TTS models and ProPainter runtimes are not included in the source
package; review their code, model, and voice terms separately before use.

FrameWeaver is a community tool. This snapshot does not establish affiliation with
Google, Anthropic, OpenAI, or the authors of its optional integrations.
