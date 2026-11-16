# Generic Support Design

The estimator is class-agnostic. An already-qualified global support plane wins. Otherwise it builds a bbox/mask-exterior context below and beside the object, projects valid samples to world Z, bins them, forms connected image components, and scores flatness, population, image span, and contact locality. Among substantial components (at least 60% of the largest population), it selects the highest credible surface, which prevents a broad lower/background band from hiding an immediately adjacent shelf.

Support source contract:

- `matched_global_plane`: accepted upstream horizontal plane, confidence 0.90.
- `local_gravity_support_surface`: locally connected, low-MAD gravity-aligned component.
- `same_surface_consensus`: median of a geometry-discovered group whose support Z and image-bottom position agree; the update fails closed if it would produce a non-positive extent.
- `generic_lower_envelope_fallback`: object-region p02 used only when no qualified global/local surface exists, with low confidence and an explicit fallback reason.

Same-surface grouping uses support delta, bbox bottom-Y delta, and confidence only. It does not inspect target names or class pairs. G001 lower bottle and volleyball had a 0.0 mm final support delta and shared automatically generated per-frame group IDs. Upper bottle remained independent and used local support in every accepted frame.
