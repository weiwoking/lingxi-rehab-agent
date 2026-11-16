# Per-Object vs Shared-Context Audit

Same-frame sharing is evaluated only after each object independently produces candidates. It is not cross-frame.

- P1_weighted_median: sharing improved 79/171 multi-object rows; worsened/tied 92.
- P2_huber: sharing improved 91/171 multi-object rows; worsened/tied 80.
- P2_trimmed_mean: sharing improved 85/171 multi-object rows; worsened/tied 86.
- P2_robust_posterior: sharing improved 96/171 multi-object rows; worsened/tied 75.
- P3_late_multimodal: sharing improved 91/171 multi-object rows; worsened/tied 80.

Because helping and contamination both occur, unconditional cross-object pooling is not justified.
