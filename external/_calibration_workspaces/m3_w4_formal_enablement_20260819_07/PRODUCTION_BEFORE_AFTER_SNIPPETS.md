# Production before/after authority snippets

The complete tracked diff is in `GIT_DIFF.patch`; the complete new module is in `production_source/multimodal_support.py`. The excerpts below show the authority cutover.

## Before (intake HEAD)

```python
def measure_logical_object_v2(
    group: dict[str, Any],
    segment_masks: dict[str, np.ndarray],
    camera_axial_z_mm: np.ndarray,
    world_points_mm: np.ndarray,
    support_planes: list[dict[str, Any]],
    profile: dict[str, Any],
    camera: dict[str, Any],
    gravity: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    axial = np.asarray(camera_axial_z_mm, dtype=np.float64)
    world = np.asarray(world_points_mm, dtype=np.float64)
    if axial.shape != IMAGE_SHAPE or world.shape != (*IMAGE_SHAPE, 3):
        raise ValueError("formal Geometry v2 input shape invalid")
    region, valid, raw_mask, bbox, meta = _selected_v2_region(
        group, segment_masks, axial, world, profile
    )
    support = estimate_support(
        raw_mask if raw_mask is not None else region,
        bbox,
        axial,
        world,
        valid,
        support_planes,
        config["generic_support"],
        gravity,
    )
    generic = estimate_generic_extent(
        valid,
        world[..., 2],
        float(support["support_mm"]),
        config["generic_extent"],
    )
    router = route_shape(
        str(group.get("class_name", "")),
        raw_mask,
        bbox,
        float(support["confidence"]),
        meta["depth_iqr_mm"],
        config["router"],
    )
    enhancement: dict[str, Any] = {
        "available": False,
        "reason": "generic_family_selected",
    }
    if router["selected_family"] == "upright_container" and raw_mask is not None:
        enhancement = estimate_upright_container(
            raw_mask,
            float(support["support_mm"]),
            camera,
            gravity,
            axial,
            valid,
            config["upright_container"],
        )
    elif router["selected_family"] == "sports_ball":
        enhancement = estimate_ball_extent(
            raw_mask,
            bbox,
            float(support["support_mm"]),
            axial,
            world,
            camera,
            gravity,
            config["sports_ball"],
        )
    if enhancement.get("available"):
        disagreement = abs(float(enhancement["top_mm"]) - float(generic["top_mm"]))
        enhancement["generic_top_disagreement_mm"] = disagreement
        if disagreement > float(
            config["generic_extent"]["maximum_enhancer_top_disagreement_mm"]
        ):
            enhancement["available"] = False
            enhancement["reason"] = "generic_top_consistency_gate"
    if enhancement.get("available"):
        # Support is primary environment/contact geometry.  Shape-family
        # estimators may improve extent/top but may not replace support.
        final_support = float(support["support_mm"])
        final_top = float(enhancement["top_mm"])
        final = {
            "support_mm": final_support,
            "support_source": support["source"],
            "top_mm": final_top,
            "object_height_mm": final_top - final_support,
            "extent_method": enhancement["method"],
            "extent_confidence": float(enhancement["confidence"]),
            "estimator_path": f"generic_support->{router['selected_family']}",
        }
    else:
        final = {
            "support_mm": float(support["support_mm"]),
            "support_source": support["source"],
            "top_mm": float(generic["top_mm"]),
            "object_height_mm": float(generic["object_height_mm"]),
            "extent_method": generic["method"],
            "extent_confidence": float(generic["confidence"]),
            "estimator_path": "generic_support->generic_extent",
        }
        if router["selected_family"] != "generic":
            router["fallback_reason"] = f"enhancer_failed:{enhancement.get('reason')}"
    if not all(
        np.isfinite(
            [final["support_mm"], final["top_mm"], final["object_height_mm"]]
        )
    ) or final["object_height_mm"] <= 0.0:
        raise ValueError("Geometry v2 must preserve finite positive geometry")
    selected_source, selected = _selected_observation(group)
    base_confidence = min(
        float(support["confidence"]), float(final["extent_confidence"])
    )
    # Confidence is an evidence bound, not a mask-presence classification.
    # In particular, an otherwise good silhouette cannot promote weak or
    # occlusion-derived support evidence across the normal-confidence gate.
    geometry_confidence_value = (
        min(CONFIDENCE_THRESHOLD - 1e-6, base_confidence)
        if meta["region_source"] == "bbox"
        else base_confidence
    )
    degradation_reason = support.get("degradation_reason")
    if degradation_reason is None and meta["region_source"] == "bbox":
        degradation_reason = "bbox_only_geometry"
    if degradation_reason is None and geometry_confidence_value < CONFIDENCE_THRESHOLD:
        degradation_reason = "low_confidence_geometry"
    geometry_status = "degraded" if degradation_reason else "ok"
    public = {
        "logical_object_id": str(group["logical_object_id"]),
        "class_id": int(group["class_id"]),
        "class_name": str(group["class_name"]),
        "confidence": float(selected["confidence"]),
        "bbox_xyxy": bbox,
        "selected_source": selected_source,
        "provenance": dict(group.get("provenance") or {}),
        "numeric_available": True,
        "geometry_status": geometry_status,
        "geometry": {
            "distance_mm": float(np.median(axial[valid])),
            "object_height_mm": float(final["object_height_mm"]),
            "support_height_mm": float(final["support_mm"]),
            "top_height_above_floor_mm": float(final["top_mm"]),
        },
        "geometry_confidence": float(geometry_confidence_value),
        "degradation_reason": degradation_reason,
        "failure_reason": None,
        "diagnostics": {
            **meta,
            "distance_semantics": "camera_axial_z_median",
            "height_semantics": "stage3a_gravity_world_z",
            "support_source": final["support_source"],
            "extent_method": final["extent_method"],
            "estimator_path": final["estimator_path"],
            "shape_family": router["selected_family"],
            "degradation_reason": degradation_reason,
            "same_surface_group_id": None,
            "consensus_used": False,
        },
    }
    return {
        "public": public,
        "bbox_xyxy": bbox,
        "generic_support": support,
        "generic_extent": generic,
        "router": router,
        "enhancement": enhancement,
        "final": final,
        "same_surface_group_id": None,
        "consensus_used": False,
    }

```

