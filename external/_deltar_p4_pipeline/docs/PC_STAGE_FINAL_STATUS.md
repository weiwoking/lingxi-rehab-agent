# PC Stage Final Status

Stage 6.2 is the formal input-schema erratum. The scan, frame, and raw-ToF v1 contracts are closed Draft 2020-12 schemas aligned with the Python runtime validator and fixtures. `created_at` requires an RFC3339 timezone; sequence is scan-local from zero; `pair_delta_us` is the non-negative absolute delta. No business field, directory, CLI, model, algorithm, or output v1.1 contract changed. When `outputs_stage6_2/stage6_2_acceptance.json` is PASS, the PC stage remains complete and P4 `rehab_scan_capture` implementation is allowed.

Stage 6.1 is PASS and closes the PC algorithm/batch/interface phase. The real chain is RGB565 + ToF → DELTAR direct/letterbox → YOLO detect/segment → frozen Stage 3.1 → formal Stage 4.1 object groups → conservative aggregation → strict v1.1 contracts.

Segmentation surface statistics and Stage 4.1 point cloud both use the byte-identical `segment_eroded_3px` mask. Absolute validity is decided only by `geometry_quality.compute_absolute_geometry_valid`: measured calibration, PROVEN depth semantics, successful agreed geometry, automatic validity, and no critical rejection are all required. Physical and bottom height add their visibility/support conditions.

C1–C18 and two-run real determinism pass. No P4, network upload, FastAPI, SLAM, 3D stitching, training, build, flash, monitor, or Git write was performed.
# Phase A PC Scan Service (2026-07-18)

The repository-external PC stage now includes `deltar_scan_service.v1`: bounded multipart upload, strict directory reconstruction, frozen Python 3.11 validation/processing, asynchronous service jobs, polling, restart fail-closed behavior, and byte-exact v1.1 result publication. The authentic five-frame HTTP integration completed with pipeline result `rejected`, which is correctly a service `completed` state. P4 transfer remains a separate future implementation governed by `P4_SCAN_TRANSFER_CLIENT_CONTRACT.md`.
## Stage 7A Camera intrinsics addendum

The PC perimeter now includes Stage 7A orientation, ChArUco, held-out calibration and calibration-profile-v2 tooling. This does not alter Stage 2–6 algorithm semantics, official DELTAR, YOLO, or P4. Real SC2336 calibration remains `waiting_user_capture`; Stage 7B is blocked until a real held-out quality gate passes.
