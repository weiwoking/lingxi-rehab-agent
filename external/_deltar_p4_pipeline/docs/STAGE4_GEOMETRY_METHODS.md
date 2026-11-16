# Stage 4 Geometry Methods

This is a 3D object-geometry application layer built on Zhejiang University's official DELTAR dense depth. It is not a self-developed dense-depth network.

## Semantics and backprojection

The audit in `outputs_stage4/depth_semantics_audit.json` freezes `PLAUSIBLE_BUT_UNPROVEN / axial_z`: the paper explicitly calls L5 input along-ray distance, training code directly supervises with NYU/RealSense depth maps without naming output geometry, and active Calib_DELTAR code treats zone values as axial Z. The missing demo HDF5 generator prevents `PROVEN`. Both branches remain implemented.

Pinhole uses `x=(u-cx)/fx`, `y=(v-cy)/fy`; Brown points first use `cv2.undistortPoints`. Axial reconstruction is `[xZ,yZ,Z]`; radial reconstruction is `range*normalize([x,y,1])`. Debug arrays are Camera/Ground `[N,3]`, UV `[N,2]`, and depth `[N]`, kept only in memory by default.

## Point support

Segmentation uses a 3-pixel-eroded mask. Bbox fallback emits a contamination warning. Implemented comparison filters are all-valid, Stage 3 surface window, robust MAD, dominant peak, and Stage3/robust intersection. Windows use `max(k*1.4826*MAD, IQR factor, minimum metres)` from `geometry_profile.json`.

Metrics include initial/valid/retained counts and ratios, range, MAD/IQR, peak support, multimodality, border contact, method, and warnings. Unstable bbox peaks, empty support, or too few points reject instead of faking success.

Raw dense depth is converted exactly once at the Stage 4 point-cloud boundary: `depth_m = raw_depth * scale_to_meter`. Numeric/finite conversion and a finite positive scale are checked first; overflow/underflow rejects with `E_DEPTH_SCALE`. All min/max gates, MAD, IQR, surface windows, histogram bins, peak selection, clustering, and backprojection then consume only metre-valued depth. The point-cloud contract carries `depth_unit=meter`, and geometry rejects a cloud without that unit assertion. All profile fields ending in `_m` are metres regardless of the raw source unit.

## Distance and vertical geometry

Outputs include median Camera Z, median radial norm, norm of coordinate-wise median centroid, and a supported near-side band (never raw minimum). Stage 3 scalar depth stays provenance. Provisional `distance_m` is robust-centroid distance until Stage 5 freezes the public definition.

Ground Z is vertical and floor is `ground.plane_z_m`. The engine reports robust top/bottom/height plus min/max, p02/p98, p05/p95, p10/p90, and MAD-then-p05/p95 comparisons. Selected configurable quantiles are p00.5/p99.5 to reduce endpoint bias while rejecting isolated outliers. Top/bottom support and estimator stability remain explicit.

Bad order, below-floor, implausible height, low support, border contact, unresolved multimodality, partial masks, and unmeasured calibration cause rejection or warnings. Synthetic/unverified outputs have `absolute_geometry_valid=false`.

Both remapped canvases run the same core; distance/height/bottom/count deltas determine agreement. Agreement produces only a provisional selection. Disagreement produces no selection and `E_GEOMETRY_CANVAS_DISAGREEMENT`; one-sided success is explicitly marked and cannot become automatically valid. Selection is valid segmentation, matched detection fallback, unmatched detection fallback, then reject. Experimental canvas preference/ranking are transparent internal aids, not calibrated confidence.
