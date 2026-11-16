# Stage 4 Input Contract v1.1

Schema: `deltar_stage4_object_group_input.v1.1`.

Stage 4 consumes logical object groups, not flattened Stage 3.1 scalar-depth rows. Each group contains identity/class fields, `segmentation_primary`, `detection_fallback`, `match`, both `source_depth_refs`, and provenance.

Legal states are `matched`, `unmatched_segmentation`, and `unmatched_detection`. Matched groups keep segmentation as primary and the paired detection as a real fallback. Unmatched objects on either side are never silently dropped.

Both `direct_640x360` and `letterbox_640x480` candidates and their source-aligned dense-depth references are mandatory. Each depth and resolved mask is `[360,640]`; segmentation masks use an NPZ path plus key. Paths are pipeline-root relative.

Frozen methods are `segment_eroded_3px + mad_median` and `bbox_full + median`. Scalar surface depth is a filter centre and provenance only; per-pixel dense depth remains the geometry input.

`outputs_stage4/input_groups_manifest.json` is built from real Stage 3.1 results: 15 samples, 61 logical groups, 21 matched, 15 unmatched segmentation, and 25 unmatched detection. Validation fails closed with `E_STAGE4_INPUT_SCHEMA`, `E_STAGE4_DEPTH_SHAPE`, `E_STAGE4_MASK_SHAPE`, or `E_STAGE4_MASK_KEY`.
