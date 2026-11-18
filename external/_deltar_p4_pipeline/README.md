# DELTAR PC Stage 2 fake-P4 adapter

Replace `<PROJECT_ROOT>` in the commands with the current repository root.

## PC-local all-object geometry and standalone UI

The current 11-frame chain writes `stage8b_all_object_geometry.v1.json` after
logical-object grouping and same-frame axial/world depth are available. This
artifact is PC-local: it is independent of selection and is never added to the
P4 package. Mask-backed objects use a one-pixel erosion with raw-mask fallback;
maskless objects use a deterministic axial-depth cluster with center/full robust
bbox fallbacks. Geometry uses camera axial-Z median and world-Z P5/P95.

After the selected-object contract and packet are published, build the editable
UI snapshot and render it without starting the service:

```powershell
python tools\build_pc_scene_ui_input.py --result-dir C:\path\to\result
python tools\render_pc_scene_ui.py --input C:\path\to\result\pc_scene_ui_input.v1.json --output C:\path\to\scene.html
```

You may edit object text/numbers, `selected_for_p4`, geometry status/failure,
diagnostics, and image paths in the UI input, then run only the second command.
Relative image paths resolve against the UI input directory; absolute Windows
paths, `file:///`, `data:`, HTTP, and HTTPS references are accepted. Re-rendering
does not call YOLO, DELTAR, geometry, selection, packet generation, or a service.
The minimal editable fixture is `examples\pc_scene_ui_input.example.json`.

## Stage 7A Camera intrinsics tooling

Stage 7A adds PC-only SC2336 final-output orientation checking, reproducible A4 ChArUco assets, deterministic fit/held-out OpenCV Brown calibration, strict v2 Camera profile generation, schemas and fail-closed quality gates. The frozen production contract is 640×360 landscape, scene upright, software rotation 0. No real SC2336 intrinsics are bundled. See `USER_CAPTURE_GUIDE.md` and `docs/CAMERA_INTRINSICS_OPERATIONS.md`.

Stage 6.2 is a schema erratum only. It freezes the existing `deltar_scan_input.v1`, `deltar_scan_frame.v1`, and `rehab_tof_raw.v1` inputs as closed JSON Schema Draft 2020-12 documents. P4 must emit exactly those fields; `created_at` includes `Z` or an explicit offset, sequence is contiguous from zero within a scan, and `pair_delta_us` is the non-negative absolute timestamp difference. Cross-file rules remain runtime checks. No input version, output v1.1 contract, algorithm, model, directory, or CLI name changed.

Stage 6.1 is the final PC contract release: `scan-run` writes strict frame/result/job v1.1, while `scan-inspect` keeps historical scan-result v1 explicitly legacy. See `docs/PC_STAGE_FINAL_STATUS.md` and the v1.1 contract documents.

## Stage 6 batch pipeline and interface freeze

Stage 6.1 is the final PC-side packaging layer. It validates a frozen `deltar_scan_input.v1` directory, processes frames in sequence through DELTAR direct/letterbox, YOLO detect/segment, frozen Stage 3.1 statistics and the formal Stage 4.1 object-group core, then conservatively groups repeated observations and atomically writes frame/result/job `v1.1`. It does not implement P4 capture, networking, SLAM, reconstruction, a service, resume, or a persistent queue.

Use the frozen Python and `PYTHONPATH=src`:

```powershell
$python = '<PROJECT_ROOT>\external\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$env:PYTHONPATH = '<PROJECT_ROOT>\external\_deltar_p4_pipeline\src'
& $python -m deltar_p4_pipeline.cli profile-validate --profile-bundle profiles
& $python -m deltar_p4_pipeline.cli scan-validate --scan-dir C:\data\scan_0001 --profile-bundle profiles
& $python -m deltar_p4_pipeline.cli scan-run --scan-dir C:\data\scan_0001 --output-dir C:\data\scan_0001_result --profile-bundle profiles
& $python -m deltar_p4_pipeline.cli scan-inspect --result C:\data\scan_0001_result\result.json
```

See `docs/SCAN_INPUT_CONTRACT.md`, `docs/PROFILE_BUNDLE_CONTRACT.md`, `docs/SCAN_RESULT_CONTRACT.md`, `docs/MULTIFRAME_AGGREGATION.md`, `docs/STAGE6_CLI_GUIDE.md`, `docs/P4_FUTURE_UPLOAD_CONTRACT.md`, and `docs/PC_STAGE_FINAL_STATUS.md`.

## Stage 5.2B scan and partial-object freeze

