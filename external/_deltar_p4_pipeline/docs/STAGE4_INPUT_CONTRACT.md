# Stage 4 Input Contract

Schema: `deltar_stage4_object_input.v1`.

Stage 4 consumes the method frozen by `outputs_stage3_1/frozen_method_selection.json`. It must not independently choose a region/statistic or fall back to the deprecated Stage 3 object priority.

```json
{
  "schema_version": "deltar_stage4_object_input.v1",
  "sample_id": "official_000",
  "object_id": "official_000:segmentation:000",
  "class_id": 56,
  "class_name": "chair",
  "class_confidence": 0.9,
  "object_source": "segmentation",
  "bbox_xyxy": [0.0, 0.0, 1.0, 1.0],
  "mask_key": "instance_000",
  "canvas_strategy": "direct_640x360",
  "region_method": "segment_eroded_3px",
  "statistic_method": "mad_median",
  "surface_depth_m": 1.0,
  "pixel_count": 1,
  "valid_pixel_count": 1,
  "valid_ratio": 1.0,
  "mad_m": 0.0,
  "iqr_m": 0.0,
  "dominant_peak_support": 1.0,
  "multimodal": false,
  "direct_letterbox_delta_m": 0.0,
  "warnings": [],
  "rejection_reason": null,
  "provenance": {}
}
```

Selection order is: valid segmentation primary; otherwise a matched detection fallback if available; otherwise an unmatched detection-native fallback; otherwise reject. A fallback must keep its `object_source=detection_fallback`, confidence, bbox, match provenance, and rejection history. It may not borrow a segmentation mask.

`provenance` must identify the `[360,640]` source-aligned depth map, canvas strategy, model hashes/tasks, frozen selection schema, and mask or region reference. A mask/region reference is mandatory because scalar surface depth alone is insufficient for object height.

Rejected records keep identity, source, warnings, and a non-null rejection reason; they do not contain a fabricated surface depth.

This contract does not authorize computing full 3D geometry from a scalar depth. Stage 4 must add calibrated intrinsics/extrinsics, source pixels, 3D point validity, camera-to-ground policy, and real-device validation before distance, physical height, or height-above-ground can be accepted.
