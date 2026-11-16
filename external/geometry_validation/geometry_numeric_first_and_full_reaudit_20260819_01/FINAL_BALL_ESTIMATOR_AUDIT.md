# FINAL_BALL_ESTIMATOR_AUDIT

- Routed observations: 66
- Specialized sphere estimator active: 6
- Generic extent fallback: 60
- Degraded observations still carrying numeric object height: 65
- Active results with `read_only_environment_support`: 6/6
- Fixed nominal radius/height: none
- Support override: none

Sphere fitting may fail its own fit/support-consistency gates; that produces generic extent fallback, not a fabricated nominal ball. Successful sphere estimates determine center/radius/top but cannot mutate environment support. `FINAL_BALL_ESTIMATOR_AUDIT_PASS=true`.