Stage 5.2B freezes the VL53L5CX 8x8/15 Hz scan strategy, `deltar_partial_object_visibility.v1`, the seven launch-core classes, and the PC Stage 6 entry contract. The default scan distance is 1.2 m; use object-specific ranges and fail closed for physical/bottom height. Run the five `stage5_2b` scripts with the frozen Python and `PYTHONPATH=src`. Authoritative outputs are under `outputs_stage5_2b/`; see `docs/SCAN_DISTANCE_GUIDE.md`, `docs/PARTIAL_OBJECT_CONTRACT.md`, `docs/CORE_CLASS_CONTRACT.md`, and `docs/STAGE6_ENTRY_CONTRACT.md`.

This repository-external PoC reads a frozen 640×360 RGB565 little-endian image, raw 8×8 ToF facts, and an explicit strategy profile. It validates the contract, constructs the official DELTAR tensors without GT depth, invokes the unmodified official model core, and writes numeric/debug artifacts. It does **not** establish real P4 accuracy.

## Stage 4 geometry

Stage 4 adds source-pixel backprojection, explicit Camera/Ground frames, robust object point clouds, four distances, height/floor clearance, dual-canvas agreement, explainable rejection inputs, analytic scenes, official-sample internal consistency, and sensitivity analysis. Both `axial_z` and `radial_range` are implemented; current evidence is `PLAUSIBLE_BUT_UNPROVEN / axial_z`, never a silent default.

Stage 4.1 hardens the output contract to `deltar_stage4_geometry.v1.1`: canvas disagreement selects nothing, single-canvas results are explicit and never automatically valid, absolute validity requires every measured/proven/agreed prerequisite, and raw depth is converted to metres before every gate/statistic/cluster. Run `scripts/run_stage4_1_hardening.py` to regenerate `outputs_stage4_1/hardening_summary.json`.

Run `scripts/build_stage4_inputs.py`, `audit_depth_semantics.py`, `run_stage4_synthetic_matrix.py`, `run_stage4_official_matrix.py`, and `run_stage4_sensitivity.py` with the frozen Python and `PYTHONPATH=src`. Stage 4 PASS is not a real-P4 absolute-accuracy claim. See the six `docs/STAGE4_*`, calibration/coordinate, and Stage 5 plan documents.

## Stage 5 synthetic/public validation

Stage 5 adds analytic 3D scenes, official-demo appearance billboards, a ToF-viewpoint 8x8 approximate sensor simulator, L0/L1/L2/L3 evaluation, a faithful repository-external old-chain baseline, direct/letterbox comparison, and A0-A8 ablation. Run `audit_stage5_datasets.py`, both Stage 5 builders, `run_stage5_matrix.py`, and `run_stage5_ablation.py` in that order.

The measured verdict is **PARTIAL**: all engineering paths ran and no GT entered model inference, but official DELTAR synthetic-domain accuracy, full-chain coverage, direct/letterbox stability, and paired new-over-old value did not satisfy the frozen Stage 5 gates. Stage 6 packaging remains blocked. See `docs/STAGE5_RESULTS.md` and `outputs_stage5/stage5_acceptance.json`.

## Frozen runtime and assets

- Python: `<PROJECT_ROOT>\external\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe`
- Official derived worktree: `<PROJECT_ROOT>\external\_deltar_formal\work\deltar_f3c5175_win_cu128`
- Checkpoint: `<PROJECT_ROOT>\external\_deltar_formal\assets\nyu.pt`
- Set `PYTHONPATH=<PROJECT_ROOT>\external\_deltar_p4_pipeline\src`; no system install is required.

The CLI checks the checkpoint SHA-256 before inference and imports the official `Deltar`, checkpoint loader, hypothesis sampler, and `patch_info_from_rect_data` from that worktree. No upstream source or weight is copied here.

## Stage 5.2A official old-vs-new benchmark

Stage 5.2A evaluates all 287 official Zhejiang University demo HDF5 samples at their native 640x480 size. It matches YOLO detect and segment predictions at same-class bbox IoU >= 0.5, defines the object surface reference as official GT dense depth inside the eroded predicted segment, adapts the old bbox+8x8 median/MAD chain to the official FR identity contract, and runs the unmodified official DELTAR checkpoint directly from official RGB/hist_data/FR/mask inputs. Outputs are under `outputs_stage5_2a/`; the measured verdict is **PARTIAL**, so Stage 6 remains blocked and no hybrid chain was implemented.

## Commands

From PowerShell:

