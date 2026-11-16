# Partial Object Contract

Schema: `deltar_partial_object_visibility.v1`.

## Visibility status

`complete` is permitted only when the mask and bbox touch no image border, top and bottom have stable mask support, no obvious occlusion or large truncation exists, object depth is continuous, and Stage 4 has sufficient top and bottom point support. Any border contact, missing top/bottom, obvious occlusion, incomplete mask, Camera-FoV clipping, or partial ToF coverage makes the object `partial`. If neither complete nor partial can be proved, use `unknown`.

The official demo has no manual complete/partial labels. Border contact is therefore a risk proxy, not visibility ground truth.

## Output rules

- `class_name`: allowed when the YOLO class and region pass, regardless of complete extent.
- `distance_m`: allowed for partial objects only when depth support, stability, canvas and rejection gates pass. Set `distance_semantics=visible_surface_distance`.
- `visible_height_m`: allowed when the visible mask and Ground transform are valid. It is never synonymous with physical height.
- `physical_height_m`: allowed only for `complete`, `top_visible=true`, `bottom_visible=true`, `full_extent_visible=true`, valid Ground transform and measured calibration. Otherwise it is `null` and `physical_height_valid=false`. Category priors must not fill it.
- `bottom_height_m`: allowed only when the bottom is visible and unoccluded/untruncated, bottom point support is sufficient, Ground transform is valid, and calibration is measured. Otherwise it is `null`.
- `placement_level`: may be `ground|low|tabletop|elevated|unknown`; numerical thresholds belong to a Stage 6 profile.

Reject the requested measurement when its required depth/canvas/class/calibration gate fails. Use warnings for partial/unknown extent, visible-surface-only distance, visible-height-only results, low target fill, multimodal depth, extended range, corner zone, ambient light, or low reflectance.
