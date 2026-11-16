# Stage2B example outputs

These are format examples, not real Stage2B measurements and not capture instructions frozen in schema.

```text
> status
registered_scans=12 usable_scans=11 frames=132 pairs=132 camera_plane_frames=116
broad_zone_observations=7012 strict_zone_observations=6388 distance_bins={'near_lt_550': 27, 'mid_550_850': 55, 'far_ge_850': 34}
orientation=AMBIGUOUS best=flip_horizontal confidence_gap_mm=0.42
pose_registry=dynamic no_maximum=true
```

```text
> coverage
registered_scans: 12
usable_scans: 11
frame_pair_count: 132
distance_histogram: near=27, mid=55, far=34
plane-angle: yaw/pitch/combined tilt derived from PnP plane normals
zone heatmaps: broad count, strict count, distance spread, angle spread
```

```text
> suggest-next
Recommended next capture:
- keep distance approximately unchanged
- keep height approximately unchanged
- move horizontally to the right
- maintain current pitch
- add a moderate board-plane yaw while keeping the component still during all 11 frames
Reason:
- right-side zones are underconstrained
- the leading D4 orientations remain too close
This is guidance only; it does not capture or register data.
```

The actual command uses measured coverage and may instead ask for a distance-only or vertical/pitch-only move.
