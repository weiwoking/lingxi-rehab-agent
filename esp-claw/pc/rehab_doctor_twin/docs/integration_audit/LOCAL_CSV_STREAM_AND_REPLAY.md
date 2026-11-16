# Local CSV Stream and Replay

## Scope

This change adds backend-only CSV tail/replay sources for patient realtime display. Diagnostic CSV modes do not create sessions, write CSV files, submit Score Jobs, or enter reports, prediction, trend, or doctor log records. Formal automatic session mode instead reads the current score-runtime session CSV by `session_id`.

## Why the browser does not read `C:\`

The browser must not read local absolute paths. The configured CSV path stays on the backend process through environment variables. The frontend only receives sanitized runtime fields such as file name, row counters, status, `log_seq`, and `time_ms` through the existing patient runtime API and WebSocket snapshot.

## Source modes

- `PC_STREAM_SOURCE_MODE=udp` keeps the existing UDP 6006 live device path and remains the default.
- `PC_STREAM_SOURCE_MODE=csv_tail` reads new complete rows appended to the explicitly configured CSV.
- `PC_STREAM_SOURCE_MODE=csv_replay` replays an explicitly configured completed CSV by adjacent `time_ms` gaps.

In explicit diagnostic modes the CSV path is accepted from `PC_STREAM_CSV_PATH`. The formal patient-page path does not use a hard-coded TASK id and does not require `PC_STREAM_CSV_PATH`.

## Formal automatic session mode

`PC_STREAM_CSV_PATH` is now a diagnostic/development override, not the formal patient-page data source. In formal PC-local mode the backend selects the current CSV from `SCORE_RUNTIME_DATA_DIR\sessions\<session_id>\task.json`.

For a growing active session, the backend attaches `csv_tail` to that session CSV and reads only new complete rows. For a completed session, the backend attaches `csv_replay`, starts from the first row once, follows `time_ms`, and then leaves replay controls available.

The transition from `csv_tail` to `csv_replay` is based on the task manifest being finalized (`end_time` plus `end_reason`). Scoring states such as pending, processing, completed, failed, or valid=false do not determine playback mode and must not rebuild the player.

The browser never reads a local filesystem path directly; it receives frames through the backend WebSocket/API only.

## Shared 86-column parsing

CSV tail and replay use `csv.reader` and the same frozen 86-column header and row parser used by the UDP ingest path. Rows are rejected when the column count is not 86, `log_seq` or `time_ms` cannot be parsed, no valid sensor flag exists, signals are non-finite, or all required signal values are zero. Invalid rows do not update realtime charts.

## Tail mode

Tail mode validates the header, stores the current file offset, reads only newly appended bytes, preserves partial lines until a newline arrives, and parses only complete rows. Truncation or file replacement resets the parser, offset, and header validation. No previous frame is repeated as a new frame.

## Replay mode

Replay mode validates the completed CSV and supports play, pause, resume, restart, and speeds `0.5x`, `1.0x`, and `2.0x`. Playback stops at EOF, preserves the last realtime frame, and reports replay completion. It does not loop automatically.

## Runtime integration

UDP, CSV tail, and CSV replay feed the same `PcLocalRuntimeService` snapshot path. The existing `/api/patient/runtime`, `/api/state/current`, and `/ws/live` consumers continue to read one runtime state. Source modes are mutually exclusive; CSV modes do not start the UDP listener.

## Formal training isolation

CSV replay controls are waveform-viewing controls only. They do not create formal sessions, append recording buffers, write new CSV files, call the score service, or alter `job_status.json`.

## Scoring separation

CSV replay is a waveform-viewing path only. It must not feed resampled chart data, WebSocket snapshots, cached frontend points, or replay runtime rows into scoring.

Formal scoring uses the original complete CSV file through the Score Job API. The score service is responsible for internal action-cycle segmentation and session-level aggregation.

## User verification

For diagnostic mode, the user can manually start the PC backend with `PC_STREAM_SOURCE_MODE=csv_replay` or `csv_tail` and an explicit `PC_STREAM_CSV_PATH`, then verify that IMU and sEMG values update in the patient realtime page. For formal mode, start the normal PC backend and let the watcher select the latest valid score-runtime session by `session_id`.
