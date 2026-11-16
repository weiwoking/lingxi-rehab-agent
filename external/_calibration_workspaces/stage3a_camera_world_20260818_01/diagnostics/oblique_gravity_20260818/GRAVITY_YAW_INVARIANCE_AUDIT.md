# Gravity yaw-invariance audit

## Contract and sign

The printed ChArUco board uses `+Y_board` downward. The fixed vertical-board contract therefore gives

`+Y_board = -Z_world`.

OpenCV returns object-to-camera rotation `R_camera_from_board`, so the printed-down axis expressed in camera coordinates is its second column. World-up in camera coordinates is consequently

`u_up_camera = -normalize(R_camera_from_board[:, 1])`.

For a frontal identity rotation this returns `[0, -1, 0]`, which is camera-up under the OpenCV image convention. The sign test is covered by `test_gravity_vector_sign`.

## Why vehicle yaw does not change camera gravity

Let `R_world_from_camera` describe the rigid Camera+ToF assembly. World-up expressed in camera coordinates is

`u_up_camera = R_world_from_camera.T @ e_z`.

A vehicle-only turn on level ground left-multiplies the camera orientation by a world vertical-axis yaw `Rz(psi)`:

`R'_world_from_camera = Rz(psi) @ R_world_from_camera`.

Then

`u'_up_camera = R'_world_from_camera.T @ e_z`

`= R_world_from_camera.T @ Rz(psi).T @ e_z`

`= R_world_from_camera.T @ e_z`

`= u_up_camera`,

because a yaw about world-up leaves `e_z` unchanged.

The numerical audit sweeps widely separated internal yaw cases and recovers the same vector to floating-point precision. This proves why W04G does not need a measured vehicle-to-board yaw and why the full W04G PnP rotation must not become Camera→World authority.

## Authority boundary

W04G may authorize only:

- `u_up_camera`;
- an angular uncertainty derived from frame spread, core-solver agreement, and K/D sensitivity;
- `source_pose = W04G`;
- `representation_policy = VECTOR_FIRST`.

It may not authorize world yaw, global horizontal origin, or a full `R_world_from_camera`. Those remain separate and low-confidence until another authority is supplied.
