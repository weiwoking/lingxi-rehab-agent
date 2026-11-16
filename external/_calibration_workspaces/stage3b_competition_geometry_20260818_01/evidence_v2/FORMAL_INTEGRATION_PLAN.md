# Formal Integration Plan

This plan is authorized for design only; no formal module is changed in this task.

1. Add a feature-flagged all-object v2 implementation behind the existing Stage8B object contract. Default the flag off until formal replay parity and rollback tests pass.
2. Bind only to the frozen Stage2 dynamic-Fr camera authority and Stage3A gravity/world-Z authority. Validate their schema and hashes at startup; do not duplicate or refit them.
3. Preserve per-object outputs and add transparent diagnostics: generic support, support source/confidence, generic extent, selected family/evidence, enhancer result/rejection, final path, and same-surface group ID.
4. Keep the stabilizer boundary downstream. Stabilization consumes final per-frame geometry and must not feed target names or temporal truth-like corrections back into generic support/extent.
5. Port `generic_support`, `generic_extent`, router, and family enhancers as separable modules. Preserve generic fallback for every accepted object.
6. Regression gates: existing formal Geometry contract tests; synthetic router tests; G001 primary thresholds; G002 structural replay; H001 65-object count/finite/positive/no-catastrophe guard; malformed mask/depth and enhancer fail-closed tests.
7. Rollback: one configuration flag restores the existing formal path without changing Stage2/Stage3A or data formats. Record both candidate and legacy results during a shadow phase.
8. Re-audit the G002 upper −11.0% margin and held-out consensus breadth during shadow validation. Do not introduce class offsets or target-name rules to close it.
