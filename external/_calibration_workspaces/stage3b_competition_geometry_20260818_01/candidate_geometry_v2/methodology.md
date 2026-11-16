# Stage3B all-object Geometry v2 methodology

The estimator is validation-only. Ground truth is absent from every estimator,
router and threshold. G001/G002 truth is loaded only after aggregation.

Every successful formal object first receives a generic support estimate and a
spatially coherent top-k vertical envelope. Accepted global planes are preferred;
otherwise the largest substantial, highest gravity-Z-stable component adjacent to
the object bottom is used. A p02 lower envelope is the last finite fallback.

Same-surface groups are discovered per frame using only support-Z proximity, image
bottom proximity and confidence. A consensus is rejected per object if it would
make the extent non-positive. No target name or class pair participates.

The router uses silhouette aspect, mask availability, roundness, border state,
depth compactness and support confidence. Class contributes at most 0.10 as a weak
prior. Geometry gates remain mandatory. Enhancers are fail-closed: an upright or
spherical top more than 20 mm from the independent generic coherent top is rejected.

Threshold provenance: Stage7E source semantics supply mask/anchor/erosion choices;
synthetic ellipse/slender masks supply family geometry gates; Stage3A uncertainty,
the previous perfect-shape percentile study and robust component statistics supply
support/top consistency gates. No threshold was grid-searched against 725, 1065,
180 or 195 mm.
