# Formal Session Log Reconciliation

## Problem

A real formal training session can already have a strict 86-column CSV and Score Job runtime files, while the doctor backend does not yet have a session index entry. The doctor data log records training facts, so a score failure must not erase the completed training record.

## Boundary

Reconciliation reads only the fixed score runtime root:

`SCORE_RUNTIME_DATA_DIR / "sessions"`

It does not copy, modify, or delete CSV files, `task.json`, `job_status.json`, score results, reports, or score service runtime directories. It never calls the Score Job API and never retries scoring.

## Accepted sessions

A runtime session is accepted only when:

- `session_id` matches `^[0-9]{8}_TASK[0-9]{3}$`
- `task.json` exists and declares a local CSV file name
- `job_status.json` is read as status evidence
- the declared CSV exists under the resolved session directory
- all resolved paths remain inside the fixed runtime root
- the CSV header matches the frozen 86-column contract
- streaming `csv.reader` counts at least 20 legal rows
- duration is at least 2 seconds

`SESSION_DEMO_*`, showcase records, invalid ids, missing files, path escapes, and undersized captures are excluded.

## Metadata written by the doctor backend

The doctor backend writes its own `session.json` metadata under the doctor project data store. It includes training facts such as `session_id`, `patient_id`, `task_id`, `started_at`, `ended_at`, `duration_seconds`, `start_log_seq`, `end_log_seq`, `valid_row_count`, `csv_file`, `csv_relative_path`, `source_mode=device`, `source_origin=pc_udp_6006_ingest`, `formal=true`, `record_kind=formal`, and `training_status=completed`.

Score status is stored separately through `score_status`, `score_job_id`, `report_ready`, `final_score`, `score_error_code`, and `score_error_message`.

## Score failure display

When a Score Job fails because the scorer did not generate result artifacts, the doctor log still shows the completed training. Normal list views display a Chinese summary such as `评分服务未生成结果文件`; full raw English errors remain internal diagnostics.

TASK006 remains a failed historical job. Reconciliation does not rewrite its `job_status.json`, does not create result artifacts, and does not change failed to completed. A new verification run must use a new Job ID.

TASK006 is not a production data source. It is only a known sample for validating the repair. Normal reconciliation scans `runtime_data\sessions`, accepts each valid `^[0-9]{8}_TASK[0-9]{3}$` session by its own `session_id`, and keeps all CSV, scoring status, full result, report references, and waveform replay references tied to that same directory.

## Overview, reports, and prediction boundary

Formal training count and recent training records include reconciled completed sessions even if scoring failed. Reports, patient reports, predictions, score trend, recent score, action reconstruction, and printable reports still require successful valid scoring artifacts and remain excluded for failed TASK006-style sessions.

## User verification

The user can start the backend in normal PC local mode and confirm that valid formal session directories are discovered by their own `session_id`. For the historical `20260726_TASK006` sample specifically, it should appear as a completed training fact with score failed and report not generated. Prediction and report pages should continue to show no result for that failed sample.

## Automatic watcher behavior

The PC-local runtime now runs reconciliation as a low-frequency watcher, not only as a startup import. It tracks a small signature for each session from `task.json`, `job_status.json`, score result/report mtimes, CSV size, and CSV mtime. If a signature is unchanged, the watcher does not reread the full CSV.

When a new valid session appears, the doctor log is upserted immediately by `session_id`. While CSV upload or scoring is in progress, the same record is updated with collecting/uploading/scoring status. When `job_status.json` becomes succeeded, the watcher reads `result\score_result.json`; `valid=true` enables real score/report state, while `valid=false` remains a legal no-action result. Failed jobs update the same record as score failed.

Waveform replay never creates another log entry and never triggers scoring.
