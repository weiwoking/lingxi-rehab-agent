# Candidate Information Flow Graph

`raw environment points → production extractor observations → selector candidates → diagnostic serialization → first-round consolidation → support context → KDE/find_peaks hypotheses → final estimate`

| Stage | File / function | Input → output | Numeric / weight transformation | Information effect |
|---|---|---|---|---|
| R-1 environment points | `environment_surfaces.contact_context` | axial/world-Z pixels near object footprint | finite/positive mask; object dilation exclusion | hard spatial exclusion |
| R0 extractor observations | `extract_environment_surfaces` | context pixels → accepted-plane/local-height connected components | 8 mm histogram, ±12 mm band; median height/MAD/axial | hard pixel/span/MAD gates; `_is_duplicate` removes ≥70% overlap within 8 mm |
| S selector representation | `support_selector._eligible` | one surface + contact row → eligible estimate or rejection | projection can replace surface height; assigns evidence tier | hard rejection and possible height transformation |
| D diagnostic serialization | `select_support` diagnostics | full observations/candidates → JSON diagnostics | rejected and best-effort lists capped by `maximum_diagnostic_candidates=20` | audit-only hard truncation; runtime selection already occurred |
| R1 first-round provenance consolidation | Round-1 `consolidate_object_candidates` | serialized candidates → representatives | close same-generator height/axial/spatial rows merged; representative height is evidence-weighted average; group weight capped | changes height and compresses weights |
| R2 support context | Round-1 `context_clusters` | object representatives → bbox-bottom-Y contexts | ≤30 px bottom-Y linking; scene-spanning bbox isolated | group assignment; no candidate-height deletion within assigned object |
| R3 surface hypotheses | Round-1 `build_hypotheses` | context representatives → KDE peaks | 24 mm bandwidth; 42 mm peak distance; 10% relative density floor; 42 mm assignment radius | peak pruning, unassigned tails, reconstructed centers/probabilities |
| R4 estimate | Round-1 `method_outputs` | hypotheses → weighted expectation | top ≤4 hypothesis centers averaged | final numeric reconstruction |

The Round-2 P1 path preserves every selector-candidate height. Provenance groups normalize only total weight; they do not replace member heights. P2 robust estimators operate on that continuous distribution. P3 interprets modes only at the final step.

## Frozen six-scan cardinality

| Transition | Aggregate input | Aggregate output | Cardinality interpretation |
|---|---:|---:|---|
| R0 → R0S | 3,156 | 3,076 | 80 rows are absent only from capped diagnostics; runtime selection already saw all R0 rows |
| R0S → first-round R1 | 3,076 | 1,913 | duplicate/provenance grouping plus representative-height reconstruction |
| full R0 → full-capture consolidation | 3,156 | 1,950 | separates true consolidation from diagnostic truncation |
| full R0 → height-preserving P1 | 3,156 | 3,156 | no height deletion; group/object total weights alone are normalized |
| object distributions → R2 | 229 objects | 135 contexts | bbox-bottom-Y connected grouping; candidate rows remain 3,156 across assigned contexts |
| first-round R1/R2 → R3 | 1,913 representatives | 150 hypotheses | KDE peak reconstruction/pruning and 42 mm member assignment |
| R3/current selector → R4 | 150 offline hypotheses / production candidate lists | 229 object outputs | first-round P0 uses hypothesis expectation; current production uses per-object lexicographic selector |

`R-1 → R0` cannot be expressed as a single candidate cardinality because R-1 is a dense per-object context-pixel set. Its per-object pixel count, world-Z percentiles, and 4 mm histogram are retained in `round2_compact_candidate_data.json`; R0 begins only after the production extractor's pixel-count, span, MAD, and overlap gates.
