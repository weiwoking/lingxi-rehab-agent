# Job Report Contract v1.1

`deltar_pc_job_report.v1.1` strictly records job/scan identity, status and exit code, timestamps, input/output schemas, processed and rejected frames, diagnostics, frozen provenance, timing, and the explicit absence of resume/persistent queue support.

Exit codes remain: 0 success, 2 input, 3 profile/model validation, 4 model inference, 5 frame processing, 6 aggregation, and 7 output/schema. Reports are runtime-validated before atomic write. Public fields never contain absolute local paths.
