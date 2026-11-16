# rehab_scan_capture

`rehab_scan_capture` is the minimal P4 Camera+ToF scan recorder. It asynchronously captures 1–64 RGB565 frames, pairs each frame with the nearest valid ToF Extended sample, and commits a frozen DELTAR scan v1 directory to writable storage. It does not upload data, run inference, control motion, or expose Agent/Capability/Web/HTTP entry points.

Console commands:

```text
rehab_scan start [--frames N] [--interval-ms N]
rehab_scan status
rehab_scan abort
```

Defaults are 5 frames and a 1000 ms minimum interval. Allowed ranges are 1–64 frames and 500–10000 ms. Scanning and tracking modes are operationally exclusive; do not start a tracking or other camera mode while a scan is active.

The caller supplies `<CLAW_PATH_DATA>/rehab/scans`. A successful scan is committed as `<scan_id>/scan.json` plus `frames/frame_NNNNNN/{image.rgb565,tof.json,frame.json}`. Work is written under `<scan_id>.incomplete`; each leaf is first written as `.tmp`, flushed, fsynced, closed, and renamed. A failed or aborted scan retains `.incomplete`, never becomes a final scan, and is ignored on later starts.

For each frame the worker reads ToF `before`, acquires and validates one 640×360 RGB565 little-endian Camera lease, copies exactly 460800 bytes to its single reusable PSRAM buffer, releases the lease, then waits up to 100 ms for a strictly newer ToF `after`. It selects the smaller absolute timestamp delta, preferring `after` on equality. JSON `sequence` is scan-local and begins at zero; timestamps are provider monotonic microseconds, while `created_at` is RFC3339 UTC wall time.

After manually copying a final scan directory to the PC, validate it with:

```powershell
$env:PYTHONPATH='C:\ec\_deltar_p4_pipeline\src'
C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe -m deltar_p4_pipeline.cli scan-validate --scan-dir '<copied_scan_dir>' --profile-bundle 'C:\ec\_deltar_p4_pipeline\profiles'
```

Codex did not run firmware build, flash, monitor, or hardware tests for this implementation.
