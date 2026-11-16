# NUMERIC_PUBLICATION_GATE_AUDIT

Independent source-first inventory of the gates changed in Phase A. `confidence` is now quality metadata, not publication permission.

| File | Function/path | Condition | Pre-change behavior | Frozen post-change behavior |
|---|---|---|---|---|
| `geometry_v2/support_selector.py` | `select_environment_support` | observed metric surfaces exist, but none satisfies direct physical eligibility | `SupportUnavailable(ambiguous_support:no_direct_physical_candidate)` | select the best existing generic-order candidate; numeric + degraded; confidence capped at 0.15 |
| `geometry_v2/support_selector.py` | near-tie branch | physically separated candidates are indistinguishable | `SupportUnavailable(ambiguous_support:indistinguishable_physical_surfaces)` | publish chosen candidate; numeric + degraded; confidence capped at 0.20 |
| `all_object_geometry.py` | object publication | confidence below 0.5 / degraded support | numeric could be converted to unavailable/null | publish finite support/extent; `numeric_available=true`, `geometry_status=degraded` |
| `tof_degraded.py` | zero formal-valid branch | raw finite positive distance/sigma exists but target/status invalid | nearest-ToF attempt, then unavailable | `TOF_DEGRADED_RAW`; raw evidence remains explicitly low-quality, confidence at most 0.05 |
| `dual_gate_selection.py` | geometry gate | geometry status not `ok` | treated as failure/fallback | both `ok` and `degraded` are numeric success |
| `selected_object_projection.py` | canonical P4 projection | degraded numeric | could become invalid/null | `valid=true`, numeric preserved, `all_object_geometry_degraded` |
| `pc_scene_ui_adapter.py` | display-level projection | degraded numeric | risk of `—` | render numeric fields and quality diagnostics; only unavailable renders `—` |

Residual `None`/raise paths were separately classified in `FINAL_NULL_AND_FAILURE_PATH_AUDIT.md`. No fixed nominal value, truth clamp, previous-result reuse, or class-specific support value was added.
