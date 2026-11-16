# Tool architecture

Single entry point: `stage2a_camera_intrinsic.py`.

Commands: `inspect`, `register`, `status`, `rank`, `calibrate`, `validate`, `compare`, `run-all`, `synthetic-test`.

Registration strictly validates 11 frames, hashes source before/after, and keeps every Camera and ToF artifact through hardlink or copy. Ranking evaluates all 11 frames using marker/corner completeness, hull coverage, spatial cells, blur, exposure, clipping, border margin, and provisional PnP residual. Main fit uses best-1 per pose; the second strong frame is held out. Top-2 and a single recorded robust-filter pass are sensitivity diagnostics, not automatic final selection.

OpenCV 4.10 exposes the current path used here: `CharucoDetector`, `CharucoBoard.matchImagePoints`, and `cv2.calibrateCameraExtended`, with Brown k1/k2/p1/p2/k3 and subpixel refinement.

Formal paths are read-only. There is no promotion command.
