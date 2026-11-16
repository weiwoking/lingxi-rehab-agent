# Offline Fusion Method Comparison (truth blind)

| Method | mean scan MAD | mean scan std | mean scan span | >100 mm repeatability outliers |
|---|---:|---:|---:|---:|
| `current_winner_take_all` | 39.434 | 152.451 | 462.236 | 80 |
| `hybrid_dominant_or_soft` | 33.564 | 153.278 | 437.599 | 82 |
| `mixture_posterior_soft_estimate` | 30.921 | 153.120 | 436.583 | 82 |
| `provenance_weighted_median` | 34.591 | 152.192 | 432.619 | 82 |
| `surface_hypothesis_expectation` | 30.889 | 153.168 | 437.331 | 82 |
| `trimmed_huber_location` | 33.444 | 150.216 | 431.102 | 82 |

Frozen method: `surface_hypothesis_expectation`. Selection used repeatability only and occurred before truth loading.

The method is not authorized for production because the later truth-bearing Gate failed.
