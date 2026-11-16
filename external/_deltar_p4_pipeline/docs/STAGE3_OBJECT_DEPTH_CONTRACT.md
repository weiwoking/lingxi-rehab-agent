# Stage 3 Object-Depth Contract

Stage 3 is an application layer based on the official Zhejiang University DELTAR. DELTAR architecture, checkpoint, RGB normalization, 16 hypotheses, `patch_info_from_rect_data`, mask gating, forward, clipping, and upscale postprocess remain unchanged.

All cross-model geometry is in half-open 640x360 source coordinates. YOLO always consumes source RGB. DELTAR returns `source_depth_m` and `source_valid_mask`, both `[360,640]`; letterbox inversion uses the Stage 2.1 recorded transform, bilinear depth, and nearest valid-mask resampling.

The YOLO document schema is `deltar_stage3_yolo.v1`. Detection instances have `mask_key=null`; segmentation keys map one-to-one to `[360,640] bool` arrays in NPZ. Bboxes are clipped; masks are nearest-resized, bounded to their bbox, and empty masks are rejected. The worker controls stderr, exit code, timeout, and batch output names.

Object matching requires equal `class_name` and greedy descending IoU >= 0.5. Unmatched instances remain explicit. No GT participates in inference, selection, matching, region construction, or estimator decisions.

Each valid `region + statistic` result reports pixel support, valid ratio, median, MAD, IQR, peak support, multimodality, interpretable confidence inputs, warnings, and rejection reason. A rejected candidate never receives a synthetic surface depth.

Stable Stage 4 input should consume: source image identity, class/id/confidence, source bbox/mask, canvas candidate, region/statistic names, `surface_depth_m`, support counts, multimodal/peak fields, warnings, and rejection reason. Stage 4 must still validate real P4 calibration, camera geometry, height, ground transform, and multi-frame behavior.

## Stage 3.1 schema upgrade

`deltar_stage3_1_object_depth.v1` supersedes the ambiguous Stage 3 object recommendation semantics without silently changing `deltar_stage3_object_depth.v1`. The old `recommended_candidate` priority order is deprecated. Stage 3.1 recommendations come only from `deltar_stage3_1_method_selection.v1`.

The output has three explicit sets: all segmentation-native objects, all detection-native objects, and matched pairs used only for comparison. Stable object IDs include sample, model source, and model instance ID. Unmatched objects are never inferred from output order and are never silently dropped.

YOLO worker requests use `deltar_stage3_1_yolo_worker_request.v1` and require `model_path`, `expected_sha256`, and `expected_task`. Hash is checked before model load; task is checked after load. Mismatch is a hard `E_YOLO_MODEL_HASH` or `E_YOLO_TASK` rejection.
