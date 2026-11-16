# Score Full Result Report Fix

## Scope

This note covers only the doctor PC runtime change that prevents the compact score-job result from being treated as the full `score_result`.

## Confirmed Score Service Contract

- The score service version observed statically is `0.2.1`.
- `GET /api/v1/score/jobs/{job_id}/result` returns `worker.compact_result(job_id)`.
- The compact response is a small `rehab_score_result_v1` projection intended for polling/result acknowledgement, not the complete scoring payload.
- The complete result is written by the score service under `runtime_data/sessions/<session_id>/result/score_result.json`.
- No full-result HTTP endpoint was found in the reviewed score-service API file.

## Doctor Runtime Behavior

- `backend/app/config.py` defines `SCORE_RUNTIME_ROOT`, `SCORE_RUNTIME_DATA_DIR`, `SCORE_FULL_RESULT_WAIT_SECONDS`, and `SCORE_FULL_RESULT_POLL_INTERVAL_SECONDS`.
- `backend/app/pc_local_runtime_service.py` still calls the compact result endpoint after the job succeeds, but uses it only to confirm schema and session identity.
- The doctor runtime then waits for `SCORE_RUNTIME_DATA_DIR/sessions/<session_id>/result/score_result.json`.
- `session_id` must match `^[0-9]{8}_TASK[0-9]{3}$`.
- The resolved result path must remain under `SCORE_RUNTIME_DATA_DIR`.
- The full result must be a JSON object, must match the session id, and a valid result must expose a finite final score.
- `report.json` is generated from the full result object, while `score_job_result.json` stores the compact projection separately.

## Report Contract

- `score_result.json` is the persisted full scoring object.
- `score_job_result.json` is the compact score-job projection.
- `report.json` is built from the full score result and no longer uses compact fields as if they were complete metrics.
- Report versions identify both files with `raw_score_result_path=score_result.json` and `compact_score_result_path=score_job_result.json`.

## Static Review Limits

This review did not start services, call HTTP endpoints, run a browser, build, test, connect hardware, or replay UDP/P4 traffic.
