# Stage 4 Output Contract

Schema: `deltar_stage4_geometry.v1.1`.

The envelope contains status, calibration identity/status, explicit depth semantics/status, `absolute_geometry_valid`, objects, and warnings. Status is `success`, `partial`, or `rejected`; no object success means no envelope success.

Objects record logical ID/class, selected source, both canvas results/agreement, selected geometry, fallback decisions, rejection, and provenance. Geometry includes all four distances; top, bottom clearance, and height; count; estimator comparison; explainable quality inputs; warnings; and experimental ranking only.

Every object has a stable `canvas_selection_status`: `agreed`, `disagreed`, `direct_only`, `letterbox_only`, or `both_rejected`. `disagreed` always has `selected_canvas=null`, `selected_geometry=null`, `automatic_geometry_valid=false`, and `E_GEOMETRY_CANVAS_DISAGREEMENT`. A single successful canvas may retain a provisional candidate, but must set `single_canvas_only=true` and `automatic_geometry_valid=false`. Even an agreed selection sets `selection_is_provisional=true` until Stage 5 measured validation freezes the canvas.

`distance_m` serializes its `robust_3d_centroid_distance` definition. `bottom_height_m` is Ground-frame floor clearance. Stage 3 scalar surface depth is not a final 3D distance.

Quality inputs include calibration/semantics status, source, mask support, valid/retained ratios, count, MAD/IQR, multimodality, endpoint support, quantile stability, canvas agreement, border contact, and fallback. There is no `geometry_confidence` before Stage 5 calibration.

`absolute_geometry_valid` is computed in one fail-closed predicate. It is true only for measured calibration, `PROVEN` depth semantics, successful selected geometry, an agreed canvas pair, `automatic_geometry_valid=true`, and no critical geometry rejection. Non-measured or semantics-unproven profiles may produce synthetic/internal values, but always set it false and warn. Failures use stable `E_*` codes and never write fake successful geometry.