```powershell
$python = '<PROJECT_ROOT>\external\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$env:PYTHONPATH = '<PROJECT_ROOT>\external\_deltar_p4_pipeline\src'
& $python scripts\build_official_reverse_samples.py
& $python scripts\build_synthetic_samples.py
& $python -m deltar_p4_pipeline.cli run --sample samples\official_reverse\official_000_letterbox --output-root outputs
& $python scripts\run_stage2_matrix.py
$env:PYTHONPATH = '<PROJECT_ROOT>\external\_deltar_p4_pipeline\tests\pytest_support;<PROJECT_ROOT>\external\_deltar_p4_pipeline\src'
& $python -m pytest -q
$env:PYTHONPATH = '<PROJECT_ROOT>\external\_deltar_p4_pipeline\src'
& $python -m unittest -v tests.unittest_stage2 tests.unittest_rejections tests.unittest_hardening tests.unittest_integration
```

`pytest` is the primary entry point (50 tests after Stage 2.1); the 50-test `unittest` command is the compatibility entry point. `tests\pytest_support\sitecustomize.py` appends the existing system pytest package after the frozen DELTAR environment's packages. It installs nothing and keeps PyTorch/NumPy/Pillow on the formal Python 3.11/CUDA runtime.

## Stage 3 object depth

Stage 3 adds a repository-external Ultralytics worker, source-aligned detection/instance masks, greedy class+IoU object matching, seven region families, five scalar estimators, official-demo GT evaluation, and a 15-sample direct/letterbox matrix. The application layer is based on the official Zhejiang University DELTAR; it does not modify or claim to improve the DELTAR model.

Run the frozen matrix with the formal DELTAR Python and `PYTHONPATH=src`:

```powershell
python -m deltar_p4_pipeline.cli stage3-matrix
```

The worker subprocess uses `<PROJECT_ROOT>\external\_conda_envs\vision_interpreter\python.exe`; Ultralytics is not installed into the DELTAR environment. See `docs/STAGE3_OBJECT_DEPTH_CONTRACT.md` and `outputs_stage3/stage3_matrix.json`.

## Stage 3.1 method and contract hardening

Stage 3.1 evaluates all 36 segmentation-native and all 46 detection-native objects independently; 21 matched pairs are used only for fair comparison. It uses same-region/same-statistic GT references, valid-aware bilinear/nearest/area GT resizing, fail-closed YOLO hash/task checks, and a machine-readable frozen method selection.

```powershell
python -m deltar_p4_pipeline.cli stage3-1-matrix
```

Authoritative outputs are under `outputs_stage3_1/`. Stage 4 consumers must use `frozen_method_selection.json` and `docs/STAGE4_INPUT_CONTRACT.md`, not the deprecated Stage 3 object-level priority order.

Sample generation is deterministic. The official reverse builder reads three fixed HDF5 entries from the existing official demo. The synthetic builder creates the eight contract/exception scenarios named in the Stage 2 task.

## Outputs

Each `outputs/<sample_id>/<profile_id>/` contains decoded/canvas/valid-region/fr-overlay PNGs, meter `float32` NPY depth, 16-bit millimeter PNG depth, visual depth maps, inverse-mapped 640×360 depth, `result.json`, and `validation_report.json`. Rejected inputs get a stable error code and no fabricated depth files.

## Boundaries and unverified facts

The PoC does not implement FastAPI, P4 firmware, recorder/upload, YOLO/segmentation, object distance, height, ground geometry, or transactions. `distance_mm/1000` and `range_sigma_mm/1000`, the candidate status/count mask, zone orientation, and regular-grid fr are versioned hypotheses. Status 5/6/9, true Camera↔ToF calibration, synchronized real P4 samples, and real-world depth accuracy remain unverified.

Stage 3 retains **two formal candidates in parallel**: `direct_640x360` and `letterbox_640x480`. Center-crop is the backup; non-uniform resize is not recommended for freezing. No candidate is yet proven best on real P4 data.

See [INPUT_CONTRACT.md](docs/INPUT_CONTRACT.md), [PROFILE_CONTRACT.md](docs/PROFILE_CONTRACT.md), and [OUTPUT_CONTRACT.md](docs/OUTPUT_CONTRACT.md).
# PC Scan Service Phase A

Run the controlled PC HTTP service in the existing Vision Python environment:

```powershell
& '<PROJECT_ROOT>\external\_conda_envs\vision_interpreter\python.exe' `
  '<PROJECT_ROOT>\external\_deltar_p4_pipeline\scripts\run_scan_service.py' `
  --host 127.0.0.1 --port 8030
```

The service accepts a complete 1–5 frame scan through strict multipart fields, validates and runs it with the frozen Python 3.11 worker, exposes asynchronous status, and publishes the untouched `deltar_scan_result.v1.1` bytes. See `docs/SCAN_SERVICE_HTTP_CONTRACT.md`, `docs/SCAN_SERVICE_OPERATIONS.md`, and `docs/SCAN_SERVICE_SECURITY.md`. Phase A does not implement the P4 client, authentication, TLS, a database, a persistent queue, resume, or worker/model residency.