## After (frozen working tree)

```python
def measure_logical_object_v2(
    group: dict[str, Any],
    segment_masks: dict[str, np.ndarray],
    camera_axial_z_mm: np.ndarray,
    world_points_mm: np.ndarray,
    support_planes: list[dict[str, Any]],
    profile: dict[str, Any],
    camera: dict[str, Any],
    gravity: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    axial = np.asarray(camera_axial_z_mm, dtype=np.float64)
    world = np.asarray(world_points_mm, dtype=np.float64)
    if axial.shape != IMAGE_SHAPE or world.shape != (*IMAGE_SHAPE, 3):
        raise ValueError("formal Geometry v2 input shape invalid")
    region, valid, raw_mask, bbox, meta = _selected_v2_region(
        group, segment_masks, axial, world, profile
    )
    support = estimate_support(
        raw_mask if raw_mask is not None else region,
        bbox,
        axial,
        world,
        valid,
        support_planes,
        config["generic_support"],
        gravity,
        object_id=str(group["logical_object_id"]),
        class_name=str(group["class_name"]),
    )
    distribution = support.pop("_m3_object_distribution")
    record = _finalize_v2_record(
        group,
        axial,
        world,
        region,
        valid,
        raw_mask,
        bbox,
        meta,
        camera,
        gravity,
        config,
        support,
    )
    record["_m3_object_distribution"] = distribution
    return record


def _apply_m3_w4_context_support(
    objects: list[dict[str, Any]],
    records_by_index: dict[int, dict[str, Any]],
    frame_id: str,
) -> None:
    """Replace M0 support with frozen same-frame M3 modes and W4 output."""
    for context_index, members in enumerate(context_clusters(objects)):
        distributions = [
            records_by_index[index]["_m3_object_distribution"]
            for index in members
            if index in records_by_index
            and records_by_index[index].get("_m3_object_distribution")
        ]
        if not distributions:
            continue
        combined = combine_context_distributions(distributions)
        context_id = f"{frame_id}:m3_w4_context:{context_index:02d}"
        compact_modes = [
            {
                key: float(mode[key]) if key != "candidate_count" else int(mode[key])
                for key in (
                    "center_mm",
                    "probability",
                    "spread_mm",
                    "mass",
                    "candidate_count",
                    "grid_peak_mm",
                )
            }
            for mode in combined["retained_modes"]
        ]
        for index in members:
            old_record = records_by_index.get(index)
            if old_record is None:
                continue
            old_support = old_record["generic_support"]
            support = dict(old_support)
            support["support_mm"] = float(combined["support_mm"])
            support["source"] = "m3_continuous_multimodal_w4"
            diagnostics = dict(old_support.get("diagnostics") or {})
            diagnostics["m3_w4"] = {
                "context_id": context_id,
                "member_object_ids": [
                    str(objects[member].get("logical_object_id") or "")
                    for member in members
                ],
                "member_classes": [
                    str(objects[member].get("class_name") or "unknown")
                    for member in members
                ],
                "candidate_count": int(combined["candidate_count"]),
                "independent_object_count": int(combined["independent_object_count"]),
                "retained_mode_count": int(combined["retained_mode_count"]),
                "retained_modes": compact_modes,
                "combiner": combined["combiner"],
                "formula": combined["formula"],
                "support_mm": float(combined["support_mm"]),
                "runtime_uses_cross_frame_information": False,
            }
            support["diagnostics"] = diagnostics
            inputs = old_record["_measurement_inputs"]
            new_record = _finalize_v2_record(
                inputs["group"],
                inputs["axial"],
                inputs["world"],
                inputs["region"],
                inputs["valid"],
                inputs["raw_mask"],
                inputs["bbox"],
                inputs["meta"],
                inputs["camera"],
                inputs["gravity"],
                inputs["config"],
                support,
            )
            new_record["_m3_object_distribution"] = old_record[
                "_m3_object_distribution"
            ]
            records_by_index[index] = new_record
            objects[index] = new_record["public"]

```
