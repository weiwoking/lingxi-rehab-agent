# Peak-weighting competition scorecard

All W0–W8 definitions and outputs were frozen before truth was loaded. Selection priority is CORE catastrophic >100, the equal-weight normalized span/MAD pair, >75, >50, bias and complexity.

## CORE_OBJECTS

| Scheme | mean lower MAD | std | span | lower median | bias | >50 | >75 | >100 | complexity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| W0 | 13.650 | 22.008 | 74.059 | 755.40 | +30.40 | 42 | 8 | 4 | 3 |
| W1 | 14.579 | 20.858 | 68.992 | 755.40 | +30.40 | 44 | 6 | 4 | 1 |
| W2 | 14.780 | 20.817 | 68.992 | 755.66 | +30.66 | 46 | 6 | 4 | 1 |
| W3 | 15.063 | 20.990 | 70.254 | 755.66 | +30.66 | 44 | 11 | 4 | 1 |
| W4 | 12.782 | 21.098 | 69.283 | 755.66 | +30.66 | 44 | 6 | 4 | 1 |
| W5 | 14.676 | 21.339 | 71.619 | 755.66 | +30.66 | 44 | 11 | 4 | 1 |
| W6 | 14.259 | 20.971 | 70.240 | 755.66 | +30.66 | 44 | 10 | 4 | 2 |
| W7 | 13.564 | 21.202 | 69.444 | 755.66 | +30.66 | 44 | 6 | 4 | 2 |
| W8 | 12.999 | 21.252 | 69.592 | 755.66 | +30.66 | 46 | 6 | 4 | 2 |

## ALL_OBJECTS

| Scheme | mean lower MAD | std | span | lower median | bias | >50 | >75 | >100 | complexity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| W0 | 15.467 | 33.688 | 116.419 | 755.40 | +30.40 | 59 | 22 | 18 | 3 |
| W1 | 15.395 | 33.591 | 111.944 | 755.40 | +30.40 | 62 | 20 | 18 | 1 |
| W2 | 15.596 | 33.550 | 111.944 | 755.40 | +30.40 | 64 | 20 | 18 | 1 |
| W3 | 16.459 | 33.840 | 111.734 | 755.66 | +30.66 | 62 | 27 | 18 | 1 |
| W4 | 14.478 | 33.572 | 113.447 | 755.40 | +30.40 | 62 | 20 | 18 | 1 |
| W5 | 15.869 | 34.351 | 111.490 | 755.66 | +30.66 | 62 | 27 | 18 | 1 |
| W6 | 15.363 | 33.972 | 111.630 | 755.66 | +30.66 | 62 | 25 | 18 | 2 |
| W7 | 14.959 | 33.765 | 112.382 | 755.40 | +30.40 | 62 | 20 | 18 | 2 |
| W8 | 14.290 | 33.872 | 113.261 | 755.40 | +30.40 | 64 | 20 | 18 | 2 |

**Best variant: W4 squared-probability expectation.** Versus W0, CORE mean lower MAD improves 13.650→12.782 mm (6.4%), span 74.059→69.283 mm (6.4%), >75 drops 8→6, >100 stays 4, while >50 rises 42→44. ALL_OBJECTS shows the same bounded pattern: MAD 15.467→14.478, span 116.419→113.447, >100 unchanged at 18, >75 22→20, >50 59→62.

W4 meets the task's explicit minimum “clearly better” gates, but the unchanged catastrophic floor and increased moderate-error count make it a small competition optimization, not a broad fix.
