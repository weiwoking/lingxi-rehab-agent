# P4 Rehab TTS service

This independent FastAPI service converts exact input text to MP3 with
`edge-tts`, caches results under `%LOCALAPPDATA%\P4RehabTTS`, and exposes the
audio to the P4 over HTTP on port 8765.

Use the double-click launcher in
`tools\P4_Rehab_TTS_Launcher\P4_Rehab_TTS_Launcher.cmd`. Do not start this
service inside the score service or on port 8020.

The launcher installs the exact versions in `requirements-lock.txt` on first
start. `requirements.txt` mirrors the runtime top-level dependencies for normal
Python tooling.

Configuration is through environment variables:

- `P4_REHAB_TTS_VOICE` (default `zh-CN-XiaoxiaoNeural`)
- `P4_REHAB_TTS_RATE` (default `+0%`)
- `P4_REHAB_TTS_PITCH` (default `+0Hz`)
- `P4_REHAB_TTS_DATA_DIR` (default `%LOCALAPPDATA%\P4RehabTTS`)
- cache TTL/count/size variables declared in `rehab_tts_service/config.py`

The service never logs full input text. Generated MP3 files, logs, and the
virtual environment are intentionally outside the repository.

The P4 consumer uses generation-based latest-wins behavior. A new speak request
immediately invalidates the old generation and stops audio that is already
playing. Because TTS uses a synchronous HTTP call, a new request waits when the
old call is still running and begins processing only after that call returns or
times out. The old late response is discarded and is never played.

On the P4, TTS request and stream-open failures return audio state to `IDLE`
while retaining `last_error`. A player runtime error enters `ERROR` and retains
`last_error`. The next speak request clears the error and starts a new
generation; stop restores `IDLE` but preserves the retained error detail.
