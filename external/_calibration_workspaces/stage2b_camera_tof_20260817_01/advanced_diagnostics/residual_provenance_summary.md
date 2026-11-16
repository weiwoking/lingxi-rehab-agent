# Residual provenance

Broad solver/validation metrics can include observations marked outside/background; this output preserves two explicit layers.

| layer | count | median | p95 | RMS | max |
|---|---:|---:|---:|---:|---:|
| core_reference_plane_metrics | 9906 | 2.9724 | 10.5606 | 4.9870 | 19.5226 |
| all_broad_robustness_metrics | 10474 | 3.1782 | 18.1589 | 35.2152 | 1051.0422 |
| outside_background | 568 | 34.2237 | 292.0368 | 149.7802 | 1051.0422 |
| strict_valid | 10431 | 3.1638 | 16.9434 | 23.1526 | 618.8356 |
| broad_valid_but_not_strict | 43 | 277.9397 | 744.0855 | 414.7697 | 1051.0422 |

No automatic blacklist; runtime broad policy unchanged.
