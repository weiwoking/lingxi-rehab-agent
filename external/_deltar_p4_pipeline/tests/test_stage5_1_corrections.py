from __future__ import annotations

import numpy as np

from deltar_p4_pipeline.analytic_renderer import stage5_scene_family
from deltar_p4_pipeline.rgb565 import decode_rgb565_le, encode_rgb565_le
from deltar_p4_pipeline.stage5_fr import fr_from_diagnostics, validate_fr_pair
from deltar_p4_pipeline.stage5_matching import classify_prediction_universe, perception_counts
from deltar_p4_pipeline.stage5_metrics import physical_height_applicability
from deltar_p4_pipeline.tof_simulator import simulate_tof
from deltar_p4_pipeline.zone_orientation import consumer_index_map, inverse_index_map, orientation_profile, validate_orientation


def test_fr_is_sy_sx_ey_ex_and_letterbox_is_y_plus_60():
    scene = stage5_scene_family()[0]
    tof, diagnostics = simulate_tof(scene, measurement_semantics="radial_range")
    direct, _ = fr_from_diagnostics(scene, diagnostics, "direct_640x360")
    letter, _ = fr_from_diagnostics(scene, diagnostics, "letterbox_640x480")
    valid = [c > 0 and s != 255 for c, s in zip(tof["nb_target_detected"], tof["target_status"], strict=True)]
    result = validate_fr_pair(direct, letter, valid)
    assert result["order"] == "sy_sx_ey_ex"
    assert result["zone_count"] == 64 and result["degenerate_count"] == 0
    assert result["direct_letterbox_exact_y_plus_60"]


def test_fr_uses_actual_selected_hit_points_for_valid_zones():
    scene = stage5_scene_family()[0]
    _, diagnostics = simulate_tof(scene)
    direct, metadata = fr_from_diagnostics(scene, diagnostics, "direct_640x360")
    valid_index = next(i for i, zone in enumerate(diagnostics["zones"]) if zone["valid"])
    assert diagnostics["zones"][valid_index]["selected_hit_points_camera_m"]
    assert metadata[valid_index]["fr_provenance"] == "selected_surface_hits"
    sy, sx, ey, ex = direct[valid_index]
    assert ey > sy and ex > sx


def test_invalid_zone_has_nominal_provenance_but_false_mask():
    scene = stage5_scene_family()[0]
    scene["tof"]["invalid_zone_indices"] = [0]
    tof, diagnostics = simulate_tof(scene)
    _, metadata = fr_from_diagnostics(scene, diagnostics, "direct_640x360")
    assert tof["target_status"][0] == 255 and tof["nb_target_detected"][0] == 0
    assert metadata[0]["fr_provenance"] == "nominal_invalid_zone"


def test_radial_and_axial_are_distinct_per_ray_distributions():
    scene = stage5_scene_family()[-1]
    radial, rd = simulate_tof(scene, seed=9, measurement_semantics="radial_range")
    axial, ad = simulate_tof(scene, seed=9, measurement_semantics="camera_axial_z")
    assert rd["measurement_semantics"] == "radial_range" and ad["measurement_semantics"] == "camera_axial_z"
    assert rd["sigma_mm_source"] != ad["sigma_mm_source"]
    assert any(a != b for a, b in zip(radial["distance_mm"], axial["distance_mm"], strict=True))


def test_all_orientation_strategies_are_invertible_and_corners_known():
    for strategy in ("identity", "flip_horizontal", "flip_vertical", "flip_both", "rotate_90", "rotate_180", "rotate_270"):
        profile = validate_orientation(orientation_profile(strategy))
        mapping = consumer_index_map(strategy); inverse = inverse_index_map(mapping)
        assert sorted(profile["consumer_index_map"]) == list(range(64))
        assert np.array_equal(np.arange(64)[mapping][inverse], np.arange(64))
    assert consumer_index_map("identity")[[0, 7, 56, 63]].tolist() == [0, 7, 56, 63]
    assert consumer_index_map("flip_horizontal")[[0, 7, 56, 63]].tolist() == [7, 0, 63, 56]


