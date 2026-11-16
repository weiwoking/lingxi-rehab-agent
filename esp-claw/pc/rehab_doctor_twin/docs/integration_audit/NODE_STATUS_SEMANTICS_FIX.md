# Node Status Semantics Fix

## Scope

This note covers only Node 1 and Node 2 online semantics for the PC UDP ingestion path.

## Previous Risk

`node1_rx` and `node2_rx` are cumulative packet counters. Treating those fields with a boolean helper such as `_truthy()` is wrong because ordinary counts like `2`, `12`, or `128` should be considered observed packet counts, not invalid boolean values.

## Current Semantics

`backend/app/pc_local_runtime_service.py` derives Node 1/2 status with `_node_state(row, node)`:

- Parse `nodeN_rx` as a non-negative integer.
- Parse `nodeN_age_ms` as a finite number.
- Return `unknown` when the row is missing, the fields are malformed, `nodeN_rx <= 0`, or age is negative.
- Return `stale` when `nodeN_age_ms > PC_NODE_STALE_MS`.
- Return `normal` only when the node has received at least one packet and the age is within the stale threshold.

The resulting device payload exposes:

- `online = connection_status == "normal"`
- `connection_status` as `unknown`, `stale`, or `normal`
- `packet_count` from the numeric cumulative `nodeN_rx`
- `latency_ms` from `nodeN_age_ms`

## Frontend Display

- `frontend/src/contracts/realtimeMessages.ts` models `connection_status` and nullable `packet_count`.
- `frontend/src/pages/LiveMonitorPage.tsx` displays Node status, `rx`, sequence, gap, and age/latency.
- Existing `_truthy()` usage remains limited to IMU/EMG validity flags and row validity checks, not Node 1/2 cumulative counters.

## Static Review Limits

This review did not send UDP frames or connect hardware. Final normal/stale/unknown behavior still needs user-side runtime verification with real device data.
