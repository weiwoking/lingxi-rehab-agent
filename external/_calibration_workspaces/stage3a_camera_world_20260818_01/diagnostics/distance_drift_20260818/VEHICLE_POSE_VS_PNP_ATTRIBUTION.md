# Vehicle pose versus PnP attribution

The image-space active-board centre y range across W01–W03 is **0.355 px**, a small-angle equivalent of **0.0234°** at fy. A real 1.79° pitch change would shift a distant fixed feature by approximately **27.10 px** before compensating translation; the known fixed camera/board heights do not provide the roughly 30 mm vertical compensation that would be needed over a one-metre sightline.

This centre-y test is not used alone. The JSON also reports top/bottom width ratio, left/right height ratio, and edge angles from distortion-corrected homographies for every pose. The constrained common-gravity model fits all raw corners directly and is the stronger combined test.

Yaw is different: per-pose horizontal placement and projective skew support vehicle-to-cabinet reposition/yaw variation. Because no measured vehicle-forward reference exists, yaw remains a nuisance/gauge degree of freedom and must not be promoted as high-precision vehicle yaw.

Attribution: observed yaw change can be real repositioning; the approximately two-degree PnP pitch trend is not supported as real mechanical gravity change and is primarily planar pose conditioning/coupling, with intrinsic-model sensitivity as a secondary amplifier.
