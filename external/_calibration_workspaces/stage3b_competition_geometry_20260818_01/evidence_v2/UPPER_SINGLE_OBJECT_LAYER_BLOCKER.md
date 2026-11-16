# Upper Single-Object-Layer Blocker

The blocker is resolved for the G001 primary acceptance band without same-surface help. The structural cause is consistent across G001/G002: the mask/bbox confirms an upright object, but silhouette-only height truncates the thin/transparent top. v2 independently recovers the shelf from local gravity-aligned context and rejects the short enhancer when it conflicts with coherent object-depth top evidence.

## G001

- Frames: 11; same-surface consensus: false.
- Median support: 1075.0 mm (+10.0 mm), always `local_gravity_support_surface`.
- Median top: 1237.4 mm; height: 162.5 mm (-9.7%).
- Router: `upright_slender`; final dominant path: `generic_support->generic_extent` because the short enhancer failed the generic-top consistency gate.

## G002

- Frames: 10; same-surface consensus: false.
- Median support: 1076.0 mm (+11.0 mm), always `local_gravity_support_surface`.
- Median top: 1236.1 mm; height: 160.1 mm (-11.0%).
- Router: `upright_slender`; final dominant path: `generic_support->generic_extent` because the short enhancer failed the generic-top consistency gate.

G001 moved from v1 −18.9% to v2 −9.7%, entering the acceptable ≤10% primary band. G002 moved from −19.4% to −11.0%; this is a substantial robustness improvement but remains a documented margin item for future formal integration testing.
