# Stage 5 Results

The measured verdict is **PARTIAL**. Nineteen scenes and 27 truth objects ran; official DELTAR and both YOLO models were invoked, not mocked. L0 validates distance geometry strongly (median 0.00036 m, P95 0.00434 m) while also demonstrating why robust visible-height estimates must not be called full physical height.

DELTAR isolation is poor on these synthetic distributions: L1 distance median error is about 0.958 m and rejection is about 57.4%. L2 has four matched realistic-appearance objects (two canvases each) but only eight valid geometry rows; full-chain rejection is about 85.2%. L3 has one valid paired object. On that object the old chain is better in distance, height and bottom-height, so Stage 5 has no defensible new-chain numerical value.

No false-valid result was observed. The small analytic targets were rejected because YOLO did not detect geometric color patches; they were not reported as precise measurements. Direct and letterbox both ran. Alignment simulation completed 80 cases. Stage 6 is not authorized until realistic matched coverage and DELTAR-domain compatibility improve and the same frozen comparison is rerun.
