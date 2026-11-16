# Planar PnP comparison

All methods used the same Stage2 K/D, all detected correspondences, all 33 frames, and no intrinsic refit or frame rejection.

| method | median frame RMS px | pitch slope deg/m | yaw slope deg/m | height slope mm/m | frames with alternate |
| --- | --- | --- | --- | --- | --- |
| CURRENT_ITERATIVE | 0.1545 | 5.4687 | -7.6059 | 52.8937 | 0 |
| IPPE | 0.1886 | -0.1999 | -10.5226 | 86.9221 | 33 |
| SQPNP | 0.1545 | 5.4421 | -7.5963 | 53.3599 | 25 |
| EPNP | 0.1745 | 0.0946 | -3.4241 | 19.3360 | 0 |

`SOLVEPNP_IPPE` is the planar-specific comparison and exposes two decomposition solutions per frame. `SOLVEPNP_ITERATIVE` is the current method. `SQPNP` and `EPNP` are general comparisons. `IPPE_SQUARE` is not applicable because it requires exactly four points in a prescribed square order, while this is a 175×125 mm rectangle represented by ChArUco inner corners.

Low reprojection error coexists with materially different pitch/translation decompositions across distance. Agreement among iterative and IPPE's best branch does not remove the conditioning problem; IPPE's alternate branch quantifies the planar ambiguity.

Official method contracts: https://docs.opencv.org/4.x/d5/d1f/calib3d_solvePnP.html
