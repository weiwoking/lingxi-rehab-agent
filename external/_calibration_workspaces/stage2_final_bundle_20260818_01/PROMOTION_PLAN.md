# Formal-Chain Promotion Plan — Not Executed

Status: planning only. `promotion_executed=false`; `stage3_started=false`.

## Release rule

Install new immutable, hash-addressed artifacts beside the old chain. Never overwrite Stage7A/B/C in place. Use one versioned binding/config switch for activation and one switch back for rollback. Stage2 Camera/ToF activation and Stage3A world activation are separate decisions.

## P0 — Add bundle contract and dark plumbing

1. Add a JSON schema such as `schemas/stage2_final_calibration_bundle.v1.schema.json` and a strict loader such as `src/deltar_p4_pipeline/stage2_bundle.py`.
2. Add bundle path, expected SHA-256, and independent flags for measured camera intrinsics, raw-zone mapping, and dynamic `fr` to service configuration (`scan_service.py`, `scan_service_worker.py`, `scripts/run_scan_service.py`, plus frozen runtime resource packaging).
3. Keep every flag off. Reject hash/schema/status mismatches, any non-64 zone map, any non-`axial_z` semantics, and any attempt to treat unresolved physical D4 as resolved.
4. Tests: bundle round trip, exact source hashes, K/D numeric parity, R orthonormality/determinant, raw-index bijection, malformed input failures.

## P1 — Promote Stage2A camera fields behind a flag

1. Generate a runtime calibration view from this bundle rather than editing `profiles/calibration_profile.example.json` in place.
2. Plumb measured 640×360 Brown-5 K/D to consumers that actually project points. Verify whether each consumer supports distortion; fail closed where it currently assumes zero distortion.
3. Refactor old Stage7D/7E literal Stage7A hashes/IDs into a versioned binding manifest. Do not touch world pose yet.
4. Acceptance: image/depth raster identity, reprojection fixtures, old-vs-new replay, service restart persistence, rollback switch verified.

## P2 — Promote Stage2B mapping/semantics/transform behind separate flags

1. Preserve firmware/capture raw indices 0..63. Bind `raw_zone_index_to_camera_frame_ray`; do not apply the old `rotate_90_cw` label.
2. Enforce `axial_z`. Add the Stage2B `p_camera = R*p_tof+t` adapter with millimetre units and column vectors.
3. Firmware profile IDs remain unchanged until PC acceptance; when later updated, change manifest IDs/schema only in a compatibility release with old-scan replay.
4. Acceptance: raw-zone marker fixture, identity permutation, per-zone center projection at 400/600/1000 mm, candidate-hash provenance in output manifests.

## P3 — Replace uniform-grid `fr` with feature-gated dynamic projection

1. Add a new `ToFAdapter` strategy. For each raw zone with valid axial depth, scale its four model-derived ToF boundary rays by `z/ray_z`, transform with Stage2B R/t, distort/project with Stage2A K/D, then emit `[min_y,min_x,max_y,max_x]` in source-camera pixels and apply the existing canvas transform.
2. Define one explicit invalid-zone policy and test it. Recommended safe first implementation: use a frame-robust valid axial depth only for invalid zones, emit an invalid-zone mask, and never feed zero/NaN as geometry. Temporal fallback, if adopted, needs reset/age semantics.
3. The 400/600/1000 mm tables are parity fixtures and diagnostics. A static 600 mm table is not acceptable as the formal 400–1000 mm runtime solution.
4. Existing DELTAR accepts per-call `fr`; test `[1,64,4]`, float32, device placement, direct/letterbox canvases, out-of-canvas padding, and variable-depth frames.
5. Ship plumbing with the feature flag off. Enable only after candidate replay, Stage2C replay, synthetic edge cases, and service end-to-end tests pass.

## P4 — Formal binding and downstream regression

1. After P0–P3 acceptance, install a new immutable formal Stage2 binding and update service defaults/profile IDs in one reviewed promotion commit. Record old and new hashes in every scan manifest.
2. Do not bind any camera-world transform from the old Stage7C. Mechanically fix the assembly, wait for adhesive cure, then run Stage3A as a separate authorized task.
3. After Stage3A, generate a new versioned world binding, rebuild Stage7D/7E frozen profiles, and run Geometry and Competition Shelf Stabilizer regressions. Do not tune the stabilizer to mask calibration changes.
4. Acceptance includes cm-level decision thresholds chosen by the owner, spatial/zone bias review, all Stage2C poses, plane/height invariants, support/object/top-height invariants, and rollback rehearsal.

## Decision gates

- Stage2C label `WORSE_THAN_STAGE2B_REFERENCE` is diagnostic, not a hard gate. Owner must explicitly accept or require more data for the observed Stage2C median/P95/RMS `4.716/12.778/6.702` mm.
- Mechanical fixation/glue cure is mandatory before Stage3A.
- No world/absolute Geometry promotion occurs until a fresh Stage3A result is available.
