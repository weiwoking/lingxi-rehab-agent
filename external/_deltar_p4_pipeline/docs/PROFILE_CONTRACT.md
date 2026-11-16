# Profile contract

Every profile records `profile_id`, `sample_kind`, the frozen image declaration, `seed`, and these strategy sections.

## Canvas

| Strategy | Mapping | Status |
|---|---|---|
| `letterbox_640x480` | identity plus `cy += 60`; 60 px top/bottom padding | formal candidate |
| `resize_640x480` | `sx=1`, `sy=4/3`; focal/principal y scale | formal candidate, distortion warning |
| `scale_to_fill_center_crop_640x480` | integer 853×480 raster, `sx=853/640`, `sy=480/360`, horizontal center crop | backup candidate, FOV/raster warning |
| `direct_640x360` | identity | formal Stage 3 candidate |
| `direct_640x384_pad32` | 12 px top/bottom | probe only |

Each run emits the 3×3 source→canvas matrix, inverse, valid region/mask, transformed **test** intrinsics, and inverse depth mapping. Center-crop records the actual integer resize scales, so its x/y scales differ slightly rather than pretending both are the theoretical `4/3`. Test intrinsics are not a real calibration.

## ToF and zones

- Histogram: `distance_mm_to_m` and `range_sigma_mm_to_m` are `PLAUSIBLE_BUT_UNPROVEN` P4 candidates.
- Zone mapping: `identity`, horizontal/vertical/both flip, and `rotate_180` exist for contract experiments only. No real P4 orientation is frozen.
- Mask: `explicit_bool64` is a reference; `all_valid`/`all_invalid` are contract cases; `status_count_candidate_v1` rejects only count=0 or status=255 and emits a warning. Status 5/6/9 and sigma thresholds remain unfrozen.
- fr: `[sy,sx,ey,ex]`, half-open, positive extent. Out-of-canvas rectangles are legal because official data uses padding. `explicit_model_canvas` is a reference. `regular_grid_source_then_transform` is synthetic and is not equivalent to Camera↔ToF calibration.

The adapter uses the official deterministic 16 Normal quantiles and official `patch_info_from_rect_data`. Mask-false hypotheses are zero.

`direct_640x360` and `letterbox_640x480` remain parallel formal candidates for Stage 3. Center-crop is the backup. `resize_640x480` is not recommended for freezing because of its deliberate non-uniform stretch.
