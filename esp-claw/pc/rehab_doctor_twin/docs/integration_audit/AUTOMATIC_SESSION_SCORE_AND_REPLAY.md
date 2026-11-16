# Automatic Session Score and Replay

## Product Flow

The patient page no longer owns the formal training lifecycle. A formal training unit is the score runtime session directory:

```text
runtime_data\sessions\<session_id>\
  task.json
  DATA_SEQ*.csv
  job_status.json
  result\
    score_result.json
    trajectories\
```

The backend watches `runtime_data\sessions`, accepts only `^[0-9]{8}_TASK[0-9]{3}$`, resolves paths under the fixed score runtime root, reads the CSV named by `task.json`, and upserts one doctor log record per `session_id`.

## Why Manual Buttons Were Removed

The real device and score service create and finalize formal sessions. A patient-side start or stop button could create a second source of truth. The patient page now shows state only: waiting for data, receiving data, replaying waveform, uploaded, scoring, completed, invalid, failed, or archived.

Legacy start/stop API compatibility can remain for older tools, but the formal patient page does not call it.

## CSV Playback

For a growing CSV, the backend uses `csv_tail` against the current active session CSV. It tracks file offset, keeps partial lines buffered, parses only complete rows with `csv.reader`, enforces the frozen 86-column contract, and reuses `CsvFrameParser`.

For a complete uploaded CSV, the backend switches to `csv_replay`, starts from the first row, follows `time_ms` at 1.0x by default, plays once automatically, and then keeps replay controls available for history playback.

Playback mode is decided by CSV/session completeness, not scoring status. A session whose `task.json` has a valid `end_time` and `end_reason` remains `csv_replay` while `job_status.json` moves through `accepted`, `queued`, `scoring`, `succeeded`, or `failed`. Score status updates must not stop, rebuild, or switch the current replay player back to `csv_tail`.

Automatic replay is marked complete only after the stream reports `csv_replay_state=complete`. Issuing the initial `play` command is tracked separately from replay completion. WebSocket reconnects and page refreshes do not replay again while the backend process is alive; a backend restart may auto-play the latest complete formal session once.

The replay loader preserves play requests that arrive before CSV initialization is complete. `control("play")` records a pending play request; after the 86-column CSV is loaded and `csv_total_rows` is known, the replay loop enters `playing` instead of overwriting the request with `paused`.

CSV activation now publishes state immediately after selecting the session and again after replay readiness. The snapshot exposes sanitized diagnostics such as `selected_session_id`, `selected_csv_file`, `csv_replay_ready`, `csv_activation_state`, and `csv_activation_error`; it never exposes the absolute local CSV path.

`PC_STREAM_CSV_PATH` remains a diagnostic option only. The formal product path derives CSV paths from `runtime_data\sessions\<session_id>\task.json`.

## Automatic Score State

The score service remains the scoring owner. After a Job API upload is accepted, the score worker reads the same complete CSV under the same `session_id`, writes `result\score_result.json`, writes `result\trajectories\`, and updates `job_status.json`.

The doctor backend observes `job_status.json` and `result\score_result.json`; it does not score replay buffers, WebSocket data, chart data, or sliced CSV.

When a complete runtime session has no `job_status.json`, the doctor backend submits the original on-disk `task.json` bytes plus the original complete CSV bytes. It validates the seven-field task contract locally, but it does not reserialize, reorder, reformat, or rewrite `task.json`; this preserves the identity SHA-256 used by the score service when adopting an existing session directory.

Automatic submission errors are stored in runtime diagnostics and the doctor log summary. Temporary create/adopt failures such as connection errors, timeouts, and transient 503 responses use bounded retry delays of 2 seconds, 5 seconds, and 10 seconds. Contract errors and `session_content_conflict` are not retried automatically. A submitted, queued, scoring, succeeded, or failed job is not submitted again, and no failure path creates a fake score.

## Idempotency

The watcher stores per-session signatures from `task.json`, `job_status.json`, `score_result.json`, `report.json`, CSV size, and CSV mtime. Repeated polling, page refreshes, WebSocket reconnects, and waveform replay update the same session record instead of creating duplicate logs.

The same `session_id` is scored once by the score service state machine. `uploading`, `queued`, `scoring`, `succeeded`, and `failed` states are not resubmitted by the patient page.

## Log and Result Display

The doctor data log is created as soon as a valid formal session and CSV are discovered. It does not wait for `final_score`, `report_ready`, `score.valid`, or non-empty actions.

- `valid=true`: show the real final score and allow report generation.
- `valid=false`: show that no complete valid action was detected, keep the waveform replay, and do not create a fake report or fake score.
- `failed`: show score failure, keep the training log and waveform replay.

## Replay Isolation

Replay after training is historical waveform viewing. It does not create a session, generate a CSV, call scoring, create another doctor log, change `job_status.json`, change `score_result.json`, or enter prediction.

If a new active session appears while a historical replay is visible, the active session wins and the backend switches the patient page to the new session CSV.

Replay control POSTs are immediate feedback only. The backend applies the replay command, rebuilds and publishes the runtime snapshot, and returns both the control result and the latest snapshot. The frontend treats `accepted=false` as an error, applies the returned snapshot immediately, and then relies on `/ws/live` for row counters, IMU waveform samples, sEMG waveform samples, replay completion, and later score/report status.

## Path Safety

The browser never receives an arbitrary absolute CSV path. The backend validates `session_id`, resolves under `SCORE_RUNTIME_DATA_DIR\sessions`, checks that `task.json` names a local `DATA_SEQ*.csv`, verifies the resolved CSV stays inside that session directory, and reads it through the backend stream service.

## Verification Notes

Static checks should confirm that the patient page no longer calls `startTraining()` or `stopTraining()`, no fixed TASK id is used in production code, replay controls do not trigger scoring, and scoring plus waveform playback read the same original CSV through separate read-only paths.
