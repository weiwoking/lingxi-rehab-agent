# PC Scan Service HTTP Contract

Contract version: `deltar_scan_service.v1`. The service defaults to `127.0.0.1:8030` and has no authentication or TLS in Phase A.

## Endpoints

- `GET /api/v1/scan/health`
- `POST /api/v1/scan/jobs`
- `GET /api/v1/scan/jobs/{service_job_id}`
- `GET /api/v1/scan/jobs/{service_job_id}/result`

`POST` requires a positive `Content-Length`, rejects chunked transfer and non-multipart requests, and caps the complete encoded request at 3 MiB. A new accepted scan returns HTTP 202. The service permits one upload/worker job at a time. The same active scan returns the existing job with 202; a different scan returns 503 `E_SERVICE_BUSY`; a valid completed scan returns the existing job with 200.

## Multipart fields

Every part is a file part. The exact set is `scan_json` plus three fields for every contiguous zero-based frame:

```text
frame_<6 digits>_frame_json
frame_<6 digits>_tof_json
frame_<6 digits>_image_rgb565
```

The field name alone selects the server path. Filenames are checked but never used as paths: `scan.json`, `frame.json`, `tof.json`, and `image.rgb565`. JSON content type may be empty or `application/json`; images require `application/octet-stream`. Each JSON part is at most 65,536 bytes and strict finite UTF-8 JSON. Every image is exactly 460,800 bytes. `frame_count` is 1–5 and `capture_mode` is `single_frame` for one frame, otherwise `multi_frame`.

## Status and result

Service states are `accepted`, `running`, `completed`, and `failed`. Pipeline result states are `success`, `partial_success`, `rejected`, `failed`, or null. `success`, `partial_success`, and `rejected` all map to service `completed`.

`service_job_id` is a random `svc_<32 hex>` HTTP identifier. `pipeline_job_id` is the untouched `job_id` from the pipeline result. Clients compare `result.scan_id` to `status.scan_id` and `result.job_id` to `status.pipeline_job_id`.

Before completion or after failure, result access returns 409. On completion it returns the exact stored `result.json` bytes with `application/json`. Publication fails closed if the result exceeds 256 KiB, has over 100 objects, has the wrong v1.1 schemas, contains non-finite JSON, or has inconsistent IDs/status.

## Error mapping

| HTTP | Codes |
|---|---|
| 404 | `E_JOB_NOT_FOUND` |
| 409 | `E_SCAN_CONFLICT`, `E_JOB_NOT_COMPLETE`, `E_PIPELINE_FAILED`, `E_RESULT_*` |
| 411 | `E_REQUEST_CONTENT_LENGTH` |
| 413 | `E_UPLOAD_TOO_LARGE`, `E_PART_TOO_LARGE` |
| 415 | `E_REQUEST_CONTENT_TYPE` |
| 422 | `E_MULTIPART_*`, `E_PART_*`, `E_IMAGE_SIZE`, `E_SCAN_JSON`, `E_SCAN_VALIDATE` |
| 503 | `E_SERVICE_BUSY` |
| 500 | `E_SERVICE_INTERNAL` |

Errors use `deltar_scan_service_error.v1` and never contain tracebacks, internal commands, logs, or absolute paths.
