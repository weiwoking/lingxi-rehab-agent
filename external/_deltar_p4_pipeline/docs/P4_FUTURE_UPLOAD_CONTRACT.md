# P4 Future Upload Contract

Before upload exists, P4 `rehab_scan_capture` must produce only the frozen Stage 6.2 input forms `deltar_scan_input.v1`, `deltar_scan_frame.v1`, and `rehab_tof_raw.v1`. It must serialize the checked schemas exactly, including closed objects, formal profile IDs, RFC3339 `created_at` with timezone, scan-local contiguous sequence, and non-negative absolute `pair_delta_us`. Runtime validation remains authoritative for cross-file relationships. Stage 6.2 changes no business field and no version.

This stage implements no P4 or upload code. A future independent P4 stage may consume only `deltar_scan_frame_result.v1.1`, `deltar_scan_result.v1.1`, and `deltar_pc_job_report.v1.1` after strict validation.

It must preserve logical object IDs, selected source/fallback state, all formal canvas states, absolute-valid flags, partial visibility semantics, model/profile hashes, warnings, and error codes. It must reject legacy v1 as an upload payload, absolute host paths, non-finite numbers, unknown critical fields, and incomplete nested objects.
