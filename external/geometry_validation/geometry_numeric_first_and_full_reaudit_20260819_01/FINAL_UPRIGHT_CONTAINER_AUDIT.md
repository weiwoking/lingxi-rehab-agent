# FINAL_UPRIGHT_CONTAINER_AUDIT

- Routed observations: 141
- Specialized estimator active: 134
- Generic fallback: 7
- Degraded observations still carrying numeric object height: 100
- Active results with `read_only_environment_support`: 134/134
- Fixed nominal height: none
- Support override: none

The cup/bottle estimator determines top/extent only. Its result is combined with the already-selected environment support, and degraded support lowers final geometry confidence through `min(support_confidence, extent_confidence)`. It remains active in all six replays. `FINAL_UPRIGHT_CONTAINER_AUDIT_PASS=true`.
