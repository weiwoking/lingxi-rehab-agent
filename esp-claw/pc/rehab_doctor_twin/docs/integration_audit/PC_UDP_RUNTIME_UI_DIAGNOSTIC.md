# PC UDP Runtime UI Diagnostic

## Confirmed External Evidence

- Formal workspace: `C:\ev\rehab_doctor_twin`.
- Score service workspace is evidence-only for this round: `C:\ec\esp-claw\pc\rehab_score_service`.
- Manual process check confirmed UDP `6006` and backend TCP `8001` were served by the same backend process: `backend\.venv\Scripts\python.exe run.py`.
- TASK006 evidence from the score runtime showed a real device stream reached the PC chain and produced the formal 86-column CSV input:
  - session/job: `20260726_TASK006`
  - csv: `DATA_SEQ0000000001.csv`
  - log sequence: `1` to `2331`
  - score job input validation: `inputs_validated = true`
- The later score failure `scorer did not create the trajectories directory` is outside this round and was not modified.

## Observed Problem

The real UDP stream could be cached into the formal training path, but `GET /api/patient/runtime` and the patient UI could still show a waiting/zero state. The source audit found no second `PcLocalRuntimeService` instance in the active `pc_local` path. The concrete gap was that the previous runtime status was driven almost entirely by valid-frame state. Raw UDP datagrams were not counted before parsing, and the UI had no separate way to show:

- UDP packets are arriving.
- Packets are failing UTF-8, CSV, 86-column, valid flag, signal, order, or duplicate checks.
- Valid frames are stale while raw UDP may still be arriving.

That made "no UDP has ever arrived" and "UDP arrived but no valid 86-column frame was accepted" collapse into the same waiting-looking user experience.

## Backend Call Chain

### A. UDP Receive Path

- File: `backend/app/pc_udp_ingest_service.py`
- Class: `PcUdpIngestService`
- UDP protocol: `_UdpProtocol.datagram_received()`
- Entry function: `PcUdpIngestService.accept_datagram(data, addr)`
- Shared object: the `_udp` member owned by the single `PcLocalRuntimeService` instance.
- Raw UDP fields updated immediately after `recvfrom()` succeeds and before decode/CSV parsing:
  - `udp_datagram_count`
  - `udp_bytes_total`
  - `udp_last_source_ip`
  - `udp_last_source_port`
  - `udp_last_received_at_ms`
  - `udp_last_age_ms`
  - `udp_datagram_rate_hz`

### B. Valid Frame Parse Path

- File: `backend/app/pc_udp_ingest_service.py`
- Class: `PcUdpIngestService`
- Function: `_accept_row(values, addr)`
- 86-column validation: `len(values) == len(CSV_COLUMNS)`
- CSV parsing: Python `csv.reader`, not `line.split(",")`
- Header handling: exact `CSV_COLUMNS` header row is skipped.
- Deduplication key: `(source_ip, log_seq, time_ms)`
- Valid frame object: `row` with `_log_seq_int`, `_time_ms_float`, `_received_at_ms`, `_source_ip`, `_source_port`, and `_raw_values`.
- Parse counters:
  - `valid_frame_count`
  - `invalid_frame_count`
  - `duplicate_frame_count`
  - `last_valid_frame_at_ms`
  - `last_valid_frame_age_ms`
  - `last_column_count`
  - `last_log_seq`
  - `last_time_ms`
  - `last_parse_error`
  - `last_invalid_frame_at_ms`
  - `last_duplicate_frame_at_ms`

### C. Runtime Update Path

- File: `backend/app/pc_local_runtime_service.py`
- Class: `PcLocalRuntimeService`
- Function: `_receive_udp_row(row, addr)`
- Session-active dependency: no for realtime state; yes only for formal row caching.
- Behavior:
  - Any accepted valid row updates `_last_row` and rebuilds `_snapshot`.
  - Invalid or duplicate callbacks still rebuild and publish `_snapshot`, so counters and parse status are visible.
  - Formal training rows are appended only inside `if self._training_state == "RECORDING"`.
- Sensor snapshot functions:
  - `_sensor_samples(row)` exports four IMU samples and four sEMG samples from the latest real valid row.
  - `_devices(row)` exports node/sensor `connection_status`.
  - When the valid stream is stale, the latest values remain visible as last-known data, but devices/samples are no longer marked normal/online.

### D. Formal Training Cache Path

- File: `backend/app/pc_local_runtime_service.py`
- Class: `PcLocalRuntimeService`
- Function: `_receive_udp_row(row, addr)`
- ACTIVE condition: `self._training_state == "RECORDING"`
- Decoupling:
  - Realtime state updates on valid frames regardless of session state.
  - CSV/formal cache rows are appended only after the patient starts training.
  - Stopping training stops formal cache append, not realtime display.

### E. Runtime API Path

- File: `backend/app/main.py`
- Route: `GET /api/patient/runtime`
- Function: `patient_runtime()`
- Read object: module-level imported `pc_local_runtime_service`
- Snapshot function: `pc_local_runtime_service.snapshot()`
- Response schema: `{ "snapshot": RehabSnapshot }`

### F. WebSocket/Polling Path

- File: `backend/app/main.py`
- WebSocket route: `/ws/live`
- Initial frame: `snapshot_message()`
- In `pc_local` mode, `snapshot_message()` calls `runtime.state_message()`.
- File: `backend/app/pc_local_runtime_service.py`
- Publish function: `_publish()`
- Data source: same `_snapshot` returned by `snapshot()`
- Broadcasts:
  - `rehab.state` with `{ snapshot }`
  - `device.status` with `snapshot["devices"]`
