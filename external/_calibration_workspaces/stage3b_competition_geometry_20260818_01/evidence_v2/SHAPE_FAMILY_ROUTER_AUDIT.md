# Shape-Family Router Audit

Families are `generic`, `upright_slender`, and `roughly_spherical`. The router uses measured bbox aspect, mask availability/fill/roundness, border contact, support confidence, and depth compactness. Class name contributes at most 0.10 as a weak prior and cannot pass a family gate without geometry confirmation.

Synthetic unit evidence proves that a non-container class with slender geometry routes upright and a non-ball class with round geometry routes spherical. Real replay routed G001/G002 bottles upright and volleyball spherical from matching geometry. In unrelated H001, 42/65 objects used generic, 16 upright, and 7 spherical; all 65 still produced finite positive Geometry.

Failure behavior is explicit: an uncertain family returns generic with `no_shape_family_passed_geometry_confidence_gate`; an enhancer failure or top conflict records `enhancer_failed:<reason>` and falls back to generic extent. No class-only branch or fixed class compensation exists.
