# P4 Rehab TTS Launcher

Double-click `P4_Rehab_TTS_Launcher.cmd`. The dedicated window shows service,
provider, LAN IPv4, port, health/docs URLs, and log/cache locations. Its buttons
start, stop, restart, and refresh the service without requiring a command line.

On first start only, the launcher creates an isolated virtual environment under
`%LOCALAPPDATA%\P4RehabTTS\venv` and installs the pinned runtime requirements.
Before creating or using that environment, it checks the actual selected
interpreter and requires Python 3.10 or newer. An older or unparseable version
stops before venv creation, dependency installation, or service startup. It
does not install again unless `requirements-lock.txt` changes.

The stop path requires the PID file plus matching process command line and
service directory. A foreign process on port 8765 is reported and never killed.

A new P4 speak request immediately invalidates the previous generation and
stops audio that is already playing. If the old generation is still inside the
synchronous TTS/HTTP call, the new request starts processing only after that
call returns or times out; the old late result is discarded and never played.

TTS request and stream-open failures return the P4 audio service to `IDLE` while
retaining `last_error`. A player runtime error enters `ERROR` and also retains
`last_error`. A later speak request clears the previous error and starts a new
generation. Stop returns the service state to `IDLE` but does not clear the
retained error detail.
