# Stage 5 Measured Calibration and Ruler Validation Plan

No user data is required immediately. Before real P4 absolute validation, request this traceable package.

## Camera at 640x360

- exact `fx,fy,cx,cy`, distortion model/ordered coefficients;
- original target images, target dimensions and units;
- report, per-view reprojection error, RMS, software/version;
- confirmation of raw versus rectified/cropped/resized images.

## Camera to ToF

- signed optical-centre offsets along Camera right/down/forward in millimetres;
- ToF in-plane rotation direction/angle and any yaw/pitch/roll with axes/signs;
- near-edge occlusion-direction experiment over several distances;
- measured `T_camera_from_tof` or reproducible joint-calibration captures/report;
- exact ToF mode/FoV/zone ordering/status policy and final mounting state.

Baseline/rotation require strict measurement because they alter upstream zone/fr footprints and parallax at discontinuities. They are not reapplied after Camera-aligned depth exists.

## Camera to Ground

- optical-centre floor height; signed pitch/yaw/roll;
- final enclosure/support and floor-plane definition;
- measured `T_ground_from_camera` or reproducible plane/board captures.

## Paired samples and ruler truth

Retain raw `image.rgb565`, `tof.json`, `profile.json`, timestamps, pair delta, firmware/config hashes, and immutable IDs. For each object record class, precise distance definition, physical height, bottom clearance, lighting, occlusion, pixel size, measurement method, repeats, and uncertainty. Cover centre/edges, near/far, small/large, floor/elevated, slanted, borders, and discontinuities.

Stage 4.1 canvas agreement remains provisional and `absolute_geometry_valid` remains false for all current official samples because their calibration is unverified and semantics are `PLAUSIBLE_BUT_UNPROVEN`. Freeze public `distance_m`, canvas, tolerances, and any future calibrated confidence only after bias/P90/P95 are measured by range, size, pose, source, and environment.
