# ChArUco print check

Print `board.png` on A4 at **100% / actual size**. Disable ‘fit to page’, scaling, and borderless enlargement.

OpenCV `squares_x=7` and `squares_y=5` mean chessboard squares across and down; there are `24` internal ChArUco corners.

After printing, use a reliable ruler or caliper and record:

- one square side (nominal `25.000 mm`);
- complete active board width (nominal `175.000 mm`);
- complete active board height (nominal `125.000 mm`).

Copy the measured values into `board_manifest.json`, set `print_measurement.confirmed=true`, and add an RFC3339 `measured_at`. The calibration command rejects an unconfirmed or out-of-tolerance print. Do not trust printer settings alone.
