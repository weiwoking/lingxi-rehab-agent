# Stage 5 Evaluation Contract

L0 is GT mask + GT dense depth + synthetic calibration through frozen Stage 4 geometry. L1 replaces depth with official DELTAR while retaining an oracle region solely to isolate depth. L2 uses RGB565, simulated ToF, official DELTAR, YOLO segmentation or detection fallback, Stage 3.1 region handling and Stage 4.1 geometry. L3 uses the same RGB and ToF with the read-only `vision_interpreter` bbox-zone/MAD/median/old-geometry logic faithfully ported outside that repository.

Predictions match truth by class and explicit IoU threshold 0.3. Unmatched predictions are FP and unmatched truths are FN. Rejection, false-valid and all failed samples remain in denominators. GT never enters DELTAR or YOLO inference; L1's GT region is labeled `oracle_region_used=true`.

Metrics include perception coverage, depth MAE/median/RMSE/P90/P95, distance/height/bottom/visible-height absolute errors, distribution tails, rejection and false-valid. New-minus-old reports paired absolute and percentage changes. A0–A8 isolate geometry, DELTAR, YOLO, fallback, canvas, semantics and alignment.
