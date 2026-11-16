# Stage 5 Scene Contract

`deltar_stage5_scene.v1` fixes a 640×360 pinhole camera, distortion, `T_ground_from_camera`, `T_camera_from_tof`, objects and ToF profile. Supported primitives are floor/background planes, axis-aligned boxes, fronto-parallel billboards and vertically bounded slanted planes. A z-buffer produces RGB, axial depth and instance masks from one camera model.

Each truth object records visible mask, projected full extent, visible and physical top/bottom, radial surface distance, axial distance, visible and physical height, occlusion ratio, border contact and bbox. Visible-surface height is never silently relabeled as full physical height. GT arrays live at the scene parent; direct/letterbox inference folders contain only RGB565, ToF, profile and non-GT metadata.

The formal family contains F1–F12 plus 20/40/60-percent-style occlusion variants and 8/16/32-pixel-scale targets. The exact realized projection sizes and occlusion ratios are retained in `ground_truth.json` rather than asserted from requested labels.
