# Runtime Cross-Frame State Audit

`RUNTIME_USES_CROSS_FRAME_INFORMATION = false`  
`ELEVEN_FRAME_REPEATABILITY_IS_EVALUATION_ONLY = true`

- Static production source forbidden-state hits: 0.
- Dynamic ordered vs shuffled vs isolated single-frame equivalence: PASS across 66 frames.
- Each dynamic invocation reconstructs the frame input through JSON and calls only candidate extraction, within-object provenance normalization, same-frame context formation, hypothesis construction, and fusion.
- No scan accumulator, previous result, frame history, rolling statistic, or temporal state is passed to the estimator.

`RUNTIME_CROSS_FRAME_STATE_AUDIT_PASS = true`
