# Unconnected Data Checklist

This checklist intentionally records items that remain unverified because this handoff used static review only. No services, builds, HTTP calls, UDP traffic, browser sessions, hardware links, Git commit, push, reset, or P4/S3 operations were run.

## Still Needs User Runtime Verification

1. PC UDP 6006 receives the actual device stream from the expected source IP and port.
2. Datagram framing is stable: one CSV row per packet or consistently parseable multi-row packets.
3. Incoming rows match the doctor `CSV_COLUMNS` contract and preserve the expected field count.
4. Runtime stream status changes from waiting to receiving and then to stale when packets stop.
5. Node 1 and Node 2 show `normal` only when `nodeN_rx > 0` and `nodeN_age_ms <= PC_NODE_STALE_MS`.
6. Node 1 and Node 2 show `stale` when cumulative rx remains present but age exceeds `PC_NODE_STALE_MS`.
7. Training start/stop writes a `DATA_SEQxxxxxxxxxx.csv` into the doctor session directory.
8. The score service writes full `runtime_data/sessions/<session_id>/result/score_result.json`.
9. The doctor runtime writes both `score_result.json` and compact `score_job_result.json` into its session directory.
10. Patient and doctor report views read the same saved `report.json`.
11. The report displays only available metrics from the full result and does not fabricate legacy sub-scores.
12. Agent trace is present only for completed valid reports.
13. Score prediction excludes the unconfirmed historical `55.84` session and uses only completed valid device sessions.

## Preserved Facts

- The prior repo-local JSON delivery file is retained only as rejected history and is not the final delivery artifact.
- `20260724_TASK001` with score `55.84` remains unconfirmed as a real worn-device training session.
- Static review cannot mark hardware connectivity, UDP ingest, score execution, patient UI refresh, or doctor UI refresh as passed.
