# Rollback Plan

No rollback was needed in this task because no promotion occurred. This is the required rehearsal plan for a future promotion.

## Known-good anchors

| Anchor | Path / authority | SHA-256 |
|---|---|---|
| Old formal Stage7A | `C:\calibration\result_20260719_172703\calibration_profile.v2.json` | `5c7ebcd9a86c4e5c7e9a653667c309bf5a7b3f1e3d64208702e95d0dbf5a8cad` |
| Old formal Stage7B | `C:\calibration\stage7b_result_unitfix_20260724_012041\camera_tof_extrinsics.v1.json` | `dbcb5792338f50cca953c365e436e17d9f044b2f47bcca716e697308dda7f8b5` |
| Old formal Stage7C (inventory only; stale after remount) | `C:\calibration\stage7c_world_result_robustfix_20260724_044035\camera_world_pose.v1.json` | `5c730171f850ff3e05ab777eecca9849714aa20376c6d1f001da2b378ad24619` |
| Current synthetic PC calibration profile | `C:\ec\_deltar_p4_pipeline\profiles\calibration_profile.example.json` | `4933fa388879118f4f7b47eeec1a9dd04979aff4cfe86e4f049ba9a07debe89d` |
| Current PC identity orientation profile | `C:\ec\_deltar_p4_pipeline\profiles\zone_orientation_profile.json` | `2460a76fc1de7497a631afc073f3783bbae6a139f5773d2eb4a5e2d536133873` |
| Current uniform-grid generator source | `C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\tof_adapter.py` | `2db8874504d006fa5e9ee3f5724ea1b11ad8cfe5162dc4c1912d66de3f06e9af` |

The old Stage7C is a forensic anchor only. Rolling back Stage2 must not reactivate it as a valid post-remount world pose.

## One-switch rollback

1. Stop accepting new scans at the service boundary; preserve in-flight manifests.
2. Set the versioned runtime binding back to the recorded pre-promotion binding and turn all Stage2 bundle flags off.
3. Restart the service through the normal operational mechanism and verify reported bundle/profile hashes before accepting scans.
4. Run the legacy golden replay and one hardware smoke scan; compare raster, raw 64-zone ordering, DELTAR shapes and Geometry invariants.
5. For Git-managed changes, revert the promotion commit with a reviewed `git revert`; do not reset a dirty worktree. For immutable calibration artifacts, change only the binding pointer—do not delete either version.
6. Quarantine outputs produced under the rejected binding by manifest hash. Never merge them into formal Geometry evidence.

## Trigger conditions

Rollback on hash/schema mismatch, raw-zone permutation mismatch, NaN/non-positive `fr` extents, DELTAR shape/device failure, unexplained spatial/zone bias, cm-level thresholds exceeded, or any provenance manifest reporting the wrong bundle.
