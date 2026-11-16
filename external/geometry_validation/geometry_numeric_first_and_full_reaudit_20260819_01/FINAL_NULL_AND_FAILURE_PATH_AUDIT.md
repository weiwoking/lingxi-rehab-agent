# FINAL_NULL_AND_FAILURE_PATH_AUDIT

The frozen production sources were scanned for `None`, `raise`, `unavailable`, early returns, confidence/status thresholds, and valid-zone thresholds. The machine inventory is `evidence/static_gate_inventory.json`.

| Path | Classification | Why numeric may disappear | Disposition |
|---|---|---|---|
| support selector: zero observed environment surfaces | JUSTIFIED_TRUE_INCALCULABLE | no metric support candidate exists | unavailable is correct |
| object region selection: no mask/bbox or insufficient finite projected points | JUSTIFIED_TRUE_INCALCULABLE | no current metric object region | unavailable is correct |
| generic extent: insufficient finite values or top not physically above support | JUSTIFIED_TRUE_INCALCULABLE | no positive metric extent | unavailable is correct |
| final finite/positive contract check | JUSTIFIED_TRUE_INCALCULABLE | NaN, infinity, or non-positive geometry cannot be published | unavailable is correct |
| ToF: no current formal metric, no finite raw metric, and nearest violates age/pose gate | JUSTIFIED_TRUE_INCALCULABLE | no safe current or borrowed metric input | unavailable is correct |
| source capture / payload decode / RGB–ToF transfer failure | CAPTURE_OR_TRANSFER_FAILURE | source observation is missing or corrupt | frame records failure; captured RGB remains authoritative when available |
| per-object unexpected runtime exception | BUG/UNEXPECTED | implementation defect, not quality policy | isolated object unavailable with explicit failure; must be reported, never silently suppressed |
| low confidence / weak tier / ambiguous candidates with at least one metric candidate | QUALITY_ONLY_SHOULD_DEGRADE | numeric is calculable | frozen code publishes numeric + degraded; no residual quality-only null gate found |

Result: `FINAL_NULL_AND_FAILURE_PATH_AUDIT_PASS=true`. This pass is semantic only: it does not claim the published low-confidence numbers are stable.
