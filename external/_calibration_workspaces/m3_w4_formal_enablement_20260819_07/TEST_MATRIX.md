# Test matrix

- M3/W4 unit tests: 7 passed
- ToF + Review ZIP + UI + parent boundary suite: 53 passed
- Dedicated runtime import boundary: 4 passed
- Six fresh production parity: 135 contexts and 229 objects, 0 mismatches
- Single-frame independence: 264 comparisons, 0 differences
- Six P4/UI/Review contract replays: 6 passed
- compileall / diff --check: passed

One broader non-Geometry scan-service test remains red: its health fixture rejects the existing absolute runtime paths emitted by current health diagnostics. This predates and does not overlap the four-file M3/W4 diff; the dedicated parent/heavy-import and service boundary gates pass.
