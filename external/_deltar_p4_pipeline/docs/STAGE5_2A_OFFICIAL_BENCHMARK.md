# Stage 5.2A official benchmark contract

- Dataset: all 287 entries in the official Zhejiang University DELTAR demo index.
- Native input: RGB and GT depth 480x640; hist_data 64x2; FR 64x4 `[sy,sx,ey,ex]`; mask 64.
- Object universe: same RGB, YOLO11n detect and YOLO11n-seg, same class, greedy bbox IoU >= 0.5, non-empty segment.
- Reference: `segment_eroded_3px ∩ official valid GT depth`, then MAD-filtered median.
- Old prediction: detect bbox only, official hist/FR/mask only, official identity direction, range/MAD/median logic. GT and segment are unavailable to prediction.
- New prediction: direct official DELTAR native path; object statistic is `segment_eroded_3px + mad_filtered_median`. GT is unavailable to model inference.
- Fair comparison: one matched object with valid GT, valid old result, and valid new result.
- Verdict: PARTIAL. Coverage gates passed, but new absolute-error median was 3.3 mm worse and mixed-zone median improvement was not material; Stage 6 is not authorized.