- Monitor: `_monitor_stream_state()` watches valid stream status, UDP stream status, and parse status so stale transitions are published.

### G. Frontend Path

- Service: `frontend/src/services/runtimeApi.ts`
  - `fetchPatientRuntime()` reads `/api/patient/runtime`.
- WebSocket: `frontend/src/services/websocketClient.ts`
  - connects to `/ws/live`.
  - on open also calls `fetchCurrentState()`.
- Store: `frontend/src/store/rehabStore.ts`
  - `applySnapshot()` stores the full snapshot.
  - `applyRealtimeMessage()` applies the same `rehab.state` snapshot.
  - `appendRuntimeSamples()` appends only valid sample points and does not fabricate missing waveforms.
- Types: `frontend/src/contracts/realtimeMessages.ts`
  - `RehabSnapshot` now includes raw UDP and valid-frame diagnostic fields.
- Page: `frontend/src/pages/LiveMonitorPage.tsx`
  - reads the store snapshot.
  - displays the realtime data chain and expanded diagnostics.

## Shared Runtime State

The audit found the active `pc_local` chain uses one runtime service object:

- `backend/app/pc_local_runtime_service.py`
  - `pc_local_runtime_service = PcLocalRuntimeService()`
  - `PcLocalRuntimeService.__init__()` creates `self._udp = PcUdpIngestService(self._receive_udp_row)`
- `backend/app/main.py`
  - imports that same `pc_local_runtime_service`
  - startup calls `active_runtime_service().start()`
  - `/api/patient/runtime`, `/api/state/current`, and `/ws/live` read the same service snapshot.

No route-local `PcLocalRuntimeService()` or request-local `PcUdpIngestService()` instance was found in the audited source path.

## API Schema Additions

The runtime snapshot now includes these diagnostic fields while preserving existing aliases:

- `udp_stream_status`
- `parse_status`
- `has_current_valid_frame`
- `udp_datagram_count`
- `udp_bytes_total`
- `udp_last_source_ip`
- `udp_last_source_port`
- `udp_last_received_at_ms`
- `udp_last_age_ms`
- `udp_datagram_rate_hz`
- `valid_frame_rate_hz`
- `last_valid_frame_at_ms`
- `last_valid_frame_age_ms`
- `last_column_count`
- `last_parse_error`
- `last_invalid_frame_at_ms`
- `last_duplicate_frame_at_ms`

Compatibility mapping:

- `last_source_ip` maps to the latest UDP source IP.
- `last_source_port` maps to the latest UDP source port.
- `receive_rate_hz` remains the valid-frame rate.

## Status Rules

- No UDP received:
  - UDP: waiting
  - parse: waiting
  - UI: waiting for device data
- UDP received, no valid frame, recent UDP:
  - UDP: received/normal
  - parse: invalid
  - UI: received packets but no valid sensor frame
- Recent valid frame:
  - UDP: normal
  - parse: normal
  - UI: realtime data updating
- Valid frame stale:
  - parse: stale or invalid-active depending on whether raw UDP is still arriving
  - devices/sensors: stale, not normal
- UDP still arriving but valid frames stopped:
  - UDP: normal
  - parse: invalid-active
  - UI: separates parse failure from network interruption

## Patient Diagnostic UI

File: `frontend/src/pages/LiveMonitorPage.tsx`

The patient page now shows a compact "实时数据链路" section independent of training start:

- UDP 接收
- 数据来源
- 最近数据包
- UDP 包频率
- 数据解析
- 有效帧
- 无效帧
- 重复帧
- 有效帧频率
- 在线传感器

The expandable diagnostics show counters, last times/ages, last parse error, and IMU/sEMG valid flags. It does not display full UDP payloads, full CSV rows, raw patient history, filesystem paths, internal objects, stack traces, or secrets.

## Realtime Chain Versus Training Chain

Realtime chain:

P4 -> UDP 6006 -> `PcUdpIngestService` -> `PcLocalRuntimeService._snapshot` -> API/WebSocket -> frontend store -> patient UI.

Training chain:

Patient clicks start -> `PcLocalRuntimeService.start_training()` -> `RECORDING` -> valid rows appended to `_rows` -> patient clicks stop -> CSV bytes written -> score job called.

The fix keeps these chains decoupled:

- Realtime display can update before training starts.
- Realtime display continues after training stops.
- Formal CSV rows are not cached until training is in `RECORDING`.
- Receiving UDP does not automatically create a session.
- Diagnostic UI does not trigger scoring.

## Not Handled In This Round

- The scoring failure `scorer did not create the trajectories directory`.
- Score service code under `C:\ec\esp-claw\pc\rehab_score_service`.
- P4/S3 firmware or ports.
- 86-column order or CSV format.
- Score Job API contract.
- Reports, predictions, task library, Agent, action reconstruction, printable report, showcase isolation.

## User Verification Steps

These steps are for the user to run manually after restarting the backend/frontend in their environment:

1. Restart the PC backend normally.
2. Open `/patient`.
3. Connect the real P4 stream to UDP `6006`.
4. Confirm "UDP 数据包总数" increments even if parsing fails.
5. Confirm "有效帧总数" increments only after valid 86-column rows pass validation.
6. Confirm raw UDP rate and valid-frame rate are shown separately.
7. Confirm start training is enabled only when a recent valid frame exists.
8. Start/stop a real training session and confirm realtime display continues after stop.

No runtime verification was performed by Codex in this round.
