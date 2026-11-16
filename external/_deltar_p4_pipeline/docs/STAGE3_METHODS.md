# Stage 3 Methods

Regions are `bbox_full`, central 50% `bbox_center`, `bbox_depth_cluster`, `segment_mask`, segment erosion at 1/2/3 px, `segment_depth_cluster`, and eroded segment depth clusters. Erosion is explicit 3x3 binary morphology and never expands a mask. Depth clustering uses transparent 0.10 m histogram bins, minimum 8 pixels, and minimum 8% support.

Statistics are median, three-robust-sigma MAD-filtered median (with explicit MAD=0 behavior), 10%-per-tail trimmed mean, dominant peak, and nearest stable surface. Quantiles p10/p25/p50/p75/p90 and IQR are always recorded for a valid distribution.

The experimental ranking formula is:

```text
- median GT scalar error
- 0.5 * P90 GT scalar error
- rejection rate
- 0.25 * median direct/letterbox delta
```

It is only a Stage 3 comparison score, not final geometry confidence. On the frozen 15-sample official-demo matrix, the data-selected primary is `segment_eroded_3px + trimmed_mean`; the detection-compatible fallback is `bbox_depth_cluster + trimmed_mean`.

Primary metrics: median scalar GT error 0.0510 m, P90 0.2704 m, mean regional pixel MAE 0.1324 m, zero rejection among 42 canvas/object cases, and median direct/letterbox delta 0.0236 m. Fallback metrics: 0.0930 m, 0.2920 m, 0.1419 m, zero rejection among 92 cases, and 0.0151 m respectively. These quantify the official-data reverse-construction condition, not real P4 accuracy.

## Stage 3.1 evaluation rule

Stage 3.1 does not reuse the Stage 3 averaged GT references. Every predicted candidate is compared with GT computed using the same YOLO-defined spatial region and the same statistic implementation. Depth-cluster methods apply the same cluster parameters independently inside the fixed GT spatial region; GT never changes the YOLO bbox or mask.

Original 480x640 GT is resized with valid-aware numerator/weight interpolation. Bilinear, nearest, and area/BOX are evaluated independently; zero and non-finite source values cannot dilute valid depths.

Native sets are ranked separately. Coverage denominators are 72 canvas cases for 36 segmentation objects and 92 cases for 46 detection objects. The matched 21 pairs are comparison-only. With strict same-method GT and resize sensitivity, the frozen methods are:

```text
segmentation: segment_eroded_3px + mad_median
detection fallback: bbox_full + median
```

These replace the Stage 3 recommendations for Stage 4 consumption. The ranking remains experimental and includes coverage, rejection, median/P90/P95 GT scalar error, direct/letterbox agreement, and a GT-resize spread penalty.
