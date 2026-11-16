# Fixed-height math audit

## Board coordinates and labels

For the exact installed OpenCV 5.0 runtime, `CharucoBoard((7,5), 0.025, 0.018, ...)` returns 24 chessboard object points spanning x=25..150 mm and y=25..100 mm. Raw-image correspondence verifies ID 0 (`[25,25,0]`) is the physical top-left inner corner, X increases image-right, and Y increases image-down. Thus the effective outer-board origin is the physical top-left, and the printed active outer rectangle is 0..175 mm by 0..125 mm. Active top is y=0 and active bottom is y=125 mm; the tool outline matches the printed active rectangle rather than the inner-corner envelope. This runtime check is important because generic OpenCV ChArUco documentation describes historical/version-dependent axis conventions.

The fixed mapping is

`R_world_from_board = [[1,0,0],[0,0,1],[0,-1,0]]`,

so board +X maps to world +X, board +Y (printed down) maps to world -Z, and board +Z maps to world +Y. Its determinant is +1 and it is orthonormal.

OpenCV PnP returns column-vector extrinsics:

`p_camera = R_camera_from_board @ p_board + t_camera_from_board`.

With a known board-to-world orientation,

`R_world_from_camera = R_world_from_board @ R_camera_from_board.T`.

The camera-origin contract fixes

`t_world_from_camera = [0, 0, 972] mm`,

and reconstruction is

`p_world = R_world_from_camera @ p_camera + t_world_from_camera`.

For a bottom point `[x,125,0] mm`, exact compatible transforms give world Z=912 mm; top points `[x,0,0]` give 1037 mm. Matrix direction, transpose, signs, column/row convention, and labels are internally consistent. No fixed-height math or active-rectangle bug was found.

## Important limitation in the old validation

The old tool first averages three full per-pose rotations, including nuisance yaw and planar-PnP pitch, then applies that one aggregate rotation to each pose's PnP translation. That operation is algebraically valid for a single common full camera/world orientation, but the experiment did not establish common yaw: vehicle-to-cabinet yaw changed between poses. Thus the validation is not a pure height check under the actual nuisance model, even though the matrix math itself is correct.

## Height-input sensitivity

For any pose, changing camera height by ΔH adds exactly ΔH to reconstructed bottom Z; changing the asserted board bottom by ΔB subtracts exactly ΔB from the signed residual. Neither depends on distance, so all combinations H=962/972/982 and B=907/912/917 preserve the slope **52.893661 mm/m** (up to floating-point roundoff) while shifting the common offset by ΔH-ΔB.