def test_explicit_orientation_permutation_round_trip():
    explicit = list(reversed(range(64)))
    profile = validate_orientation(orientation_profile("explicit_index_map_64", explicit))
    assert profile["consumer_index_map"] == explicit
    assert np.array_equal(np.arange(64)[explicit][inverse_index_map(explicit)], np.arange(64))


def test_prediction_universe_counts_wrong_class_duplicate_and_false_valid():
    truths = [{"class_name": "chair", "bbox_xyxy": [0, 0, 10, 10]}]
    predictions = [{"class_name": "chair", "bbox_xyxy": [0, 0, 10, 10]},
                   {"class_name": "chair", "bbox_xyxy": [0, 0, 10, 10]},
                   {"class_name": "tv", "bbox_xyxy": [0, 0, 10, 10]},
                   {"class_name": "cup", "bbox_xyxy": [20, 20, 30, 30]}]
    rows = classify_prediction_universe(predictions, truths)
    assert {row["match_status"] for row in rows} == {"tp", "duplicate", "wrong_class", "fp"}
    counts = perception_counts(rows, {0: True, 1: True, 2: True, 3: False})
    assert counts["TP"] == 1 and counts["FP"] == 3
    assert counts["wrong_class"] == 1 and counts["duplicate"] == 1 and counts["false_valid_count"] == 2


def test_visible_height_always_scoreable_but_physical_height_can_be_not_applicable():
    occluded = {"visible_pixel_count": 40, "full_projected_pixel_count": 100, "occlusion_ratio": .6,
                "image_border_contact": False, "full_geometric_extent_inside_image": True}
    result = physical_height_applicability(occluded)
    assert result["physical_height_metric_status"] == "not_applicable"
    assert "occluded" in result["physical_height_not_applicable_reasons"]
    visible = {"visible_pixel_count": 100, "full_projected_pixel_count": 100, "occlusion_ratio": 0,
               "image_border_contact": False, "full_geometric_extent_inside_image": True}
    assert physical_height_applicability(visible)["physical_height_metric_status"] == "applicable"


def test_rgb565_is_single_deterministic_rgb_source():
    rgb = np.zeros((360, 640, 3), dtype=np.uint8); rgb[..., 0] = 255; rgb[..., 1] = 127; rgb[..., 2] = 9
    payload = encode_rgb565_le(rgb); decoded = decode_rgb565_le(payload)
    assert len(payload) == 460800 and decoded.shape == (360, 640, 3) and decoded.dtype == np.uint8
    assert decoded[0, 0, 0] > decoded[0, 0, 2]


def test_stage5_full_chain_calls_frozen_stage4_entrypoint(monkeypatch, tmp_path):
    import deltar_p4_pipeline.stage5_full_chain as chain
    calls = []
    monkeypatch.setattr(chain, "process_object_group", lambda group, calibration, profile, root: calls.append(group) or {"status": "rejected", "logical_object_id": group["logical_object_id"]})
    np.savez(tmp_path / "masks.npz")
    document = {"instances": [{"instance_id": 0, "class_id": 56, "class_name": "chair", "confidence": .9, "bbox_xyxy": [100, 100, 200, 250], "mask_key": None}]}
    result = chain.process_full_new_scene("sample", {canvas: np.full((360, 640), 2.0, np.float32) for canvas in chain.CANVASES},
                                          document, {"instances": []}, {}, tmp_path / "masks.npz",
                                          {canvas: "depth.npy" for canvas in chain.CANVASES}, {}, {}, tmp_path)
    assert len(calls) == 1 and result["frozen_methods"]["detection_fallback"] == chain.DETECTION_METHOD
    assert calls[0]["provenance"]["stage4_function"] == "stage4_validation.process_object_group"
