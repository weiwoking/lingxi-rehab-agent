# Before/after numeric publication snippets

The full previous source snapshots and frozen final snapshots are included alongside this file.

| Gate | Before | After |
|---|---|---|
| no eligible direct support, but observed metric candidates exist | `SupportUnavailable(ambiguous_support)` | select existing ordered best-effort candidate, numeric + degraded, confidence ≤ 0.15 |
| two physically separated candidates are indistinguishable | `SupportUnavailable(ambiguous_support)` | preserve chosen numeric candidate, degraded, confidence ≤ 0.20 |
| formal ToF valid count is zero, raw 64-zone distance/sigma finite | attempt nearest / unavailable | `TOF_DEGRADED_RAW`, current raw metric evidence, confidence ≤ 0.05 |
| geometry confidence below 0.5 | unavailable/null publication | numeric remains published; status becomes degraded |
| P4 projection receives degraded geometry | nullable/failure semantics | `valid=true`, numeric fields preserved, degraded distance status |
