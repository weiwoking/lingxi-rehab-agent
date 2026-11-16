# Calibration Profile Contract

Schema: `deltar_geometry_calibration.v1`.

Required fields are profile identity, `calibration_status`, source image size, camera model, explicit depth semantics/status and metre scale, `T_ground_from_camera`, `T_camera_from_tof`, mechanical hints, and ground plane. Status is `synthetic`, `unverified`, or `measured`; only `measured` permits `absolute_geometry_valid=true`.

Camera models are `pinhole` and `opencv_brown`. `fx/fy` are positive pixel focal lengths. Brown coefficients follow OpenCV order `(k1,k2,p1,p2[,k3,k4,k5,k6,s1,s2,s3,s4,tauX,tauY])`; zero distortion is pinhole-equivalent.

Depth is explicitly `axial_z` (Camera optical-axis Z) or `radial_range` (Euclidean ray distance), with status `PROVEN`, `PLAUSIBLE_BUT_UNPROVEN`, or `UNRESOLVED`. Unresolved semantics may be audited but cannot silently choose reconstruction.

Transforms are homogeneous 4x4 column-vector matrices named `target_from_source`. Rotations must be orthonormal with determinant +1 and last row `[0,0,0,1]`.

The user-frozen mechanical facts are non-numeric hints: Camera/ToF coplanar; Camera on the ToF long-axis extension; translated optical centres; physically rotated ToF; PC-only compensation; P4 uploads raw Camera/ToF/timestamp/pair delta. Hints never replace measured extrinsics.

`T_camera_from_tof` belongs to upstream ToF-zone/fr projection. Stage 4 consumes Camera-aligned dense depth, so it must not be applied again during pixel backprojection.
