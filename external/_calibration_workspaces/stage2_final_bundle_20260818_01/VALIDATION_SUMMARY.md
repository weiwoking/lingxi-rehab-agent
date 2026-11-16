# Validation Summary

## Stage2A

- Train RMS `0.156727` px; held-out RMS `0.157685` px; held-out P95 `0.286514` px; max `0.630078` px.
- 28 fit frames and 28 held-out frames. Candidate validation says `True`.
- Primary-vs-top2 full-image mapping displacement P95/max: `2.384494` / `2.720265` px.

## Stage2B

- Selected solver: `R3_core_square_centered`; core all-data RMS `5.183` mm.
- Core scan-held-out median/P95/RMS/max: `3.120` / `9.043` / `4.683` / `19.972` mm.
- LOPO angular P95 summary p50/p95/max: `0.1033` / `0.2187` / `0.2270` deg.
- Subset angular P95 summary p50/p95/max: `0.1936` / `0.3748` / `0.4366` deg.

## Stage2C independent validation

- 4 registered scans; core count `2233`.
- Median/P90/P95/P99/RMS/max absolute: `4.716` / `10.419` / `12.778` / `19.525` / `6.702` / `25.170` mm.
- Signed mean `-2.297` mm; absolute mean `5.379` mm.
- Coverage: near/mid/far `{'near': 1, 'mid': 2, 'far': 1}`; low/medium/high angle `{'low': 2, 'medium': 1, 'high': 1}`.
- Novelty: two novel, one novel-relative, one likely repeat. No pose or zone was deleted; no automatic blacklist; frozen authorities and candidate bytes remained unchanged.
- Diagnostic delta versus Stage2B: median `+1.596`, P95 `+3.735`, RMS `+2.019`, max `+5.198` mm. The label is not a hard gate. The data shows cm-scale degradation worth an owner decision, not a 10s/100s-mm collapse.

## Bundle consistency

- `29` checks; `0` failed; overall `PASS`.
- Brown projection regenerated all center and rectangle tables at 400/600/1000 mm with max errors `2.274e-13` / `2.274e-13` px.
- Raw-zone count/order, identity canonical gauge, axial-z semantics, transform convention, R orthonormality and authority hashes passed.
