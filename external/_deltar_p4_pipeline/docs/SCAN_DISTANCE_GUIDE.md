# Scan Distance Guide

Frozen mode: VL53L5CX continuous 8x8 at 15 Hz. Default distance is **1.2 m**; normal recommended range is **0.8-1.5 m**. The conditional extended range is **0.6-2.5 m**, never an unconditional accuracy promise.

| Object | Range | Rationale |
|---|---:|---|
| Small (cup/bottle/book) | 0.8-1.2 m | Keeps a small target large enough relative to pixels/zones. |
| Medium | 1.0-1.5 m | Balances target fill and complete-extent coverage. |
| Large (chair/table) | 1.2-1.8 m | More FoV coverage; farther values require favorable ToF conditions. |
| Complete adult | 2.0-2.5 m or multi-frame | About 2.05 m is needed to cover 1.70 m vertically with 45 degrees; ToF may still fail for gray/bright/corner targets. |

With the official 45 x 45 degree detection FoV, geometric width/height is `2*d*tan(22.5 degrees)`, or about `0.828*d`. At 1.2 m this is 0.994 x 0.994 m and a nominal zone is about 0.124 x 0.124 m. At 1.5 m the values are 1.243 m and 0.155 m/zone. Geometry is not a ranging guarantee.

ST DS13754 Rev 13 states 2-400 cm per zone, but in 8x8/15 Hz continuous mode the typical maximum changes from 3.5 m (white, dark, inner) to 0.65 m (gray, 5 klx, corner). For 201-4000 mm it specifies +/-5% in dark for white/gray, versus +/-8% white and +/-11% gray at 5 klx, under full-FoV target conditions. The dataset-derived band metrics are in `outputs_stage5_2b/distance_band_analysis.json`.

Do not move closer than 0.8 m by default: large objects clip more readily, and cover-glass crosstalk below 0.6 m needs special attention. Do not move farther merely because 4 m is advertised: zones get coarser and ambient/reflectance/corner-zone limits dominate. Camera FoV remains unfrozen and cannot be inferred from 640x360.

Official sources: `docs_external/vl53l5cx_ds13754.pdf` (DS13754 Rev 13, especially pages 4, 21-23) and `docs_external/vl53l5cx_um2884.pdf` (UM2884 Rev 7, especially pages 1 and 6), downloaded from STMicroelectronics URLs recorded with hashes in `hardware_spec_audit.json`.
