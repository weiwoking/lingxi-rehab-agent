# Second-Round Fusion Architecture Options

All options remain design candidates; production was not modified.

## A — Repair preprocessing, keep discrete hypotheses

Preserves explicit surface identity, but remains sensitive to representative-height averaging, KDE bandwidth, peak floor, separation, and assignment radius. It is appropriate only if hypothesis survival can be made stable without truth-tuned thresholds.

## B — Height-preserving provenance-normalized continuous distribution

Retains every candidate height while limiting each provenance group's total vote. It directly removes an observed information-compression mechanism and is easy to inspect. Risk: a single robust center can hide genuine multimodality.

## C — Continuous distribution with late multimodal interpretation

Retains B and delays dominant-mode/posterior interpretation to the final step. It best matches the observed mix of correct-centered and multimodal frames. Risk: more states, calibration obligations, and ambiguous-output policy.

## D — Minimal provenance-normalized robust location

Weighted median or Huber only. Lowest implementation complexity and a useful fallback, but it cannot express competing support surfaces and shared-context contamination must remain gated.

## E — Keep current selector, diagnose upstream bias first

Appropriate for Type-C-dominant scans, where fusion cannot create missing central evidence. It does not address raw-near evidence lost during hypothesis construction.

Recommendation: **C**, built on B's lossless representation, because it preserves information through the pipeline and handles Type B without an early hard winner. Alternate: **D** as a minimal, separately gated first implementation if Owner prioritizes auditability and rollback simplicity. Type-C cases still require an upstream workstream; neither option should be truth-fit.
