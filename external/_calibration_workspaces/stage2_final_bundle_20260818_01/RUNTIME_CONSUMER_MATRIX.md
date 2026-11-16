# Runtime Consumer Matrix

This is a read-only inventory and a proposed change map. No row below has been promoted.

| Consumer / boundary | Current authority and behavior | Stage2 bundle field | Planned change | Risk / acceptance evidence |
|---|---|---|---|---|
| Firmware VL53L5CX provider | ULD results emitted as raw indices 0..63; no rotation | `camera_tof.raw_zone_camera_mapping`, identity raw-index authority | Preserve raw order; do not introduce D4 names | Golden raw-index fixture; scan byte parity |
| Firmware capture/transfer | Pairs RGB565 + 64-zone arrays; hard-coded profile IDs still say unverified/identity | Bundle identity + hashes | P4-only profile-ID/schema update after PC acceptance | Upload/capture contract and backward-compat tests |
| PC `zone_orientation_profile.json` | Identity reorder is applied to distance/sigma/mask and `fr` | `gauge.raw_index_for_canonical_index` | Make bundle identity authoritative; fail closed on non-identity mismatch | 64-zone permutation test; no double reorder |
| PC calibration profile | Synthetic pinhole/no distortion; unresolved semantics | Stage2A K/D + Stage2B `axial_z` and transform | New immutable bundle loader/validator; keep existing profile as rollback | Schema/hash validation; legacy-off/on replay |
| `ToFAdapter` | Generates uniform 8×8 source rectangles, then canvas transform | Per-zone rays, R/t, K/D, raw axial depth | Add feature-gated per-frame dynamic projection strategy | Reference 400/600/1000 parity ≤1e-6 px; invalid-zone policy tests |
| Canvas transform | `[sy,sx,ey,ex]`; direct or +60 y letterbox | Stage2B `fr` field order/units | Reuse existing transform after source-camera projection | Bounds/half-open/direct/letterbox tests |
| Official DELTAR forward | Receives `fr` as `[1,64,4]` per inference; computes patch info per call | Dynamic per-frame `fr` | No model architecture change expected; validate loader assumptions | Replay parity, batch/shape/dtype/device tests |
| Base point-cloud Geometry | Uses calibration profile camera model, depth semantics and ground transform | Stage2A K/D; Stage3A future world pose | Stage2 Camera fields can be plumbed first; world switch waits for Stage3A | Point-cloud projection/reprojection regression |
| Stage7D/7E contracts | Hash-pin old Stage7A/B/C paths and IDs | New Stage2A/B plus future Stage3A | Versioned binding manifest; do not overwrite old artifacts | Frozen profile rebuild and golden geometry diffs |
| Competition Shelf Stabilizer | Post-package support/object height adjustment | Downstream only | No calibration-promotion edit; regression target after new world chain | support/object/top-height invariants |

## Current data flow

`VL53L5CX raw[0..63] → capture/transfer unchanged → PC identity raw order → per-zone axial_z → project ToF boundary rays through Stage2B R/t and Stage2A K/D → source-camera fr[64,4] → existing canvas transform → DELTAR fr[1,64,4]`

The old Stage7B label `rotate_90_cw` belongs to an older canonical gauge artifact and is not the current firmware/PC raw-index operation. It must not be reintroduced as physical truth.
