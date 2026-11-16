# Same-Pose Repeatability Audit

The eleven frames are evaluation-only repetitions. Every estimate was computed from one frame without previous/next-frame input.

| Scan | Before lower MAD | After lower MAD | Before lower span | After lower span | Correct peak rate |
|---|---:|---:|---:|---:|---:|
| G001 | 19.098 | 16.229 | 106.164 | 47.038 | 1.000 |
| G002 | 7.095 | 4.354 | 70.923 | 22.589 | 1.000 |
| SCAN_070604 | 30.491 | 22.777 | 396.038 | 325.266 | 0.455 |
| SCAN_085223 | 37.611 | 41.610 | 233.517 | 226.478 | 0.455 |
| SCAN_161718 | 15.023 | 6.391 | 82.180 | 32.178 | 0.909 |
| SCAN_165011 | 10.576 | 11.886 | 85.663 | 40.728 | 0.273 |

Span improved, but the required simultaneous MAD reduction and correct-peak availability did not. 070604 and 085223 expose a near-truth peak in only 5/11 frames; 165011 only 3/11.

`SAME_POSE_REPEATABILITY_AUDIT_PASS = false`
