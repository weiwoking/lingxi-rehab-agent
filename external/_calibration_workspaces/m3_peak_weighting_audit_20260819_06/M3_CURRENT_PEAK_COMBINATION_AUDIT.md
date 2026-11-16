# M3 current peak-combination audit

**M3_CURRENT_RULE_RECONSTRUCTED = true.** W0 reproduces all 135 frozen context outputs with maximum absolute difference **0.0 mm**.

Authoritative source: `extract_and_counterfactual.py`, parameters lines 25–27 and estimator lines 271–276. Modes are sorted by mass/density, limited to four (observed max three), and probabilities sum to one in all retained contexts.

```python
ratio = inf if one_mode else top_probability / second_probability
soft = probability_weighted_average(retained_mode_centers)
W0 = top_center if top_probability >= 0.68 or ratio >= 1.85 else soft
```

Across 135 contexts: **118 single-mode**, **8 multi-mode HARD_TOP**, **9 multi-mode SOFT_EXPECTATION**. Only the eight HARD_TOP contexts exhibit a top-vs-expectation discontinuity.

| Scan | Frame | Context | Modes | top p | p−0.68 | ratio | ratio−1.85 | Branch | W0 | W1 | cliff mm |
|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|
| G001 | frame_000000 | 00 | 2 | 0.5424 | -0.1376 | 1.1851 | -0.6649 | SOFT_EXPECTATION | 1050.57 | 1050.57 | 0.00 |
| G001 | frame_000005 | 00 | 2 | 0.5012 | -0.1788 | 1.0049 | -0.8451 | SOFT_EXPECTATION | 1054.22 | 1054.22 | 0.00 |
| G001 | frame_000006 | 01 | 2 | 0.5474 | -0.1326 | 1.2093 | -0.6407 | SOFT_EXPECTATION | 693.63 | 693.63 | 0.00 |
| SCAN_070604 | frame_000001 | 01 | 2 | 0.6667 | -0.0133 | 2.0000 | +0.1500 | HARD_TOP | 713.04 | 754.14 | 41.10 |
| SCAN_070604 | frame_000002 | 02 | 2 | 0.5128 | -0.1672 | 1.0526 | -0.7974 | SOFT_EXPECTATION | 552.50 | 552.50 | 0.00 |
| SCAN_070604 | frame_000005 | 00 | 2 | 0.6106 | -0.0694 | 1.5678 | -0.2822 | SOFT_EXPECTATION | 1062.68 | 1062.68 | 0.00 |
| SCAN_070604 | frame_000005 | 01 | 2 | 0.5423 | -0.1377 | 1.1847 | -0.6653 | SOFT_EXPECTATION | 799.20 | 799.20 | 0.00 |
| SCAN_070604 | frame_000006 | 02 | 2 | 0.7028 | +0.0228 | 2.3643 | +0.5143 | HARD_TOP | 458.88 | 485.98 | 27.10 |
| SCAN_070604 | frame_000007 | 02 | 2 | 0.9164 | +0.2364 | 10.9581 | +9.1081 | HARD_TOP | 597.43 | 587.37 | 10.05 |
| SCAN_070604 | frame_000008 | 01 | 2 | 0.6905 | +0.0105 | 2.2309 | +0.3809 | HARD_TOP | 773.84 | 799.44 | 25.60 |
| SCAN_085223 | frame_000002 | 01 | 2 | 0.7065 | +0.0265 | 2.4076 | +0.5576 | HARD_TOP | 753.12 | 792.04 | 38.92 |
| SCAN_085223 | frame_000003 | 01 | 2 | 0.5766 | -0.1034 | 1.3619 | -0.4881 | SOFT_EXPECTATION | 563.25 | 563.25 | 0.00 |
| SCAN_085223 | frame_000005 | 02 | 2 | 0.7200 | +0.0400 | 2.5716 | +0.7216 | HARD_TOP | 607.82 | 577.07 | 30.76 |
| SCAN_085223 | frame_000007 | 01 | 3 | 0.5038 | -0.1762 | 1.9340 | +0.0840 | HARD_TOP | 814.59 | 750.68 | 63.91 |
| SCAN_085223 | frame_000007 | 02 | 3 | 0.8069 | +0.1269 | 6.1057 | +4.2557 | HARD_TOP | 785.24 | 732.77 | 52.47 |
| SCAN_085223 | frame_000010 | 01 | 2 | 0.6165 | -0.0635 | 1.6075 | -0.2425 | SOFT_EXPECTATION | 677.58 | 677.58 | 0.00 |
| SCAN_165011 | frame_000000 | 00 | 2 | 0.5221 | -0.1579 | 1.0924 | -0.7576 | SOFT_EXPECTATION | 1041.90 | 1041.90 | 0.00 |

The largest observed cliff is SCAN_085223 frame7: ratio 1.934 is only +0.084 above 1.85, yet the branch changes 750.68 mm expectation into the 814.59 mm top mode, a 63.91 mm jump. This demonstrates a real hard-switch cliff, but only in a small minority of contexts.
