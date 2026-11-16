# Stage 6.1 Fallback Contract

The only permitted order is:

1. valid segmentation geometry;
2. matched detection fallback when that segmentation fails;
3. unmatched detection fallback when segmentation is absent;
4. reject when all available candidates fail.

Matched detection is retained in the logical object group even when segmentation succeeds. Fallback success emits `W_DETECTION_FALLBACK` and provenance; it is not a program error. Detection uses `bbox_full + median`, retaining formal contamination warnings and rejection rules.

Stage 6 directly calls `stage4_validation.process_object_group`; it does not reproduce canvas selection or geometry logic.
