# Gravity-fixed fit and cross-distance validation design

## Fit layer

Inputs:

- frozen Stage2 K/D;
- W04G `u_up_camera` vector;
- metric 175 × 125 mm board geometry;
- raw detected image corners and IDs.

Per-frame free variables:

- one relative board/vehicle yaw nuisance around the fixed gravity axis;
- translation `tx, ty, tz` in camera coordinates.

The board-down rotation column is fixed to `-u_up_camera`. A horizontal basis is constructed orthogonal to gravity and rotated by the one yaw nuisance. Raw corner reprojection is minimized with four variables. This makes relative yaw and metric translation observable from the planar image while intentionally leaving absolute world yaw unobservable.

The fit function receives no camera height, board-bottom height, or board-top height. Tests inspect its source and reject any hidden `912`, `1037`, `CAMERA_HEIGHT`, or `BOARD_BOTTOM` dependency.

## Validation layer

Only after the image fit is complete:

`Z_world(point) = camera_height + 1000 * dot(u_up_camera, point_camera)`.

For each W01/W02/W03 frame, reconstruct the two active-bottom endpoints and active-top endpoints. Then report per pose:

- bottom and top signed residual;
- median, p95, RMS, and within-pose spread;
- residual-versus-distance slope;
- core-solver agreement;
- camera-height results at 962/972/982 mm.

Camera height changes every reconstructed Z by the same constant and cannot alter distance slope. Board-height uncertainty changes the comparison target by a constant and likewise cannot remove a slope.

## Future real-data acceptance

The main acceptance signal is substantial suppression of the old monotonic ~50 mm/m trend. A residual mostly within roughly 10–20 mm is preferred after accounting for camera ±10 mm and board ±5 mm physical uncertainty, but zero residual is not required.

If W04G gravity does not suppress the W01–W03 slope, do not emit a gravity candidate, do not collect a second oblique pose, and classify the route as failed.

## Synthetic end-to-end result

The stress test projects a realistic oblique W04G and three near-frontal poses with current corner-visibility patterns and 0.15 px noise. A horizontal effective-focal perturbation is used solely to reproduce the existing near-frontal decomposition failure class; it is not a claim that Stage2 K is wrong by that amount.

Across 16 deterministic trials:

- unconstrained full-PnP height slope p95: 52.943 mm/m;
- recovered W04G gravity error p95: 0.078°;
- gravity-fixed height slope median/p95/max: 0.622 / 1.357 / 1.425 mm/m;
- gravity-fixed image reprojection RMS median: 0.500 px.

Thus the complete vector-authority → gravity-fixed fit → independent-height-validation method suppresses the injected failure without using height truth in its fit layer.
