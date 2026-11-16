# Stage3B validation-only geometry candidate

`GROUND_TRUTH_USED_FOR_FIT = false`.

This runner replays the frozen Stage2 dynamic-fr and Stage3A gravity-only chain. It
does not construct a yaw or a full `T_world_from_camera`, and it does not edit any
formal runtime file.

For bottles, a real instance mask is required. The largest connected silhouette is
used without the one-pixel erosion that removes narrow caps. Robust lower-contour
anchors are selected using the Stage7E 15% edge trim. A local lower-interior contact
band supplies axial depth. Each base ray is back-projected at that robust depth and
then scanned in 1 mm steps along `u_up_camera`; the last contiguous point that still
projects inside the silhouette is an anchor height candidate. The candidate height
is the median across anchors, matching the physical intent of Stage7E while replacing
its hard dependency on a qualified support plane/full world transform with a
gravity-only camera-frame adapter.

For sports balls, segmentation is absent in these samples, so the detector box is
converted to an ellipse proxy. A robust free-radius 3D sphere fit is attempted on the
nearest coherent depth mode. It is accepted only under point-count, residual and
Jacobian-conditioning gates. The gravity projection of fitted centre ± radius supplies
support/top. If it fails closed, the same gravity silhouette scan uses the robust
upper-quintile median of vertical chords. No nominal ball diameter is supplied.

The detector-ellipse proxy is eroded by the existing Stage7E two-pixel mask erosion
before its envelope is measured; this is a source-derived boundary-robustness rule,
not a fitted diameter correction. The sphere fit additionally requires residual/R
<= 0.15. Fits that fail this scale-free surface-consistency gate remain diagnostics.

Support is the median world-Z of the same silhouette base anchors back-projected with
depth from a narrow visible-support band immediately below the lower contour. Object
lower-interior depth is only a fallback, because formal Stage7E already marks thin-
container dense depth as unverified. This is class-agnostic in its metric definition.
It remains explicitly a fallback (`candidate_contact_band`), not a fabricated plane.

For the two objects whose image contact and target registry place them on the same
lower layer, the final support is the median of their two per-target median contact
estimates. Each top remains independently estimated and height is `top - shared
support`. This enforces a physical same-layer constraint without inserting 725 mm.

Truth is loaded only after replay to compute acceptance errors and is never passed to
the per-frame estimator or configuration-selection functions.
