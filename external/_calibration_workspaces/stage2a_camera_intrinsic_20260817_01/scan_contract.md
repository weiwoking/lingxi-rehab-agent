# Formal 11-frame scan contract (source-first finding)

- Runtime PC storage root: `C:\ec\_deltar_scan_service_data`.
- Complete uploaded inputs: `validated\scan_YYYYMMDD_HHMMSS_NNN`.
- Current environment scan count: exactly 11; `capture_mode=multi_frame`.
- Scan identity is `scan.json.scan_id`, equal to its directory name.
- Frames are contiguous `frames\frame_000000` through `frame_000010`.
- Every frame contains `frame.json`, `image.rgb565`, and `tof.json`; the validated input has no JPEG preview contract.
- Camera: 640×360, stride 1280, tightly packed 460800-byte `RGB565_LE`; PC expands R5/G6/B5 to RGB.
- `frame.json` records scan-local `sequence`, `camera_timestamp_us`, `tof_timestamp_us`, and non-negative `pair_delta_us = abs(camera_timestamp_us - tof_timestamp_us)`.
- `tof.json` uses `rehab_tof_raw.v1`; its sequence and timestamp must equal `frame.json`, with 8×8 distance/sigma/status/target-count arrays.
- Completeness means exact declared count, exact contiguous directory identities, all required files, exact image size/raster, and cross-file timestamp/sequence consistency.

Evidence came from the current runtime defaults and actual validated 11-frame artifacts, not a guessed historical path.
