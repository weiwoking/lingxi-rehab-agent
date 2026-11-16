# Stage2B tool architecture

`workspace_manifest.json` is the only pose registry authority. Its `poses` object is append-only in this tool and has no maximum size. Each record binds a safe opaque pose ID to one validated 11-frame formal scan; pose text is not geometry.

`inspect` produces `observations/observations.stage2b.json`: all 11 repeated observations remain grouped under one scan, with full ToF raw fields and frame timing. Camera plane pose is derived independently per frame. Weak detections remain diagnostic records.

`coverage` derives distance and plane angles from PnP plane geometry. Zone coverage stays in raw row-major indexing until a D4 candidate is selected.

`solve` compares:

- Model A: historical-style nominal 45° regular center rays, as a baseline only;
- Model B: fitted `fx_tof/fy_tof/cx_tof/cy_tof` plus `T_camera_from_tof`;
- Model C: Model B plus small regularized/smoothed per-zone tangent-ray correction, selected only by scan-heldout gain.

Every D4/semantics/model A/B candidate receives train and scan-level heldout metrics. A weak co-orientation prior makes gauge ranking deterministic, while ambiguity uses the data-only margin. Hence a ranking winner may still correctly be `AMBIGUOUS`.

`validate` keeps complete scans out of training for heldout and LOPO. `stability-diagnostics` chooses a fraction of the actual registry N; no dataset size is encoded in the solver.

`advanced-diagnostics` treats physical D4 as a gauge question: all comparisons are keyed by raw zone index, and the observable authority is the raw-index-to-Camera-ray mapping. It reports core reference-plane accuracy separately from all-broad robustness, without changing broad/strict runtime policy or blacklisting any zone.

`export-candidate` writes only under this workspace. There is no promotion or runtime mutation path.
