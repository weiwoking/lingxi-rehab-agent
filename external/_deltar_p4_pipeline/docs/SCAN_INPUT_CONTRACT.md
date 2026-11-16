# Stage 6 scan input contract

> Stage 6.2 schema erratum: the three v1 inputs are frozen by `schemas/deltar_scan_input.v1.schema.json`, `schemas/deltar_scan_frame.v1.schema.json`, and `schemas/rehab_tof_raw.v1.schema.json`. All objects are closed; P4 must not emit extension fields. `created_at` requires RFC3339 with `Z` or an explicit offset. The five profile IDs are exact Stage 6 values, and `notes` is null or at most 512 characters.

The formal range is 1..64 frames, with scan-local contiguous sequence values 0..63 starting from zero. This is not the provider-global ToF sequence. `pair_delta_us` is a non-negative absolute value. Runtime validation additionally enforces directory, file-size, cross-file timestamp, sequence, count, and mode relationships that a single-document JSON Schema cannot express. No business field or v1 version changed.

The frozen directory schema is `deltar_scan_input.v1`:

```text
scan_id/
  scan.json
  frames/
    frame_000000/
      frame.json
      image.rgb565
      tof.json
```

`scan_id` must equal the directory name. `frame_count` is 1–64 and must equal the exact number of frame directories. `capture_mode=single_frame` is legal only for one frame; otherwise use `multi_frame`. `device` requires non-empty `board`, `camera`, and `tof`. All five profile identifiers are mandatory and must match the selected bundle.

Each frame uses `deltar_scan_frame.v1`. The directory name equals `frame_id`; sequences are contiguous from zero. `pair_delta_us` equals `abs(camera_timestamp_us-tof_timestamp_us)`. The image contract is exactly 640×360 `RGB565_LE`, 1280-byte stride, and 460800 payload bytes. ToF uses `rehab_tof_raw.v1`; each of the four arrays has exactly 64 finite values and its sequence/timestamp equals `frame.json`.

Only relative leaf filenames are accepted. Absolute paths, separators, `.` and `..` are rejected before file access. JSON is UTF-8 strict JSON: NaN/Infinity, unknown schema versions, missing required fields, inconsistent counts/timestamps and oversized scans fail closed. Ground-truth files are not part of an inference scan.

Stable input codes are `E_SCAN_SCHEMA`, `E_SCAN_FRAME_COUNT`, `E_SCAN_DUPLICATE_FRAME`, `E_SCAN_PATH`, `E_FRAME_SCHEMA`, `E_FRAME_IMAGE_SIZE`, `E_FRAME_TOF_SCHEMA`, `E_FRAME_TIMESTAMP`, and `E_SCAN_NO_VALID_FRAMES`. `scan-validate` is strict. `scan-run` may quarantine a corrupt individual frame and continue only when at least one other frame satisfies the contract; this produces `partial_success` and a recorded frame rejection.
