# Conservative multi-frame aggregation

Stage 6 aggregation is observation organization, not tracking, SLAM, pose compensation, mask fusion, 3D reconstruction, or object completion.

An observation can join an existing group only when the class matches, the prior observation is in a consecutive/nearby frame, and either source-space IoU meets the versioned threshold or both valid 3D centroids are within the versioned distance threshold. A group receives at most one observation from each frame. If more than one group is eligible, Stage 6 creates a new group and records `W_MULTIFRAME_AMBIGUOUS_ASSOCIATION`; ambiguity never forces a merge. GT is not an association input.

Best-observation scoring is published in each object: segmentation +100, canvas agreement +20, geometry points up to +20, complete +10, non-border +5, non-fallback +3, depth compactness up to +2, plus confidence. Ties resolve deterministically by frame ID.

Distance, visibility and height come from the single best observation. Top from one frame and bottom from another are never combined, masks are never spliced, frame results are not mutated, and no complete shape or physical height is inferred across views.
