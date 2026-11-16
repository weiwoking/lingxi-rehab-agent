# Generic Extent Design

The generic estimator replaces the false `p95 == physical top` assumption. It takes valid object samples above support, defines an extreme candidate set from a theory/sampling-derived upper percentile, finds spatially coherent connected components, and returns the median of a sqrt(N)-sized top-k set. Finite fallbacks are top-k median and then p99 positive extent.

Enhancers are advisory. A family result is accepted only when it is available and its top agrees with the generic coherent top within 20 mm. Otherwise v2 emits the generic result and records the rejection reason. This gate resolved the upper-bottle truncation: the silhouette candidate remained short and was rejected, while coherent sparse cap/top depth was retained.

Threshold sources are geometry, robust-statistics semantics, synthetic shape behavior, and cross-frame stability. Competition truths were not used for fitting, grid search, percentile search, or parameter selection.
