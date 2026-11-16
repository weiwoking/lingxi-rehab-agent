# rehab_scan_transfer

`rehab_scan_transfer` is an independent ESP-IDF component that consumes an
already committed `rehab_scan_capture` directory. It never opens Camera/ToF,
does not run inference, and does not delete scans.

It validates the frozen `deltar_scan_input.v1`, frame, and ToF contracts, then
streams a fixed-length multipart request from SD using one 16 KiB buffer. The
default endpoint is `http://192.168.137.1:8030/api/v1/scan/jobs`. Parts are
ordered as `scan_json` followed by frame JSON, ToF JSON, and RGB565 for each
contiguous frame. The complete encoded request, including multipart headers,
must be at most 3 MiB.

Each request uses a fresh 96-bit random hexadecimal boundary. The image body is
not scanned for that marker; a theoretical collision remains possible but is
negligible for the bounded payload and frozen random-boundary design.

The asynchronous state machine is `idle -> uploading -> waiting_processing ->
receiving_result -> completed`; any error ends at `failed`. Only one worker is
allowed. Polling defaults to every 5 seconds with an absolute 180-second
deadline. Abort is cooperative between file/HTTP chunks and wait slices; an
active HTTP call can take up to its configured timeout to return.

The service identifier (`svc_<32 hex>`) and pipeline `job_id` are deliberately
stored separately. Result validation compares `result.job_id` only with the
pipeline ID. A pipeline result of `rejected` is a normal completed transfer.

`latest` chooses the lexicographically greatest complete `scan_*` directory
without an existing formal result. Explicit scan IDs accept only
`[A-Za-z0-9_-]{1,128}`. The result is downloaded with a 256 KiB bound to
`CLAW_PATH_DATA/rehab/scan_results/<scan_id>/result.json.tmp`, strictly checked
for v1.1 identity, finite numbers, arrays, at most 100 objects, and critical
object summary fields, then no-overwrite renamed to `result.json`. Invalid or
partial temporary files are deleted.

Console commands:

```text
rehab_scan_send latest
rehab_scan_send <scan_id>
rehab_scan_send status
rehab_scan_send abort
```

There is no automatic retry, queue, resume/range upload, database, TLS, or
authentication. To run the PC service:

```powershell
& "C:\ec\_conda_envs\vision_interpreter\python.exe" `
  "C:\ec\_deltar_p4_pipeline\scripts\run_scan_service.py" `
  --host 192.168.137.1 --port 8030
```

Build, flash, and monitor are intentionally left to the user.
