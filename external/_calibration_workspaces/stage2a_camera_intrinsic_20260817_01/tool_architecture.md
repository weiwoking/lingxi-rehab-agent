# Tool architecture

`stage2a_camera_intrinsic.py` is the single entry point.

- `inspect`: read-only source discovery and contract validation.
- `register`: strict 11-frame validation, all-artifact hardlink/copy, before/after source hashing, pose manifest.
- `status`: dynamic registered/selected/held-out/extra counts from manifest records; the legacy 20 count is informational only.
- `rank`: RGB565 decode, all-11-frame ChArUco detection, multi-factor quality ranking, contact sheets and coverage.
- `calibrate`: best-1-per-pose primary fit, second-frame held-out, top-2 sensitivity, one-pass recorded outlier diagnostic, Brown 5-parameter candidate.
- `validate`: candidate contract and canonical content hash.
- `compare`: old/candidate parameters, same-frame held-out PnP residuals, sample undistorted rays.
- `run-all`: end-to-end operation over every currently registered pose, with no maximum dataset size and the existing minimum usable-diversity gate.
- `synthetic-test`: generated-board detection plus known-K/dist multi-pose recovery.
- `stability-diagnostics`: read-only dense raw-pixel-to-ray/mapping comparisons over every current rank-1 selection, dynamic N-run LOPO, and deterministic fraction-based subsets (`ceil(N*fraction)`). It also reconstructs historical pre-edge22 and pre-supplement25 from explicit pose-ID allow-lists and writes only below `stability_diagnostics/`.

The installed OpenCV 4.10 build exposes no legacy `aruco.calibrateCameraCharuco*` wrapper. The implemented current path is `CharucoDetector` for observations, `CharucoBoard.matchImagePoints` for exact board correspondences, and `cv2.calibrateCameraExtended` for the Brown five-parameter solve.

All write paths resolve below the workspace. Formal scans and old Stage7A are opened read-only. Candidate promotion is intentionally absent.

The old Stage7A profile belongs to a different physical camera. `compare` remains available as historical information, but stability and capture guidance for the current camera use its dynamic current-N data. The explicit 22/25-pose subsets are before/after evidence only and never current pose-count gates.

The manifest `poses` object is the dataset registry. A pose participates only when its record has a validated `scan_id`; iteration preserves manifest registration order. `A01`–`C08` remain legacy/planned display labels only. Safe new IDs are registered dynamically and are not inferred as physical poses from their spelling.
