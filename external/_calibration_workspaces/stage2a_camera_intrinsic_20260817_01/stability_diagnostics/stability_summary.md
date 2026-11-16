# Stage2A Camera Intrinsic Stability Diagnostics

Generated: `2026-08-17T13:40:48.334134Z`

Read-only diagnostic evidence only: no pose removal, candidate replacement, promotion, GO/FAIL gate, or Stage2B action was performed.

## Dynamic dataset model

Current selected primary poses: **28**; LOPO runs: **28**.

Requested subset fraction: `0.8`; rule: `ceil(N * requested_fraction)`; subset size: **23**; actual fraction: `0.821429`.

Historical pre-edge22 and pre-supplement25 are explicit pose-ID allow-lists, independent of the current N. Future selected poses are included in current full/top2/filtered/LOPO/subsample analysis without source edits.

## Current full solution comparisons

Primary vs Top-2: p50/p95/p99/max = 1.618629/2.384494/2.552362/2.720265 px; angle p95/max = 0.141924/0.154709 deg; 2 m p95/max = 4.954/5.400 mm; worst raw pixel = (0.0, 0.0).

Primary vs no-A04 diagnostic fit: p50/p95/p99/max = 0.554864/0.758300/0.810207/0.869139 px; angle p95/max = 0.045423/0.049422 deg; 2 m p95/max = 1.586/1.725 mm; worst raw pixel = (0.0, 0.0).

Primary vs historical pre-edge22 subset: p50/p95/p99/max = 9.256434/14.140126/15.739727/16.718773 px; angle p95/max = 0.846063/0.937731 deg; 2 m p95/max = 29.535/32.736 mm; worst raw pixel = (639.0, 359.0).

Primary vs pre-supplement25 subset: p50/p95/p99/max = 1.387368/2.044680/2.201860/2.484167 px; angle p95/max = 0.121999/0.141195 deg; 2 m p95/max = 4.259/4.929 mm; worst raw pixel = (0.0, 0.0).

## C14/C15/C16 before/after

Pre25 anchor reconstruction verdict: **CONSISTENT**; max absolute parameter delta `0`.

Pre25 Primary→Top2: p50/p95/p99/max = 2.281923/3.326605/3.488248/3.542804 px; angle p95/max = 0.198458/0.204950 deg; 2 m p95/max = 6.928/7.154 mm; worst raw pixel = (0.0, 32.0).

Current Primary→Top2: p50/p95/p99/max = 1.618629/2.384494/2.552362/2.720265 px; angle p95/max = 0.141924/0.154709 deg; 2 m p95/max = 4.954/5.400 mm; worst raw pixel = (0.0, 0.0).

Top2 mapping p95 change: `3.326605` → `2.384494` px; absolute `-0.942111` px, relative `-28.32%`.

Pre25 remove-B08: p50/p95/p99/max `4.977415/6.366027/6.534647/6.560819` px; angle p95/max `0.381901/0.388304` deg; 2m p95/max `13.331/13.555` mm; worst `(48.0, 352.0)`.

Current remove-B08: p50/p95/p99/max `2.272276/3.109185/3.286453/3.318596` px; angle p95/max `0.188137/0.193550` deg; 2m p95/max `6.567/6.756` mm; worst `(16.0, 359.0)`.

B08 p95 change: `-3.256842` px (`-51.16%`); max change: `-3.242223` px (`-49.42%`).

## LOPO distributions

Pre25 min/p5/p50/p95/max: `0.180267/0.453271/1.730375/3.782637/6.366027` px; worst `B08`; B08 rank `1`; worst/median `3.68x`.

Current min/p5/p50/p95/max: `0.179159/0.330987/1.250919/3.125323/3.438075` px; worst `C03`; B08 rank `3`; worst/median `2.75x`.

## Deterministic subset distributions

Pre25: N=25, size=20, actual=0.800000, mapping p95 min/p5/p50/p95/max `0.466840/1.467809/4.479353/8.883847/17.226023` px, angular-p95 distribution `0.028519/0.087292/0.267939/0.542189/1.034896` deg, worst run `90`, worst/median `3.85x`.

Current: N=28, size=23, actual=0.821429, mapping p95 min/p5/p50/p95/max `0.815795/1.588389/3.568899/9.243174/13.819040` px, angular-p95 distribution `0.048219/0.097131/0.210490/0.556639/0.833505` deg, worst run `98`, worst/median `3.87x`.

Current B08 missing-vs-present mean p95: `5.264915` vs `4.108395` px (difference `+1.156520`).

C14 missing-vs-present mean p95: `3.940648` vs `4.392943` px (difference `-0.452295`).

C15 missing-vs-present mean p95: `3.865050` vs `4.414265` px (difference `-0.549215`).

C16 missing-vs-present mean p95: `4.178848` vs `4.316908` px (difference `-0.138061`).

## Spatial sensitivity

Pre25 worst regional aggregate: `corner_bottom_left_20pct`. Current worst regional aggregate: `corner_bottom_left_20pct`. Exact rankings and heatmaps are in the JSON/output folders.

## Read-only regression protection

Manifest before/after: `ea664caf9d8d042b72e58445dc32e54d4727e57fafb1ea9dc2d0f793eb1bfb77` / `ea664caf9d8d042b72e58445dc32e54d4727e57fafb1ea9dc2d0f793eb1bfb77`.

Ranking before/after: `4ca09745a971019b59005972621a1bce23765b348f0c338f0972a6e2a03f51ef` / `4ca09745a971019b59005972621a1bce23765b348f0c338f0972a6e2a03f51ef`.

Candidate before/after: `99e5fe45e56b9f9a5a05f26179a9ee124610c3d4524b4e257b88c7b149979f6b` / `99e5fe45e56b9f9a5a05f26179a9ee124610c3d4524b4e257b88c7b149979f6b` (protected target `99e5fe45e56b9f9a5a05f26179a9ee124610c3d4524b4e257b88c7b149979f6b`).

All protected files byte-for-byte unchanged: **True**.
