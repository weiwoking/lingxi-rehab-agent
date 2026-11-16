# Systematic Bias Diagnostic V2

No calibration or correction was applied. For the truth-blind repeatability winner, descriptive Pearson correlations are:

- distance_mm: r=-0.09357100118714884, n=229
- bbox_center_x: r=0.11287524703823232, n=229
- bbox_center_y: r=0.06887183113094736, n=229
- tof_formal_valid_count: r=0.20300243793505482, n=229

Raw ToF range and lighting were not retained in the compact authority artifacts. Distance, Stage2 projection, DELTAR depth and world-Z therefore cannot be causally separated here. Class, region-source, and ToF-status group summaries are in `ROUND2_EVALUATION_SUMMARY.json`/the script-derived JSON evidence.
