# English voices in FrameWeaver

Kokoro 0.9.4 from `kokoro-main` supplies six English presets. The downloaded
source is installed as a package in `.flowkit/kokoro-venv`; it is not imported
by FastAPI. On Apple Silicon, PyTorch MPS runs the model; other supported hosts
use Kokoro's CPU/CUDA selection. No TTS service key is required.

## Use

In **Nhận dạng, dịch & lồng tiếng**, select **Tiếng Anh**, listen to a demo and
choose a voice for each speaker. Then use **Tự xử lý lồng tiếng**, review the
generated audio and **Áp dụng vào timeline**. This reads the English script;
translate the script first when the source text is in another language.

In **Tạo lời đọc theo cảnh**, select **Kokoro · Tiếng Anh** in voice settings.
Both workflows reuse the existing audio cache, timing checks and export path.

| Preset | Voice | Accent |
| --- | --- | --- |
| kokoro_af_heart | Heart | American, female |
| kokoro_af_bella | Bella | American, female |
| kokoro_am_michael | Michael | American, male |
| kokoro_am_puck | Puck | American, male |
| kokoro_bf_emma | Emma | British, female |
| kokoro_bm_george | George | British, male |

## Install on another Mac

From the FrameWeaver root, with the downloaded `kokoro-main` directory present:

```sh
python3.12 -m venv .flowkit/kokoro-venv
.flowkit/kokoro-venv/bin/python -m pip install --no-cache-dir -r requirements-kokoro.txt
```

First preparation downloads the model, English text processing data and six
voice files. Select **Chuẩn bị demo** in the library if demos are missing.
All advertised voices are loaded before the worker marks the model ready for
offline use. The American/British pipelines share one loaded model.

`KOKORO_PYTHON` can override the isolated interpreter path (including Windows).
Status: `GET /api/tts/kokoro/status`; presets: `GET /api/tts/kokoro/voices`;
prepare/resume demos: `POST /api/tts/kokoro/previews`.

Persistent data:

- `.flowkit/kokoro-cache/`: downloaded model and bounded worker log.
- `output/_shared/kokoro_previews/`: six reusable WAV demos.
- `output/_shared/speech/videos/<video UUID>/audio/`: per-cue WAVs with content,
  engine, voice and speed signatures. Selecting another voice cannot reuse
  Vietnamese audio accidentally.

Local TTS uses one shared semaphore and releases the other resident model when
switching engines. An idle worker releases its memory after ten minutes.

## Verification

The integration is covered by `test_kokoro_integration.py`, the speech and
VieNeu regression tests, and `narration_preferences.test.mjs`. Runtime checks
on the installed Mac generated all six demos, restarted from the local cache,
and generated narration at normal and increased speed without network access.

Upstream license: Apache-2.0, retained in `kokoro-main/LICENSE`.
