# Future P4 Scan Transfer Client Contract

This document freezes the future `rehab_scan_transfer` client boundary; no P4 implementation is included in Phase A.

1. POST the complete scan once to `http://<pc>:8030/api/v1/scan/jobs` as `multipart/form-data` with an exact `Content-Length`. Do not use chunked transfer.
2. Send `scan_json` and, for each index `000000` through `frame_count-1`, the three fields `frame_<index>_frame_json`, `frame_<index>_tof_json`, and `frame_<index>_image_rgb565`.
3. Use only the leaf filenames `scan.json`, `frame.json`, `tof.json`, and `image.rgb565`. JSON is `application/json`; images are `application/octet-stream`.
4. Keep the encoded request at or below 3 MiB; frame count at 1–5; each JSON at or below 64 KiB; every RGB565 image at exactly 460,800 bytes.
5. Expect 202 plus a random service job ID. Poll the returned `status_url` every 5 seconds for up to at least 180 seconds.
6. Treat service `completed` as publishable even when `pipeline_result_status` is `rejected`. Treat service `failed` as terminal and do not fetch a result.
7. Fetch `result_url` only after completion. Require `deltar_scan_result.v1.1`, finite JSON, `result.scan_id == status.scan_id`, and `result.job_id == status.pipeline_job_id`. Never compare the pipeline job ID with the service job ID.
8. A repeated active upload of the same scan receives the existing job with 202; a completed repeat receives it with 200; 503 `E_SERVICE_BUSY` means another scan is active. Do not automatically retry validation/schema errors. A transport-level retry must resend the complete multipart request; resume and range upload are unsupported.

The full stable error set is documented in `SCAN_SERVICE_HTTP_CONTRACT.md`. Future P4 work must not weaken these limits or infer paths from filenames.
