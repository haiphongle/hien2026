# Voice preview assets

Six WAV clips and their transcripts are copied from
[pnnbao97/VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS),
`src/vieneu/assets/samples`. The accompanying Apache-2.0 LICENSE is the
upstream repository license. The legacy `src/vieneu/assets/voices.json`
separately labels these six voice presets CC BY-NC 4.0 (non-commercial,
attribution to pnnbao-ump); do not assume the repository license overrides
that voice-specific notice. These legacy samples are distinct from the
v3 Turbo preset assets and their published licensing terms.
The filenames use stable ASCII identifiers to work across filesystems.

These are reference audio clips for the existing OmniVoice engine, not a
VieNeu inference backend. Keeping the small library inside FrameWeaver means
previews still work if the downloaded VieNeu source folder is moved or removed.
