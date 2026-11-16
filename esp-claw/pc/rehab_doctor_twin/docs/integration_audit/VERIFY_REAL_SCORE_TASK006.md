# Verify Real Score from TASK006

## Script

`scripts/verify_real_score_from_task006.ps1`

The script is for manual user verification only. Codex must not run it.

## Prerequisites

- The score service is already running on `SCORE_BASE_URL` or `http://127.0.0.1:8020`.
- `SCORE_RUNTIME_ROOT` points to the score runtime root, or defaults to `C:\ec\esp-claw\pc\rehab_score_service\runtime_data`.
- TASK006 exists at `runtime_data\sessions\20260726_TASK006`.

## What it does

1. Checks the score service health endpoint.
2. Reads TASK006 `task.json`.
3. Reads the complete TASK006 original CSV.
4. Computes CSV SHA-256 and byte size.
5. Chooses a new `YYYYMMDD_TASKNNN` Job ID.
6. Creates a Job through `POST /api/v1/score/jobs`.
7. Uploads the complete CSV through `PUT /api/v1/score/jobs/{job_id}/csv`.
8. Polls `GET /api/v1/score/jobs/{job_id}`.
9. On success, reads compact result through the Job API and full result from `result\score_result.json`.
10. Prints `valid`, `final_score`, `score_version`, action count, `summary`, `data_quality`, `compensation`, `fatigue`, and `feedback`.

## What it does not do

- It does not modify TASK006.
- It does not split, trim, resample, fill, smooth, or rewrite the source CSV.
- It does not manually create `score_result.json`.
- It does not manually create trajectories.
- It does not call internal Python scoring functions.

## Expected interpretation

If the new Job succeeds with `valid=true`, the real score can then flow through the normal doctor backend full-result/report chain.

If the new Job succeeds with `valid=false`, the service successfully processed the whole CSV but did not identify a valid complete action cycle.

If the new Job fails, use the printed `error_code` and `message` to continue debugging service execution or path handling without changing TASK006.
