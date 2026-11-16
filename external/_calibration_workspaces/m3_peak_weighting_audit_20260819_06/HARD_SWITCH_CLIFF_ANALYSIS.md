# M3 hard-switch cliff analysis

## Frame 7 — hard switch hurts

Modes are 814.59@0.5038, 734.81@0.2605, 631.60@0.2357. The top probability is below 0.68, but top/second=1.934 exceeds 1.85 by only 0.084, so W0 hard-selects 814.59 mm (error +89.59). W1 gives 750.68 mm (error +25.68); W4 gives 773.29 mm (error +48.29); equal mean gives 727.00 mm. The hard cliff is harmful here.

## Frame 2 — hard winner helps

Modes are 753.12@0.7065 and 885.74@0.2935. W0 hard-selects 753.12 mm (error +28.12); W1 moves to 792.04 mm (error +67.04); W4 moves to 772.63 mm (error +47.63). Here the hard rule protects the result.

## Frame 10 — already soft

Modes are 644.43@0.6165 and 730.85@0.3835. W0=W1=677.58 mm (error −47.42); W4 gives 668.54 mm (error −56.46). This is not a hard-cliff failure, and W4 slightly worsens it.

W4 therefore improves absolute error in only **1/3** multi-mode focus frames, worsens **2/3**, and is selected only because the frame7 gain dominates the smaller frame2/frame10 losses in the frozen aggregate. It is not a universal rule that “softer is better.”
