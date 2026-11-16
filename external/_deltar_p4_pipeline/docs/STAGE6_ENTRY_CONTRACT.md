# Stage 6 Entry Contract

Status: **AUTHORIZED** for PC-only packaging.

- The unmodified new DELTAR chain is the formal primary route. The old 8x8 chain is historical baseline only and must not appear in formal results.
- Default scan distance is 1.2 m. Use 0.8-1.2 m for small, 1.0-1.5 m for medium, 1.2-1.8 m for large, and conditional 2.0-2.5 m or multi-frame for a complete adult.
- Apply `deltar_partial_object_visibility.v1` and the validity rules in `PARTIAL_OBJECT_CONTRACT.md`.
- Core classes are `person`, `chair`, `dining table`, `cup`, `bottle`, `book`, and `tv`.
- Extended range, low target fill, bright ambient, low reflectance, corner zone, multimodal depth, and partial/unknown extent require warnings. Invalid depth/canvas/class/calibration gates require null/reject.
- Stage 6 is limited to PC batch processing, interface contracts, and result packaging.

Stage 6 does not authorize model changes, retraining, a hybrid chain, P4 work, real-device calibration, FastAPI, or build/flash/monitor. Long-tail errors are handled by warnings/rejection; physical height fails closed unless complete extent and measured calibration are proved.
