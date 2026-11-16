# M3 + W4 implementation mapping

This mapping is frozen before production edits. It implements the authoritative Round2 `extract_and_counterfactual.py` semantics and changes only the final W0 combiner to the Round5.1 W4 formula.

| Frozen research step | Exact semantics | Production symbol / placement |
|---|---|---|
| Raw support candidates | Every current-frame environment surface observation, including eligible and rejected observations; eligible height uses `_eligible.support_mm`, rejected height uses surface height | New `geometry_v2.multimodal_support.build_object_distribution`; called from `generic_support.estimate_support` before diagnostic truncation |
| Evidence weight | Tier × direct-contact × line × depth consistency × compactness × contact/footprint coverage, exact Round2 constants | `multimodal_support._evidence_weight` |
| Provenance-aware representation | Same-method candidates merge only within 14 mm height, 110 mm axial and 8 px component-bbox gap; group weight is `max + 0.20*(sum-max)`; raw member heights remain separate | `multimodal_support.build_object_distribution` |
| Same-frame support context | Scene-spanning bbox area ≥0.40 is singleton; otherwise bbox-bottom rows link within 30 px; ordering and membership copy frozen `context_clusters` | `multimodal_support.context_clusters` called once inside `AllObjectGeometryProcessor.process_frame` |
| Object vote normalization | Each object contributes total weight 1/object_count; context weights renormalize | `multimodal_support.combine_context_distributions` |
| Continuous distribution | Gaussian KDE, bandwidth 24 mm, 2 mm grid | `multimodal_support.retained_modes` |
| Retained modes | `scipy.signal.find_peaks`, minimum separation 42 mm, fallback global peak, assignment to nearest peak, mass/density ordering, maximum four | `multimodal_support.retained_modes` |
| Mode probabilities | Assigned candidate mass divided by total assigned mass | `multimodal_support.retained_modes` |
| Final support | **W4 only:** `sum(p_i**2 * center_i) / sum(p_i**2)` for all retained modes; one mode is identity | `multimodal_support.squared_probability_expectation` |
| Numeric-First/status | A finite distribution always publishes numeric support. Existing per-object confidence/degraded reason is retained; compact mode diagnostics are added | `_apply_m3_w4_context_support` in `all_object_geometry.py` |
| Shape extent | New environment support is immutable; generic/upright-container/ball extent is recomputed against it; no shape path may replace support | `_finalize_v2_record` in `all_object_geometry.py` |

No feature flag, M0 fallback selector, shadow path, temporal state, truth constant, 0.68 probability branch or 1.85 ratio branch will exist in production.
