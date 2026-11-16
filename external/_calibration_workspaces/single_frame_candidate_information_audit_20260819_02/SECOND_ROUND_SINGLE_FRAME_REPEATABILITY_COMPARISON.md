# Second-Round Single-Frame Repeatability Comparison

Every value is computed from one frame only; 11 frames are aggregated only for evaluation.

- Current_production: mean lower MAD=19.982 mm; mean lower span=162.414 mm; total >100 mm=24
- P0_discrete_hypothesis: mean lower MAD=17.208 mm; mean lower span=115.713 mm; total >100 mm=20
- P1_weighted_median: mean lower MAD=16.514 mm; mean lower span=118.809 mm; total >100 mm=20
- P2_huber: mean lower MAD=16.087 mm; mean lower span=114.103 mm; total >100 mm=20
- P2_trimmed_mean: mean lower MAD=15.516 mm; mean lower span=112.952 mm; total >100 mm=18
- P2_robust_posterior: mean lower MAD=16.149 mm; mean lower span=113.637 mm; total >100 mm=20
- P3_late_multimodal: mean lower MAD=15.467 mm; mean lower span=116.419 mm; total >100 mm=18
- LOCAL_P1_weighted_median: mean lower MAD=19.424 mm; mean lower span=152.472 mm; total >100 mm=23
- LOCAL_P2_huber: mean lower MAD=18.099 mm; mean lower span=144.347 mm; total >100 mm=23
- LOCAL_P2_trimmed_mean: mean lower MAD=18.847 mm; mean lower span=141.711 mm; total >100 mm=23
- LOCAL_P2_robust_posterior: mean lower MAD=17.993 mm; mean lower span=145.354 mm; total >100 mm=23
- LOCAL_P3_late_multimodal: mean lower MAD=18.101 mm; mean lower span=147.907 mm; total >100 mm=23

Truth-blind repeatability winner: `P3_late_multimodal`. Selection used MAD then span, never truth error.
