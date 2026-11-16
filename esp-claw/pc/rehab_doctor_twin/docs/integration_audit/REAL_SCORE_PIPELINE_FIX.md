# Real Score Pipeline Fix

## Fixed source locations

- `C:\ec\esp-claw\pc\rehab_score_service\cloud_interface\session_processor.py`
- `C:\ev\rehab_doctor_twin\backend\app\pc_local_runtime_service.py`
- `C:\ev\rehab_doctor_twin\backend\app\local_csv_stream_service.py`
- `C:\ev\rehab_doctor_twin\scripts\verify_real_score_from_task006.ps1`

## Scoring-service fix

The scorer now creates the job output directory and its internal `trajectories/` directory before action processing. This preserves the worker contract for both cases:

- one or more complete actions produce per-action trajectory files;
- zero complete actions still produce `score_result.json` and an empty `trajectories/` directory.

This prevents a legal no-action result from being reported as `scoring_failed` solely because no per-action trajectory file was generated.

## No formula change

The fix does not change:

- service version `0.2.1`;
- Job API routes;
- frozen `/score_trial`, `/reset_fatigue_state`, or `/set_baseline_from_rows`;
- task.json seven-field contract;
- drink full-cycle segmentation thresholds;
- DTW scoring formula;
- EMG scoring formula;
- score weights;
- standard trajectory profile.

## No valid cycle behavior

When no complete valid action cycle is detected, the service writes a real full result with:

- `valid=false`;
- `invalid_reason=no_valid_repetition` or the existing manifest-validation reason;
- `actions=[]`;
- no session final score;
- feedback explaining that no complete valid action cycle was detected.

This is a business result, not a program crash.

## TASK006 handling

TASK006 is not changed. Its CSV, `task.json`, `job_status.json`, failed state, and runtime directory remain untouched. Re-verification must create a new Job ID and upload the same whole CSV through the official Job API.

TASK006 is only a repair-verification sample. The formal system is always indexed by the current `session_id` under `runtime_data\sessions\<session_id>`. For a new formal session, the complete CSV, `task.json`, `job_status.json`, `result\score_result.json`, and `result\trajectories\` all stay under that same session directory. Doctor logs, reports, and waveform replay resolve records by that `session_id`; they must not depend on fixed `20260726_TASK006`.

## PC runtime fix

The doctor backend `_active_stats()` recursion was removed. UDP mode now reads `self._udp.stats()`, CSV modes read `self._csv_stream.stats()`, and connection hints are handled outside `_active_stats()`.

CSV `last_received_at` now reflects the last real valid frame time instead of refreshing on every stats call.
