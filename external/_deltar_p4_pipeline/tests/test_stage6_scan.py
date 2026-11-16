from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from deltar_p4_pipeline.cli import main as cli_main
from deltar_p4_pipeline.scan_aggregation import aggregate_frames, determine_scan_status
from deltar_p4_pipeline.scan_contracts import ScanContractError, atomic_write_json, load_strict_json
from deltar_p4_pipeline.scan_profiles import load_profile_bundle
from deltar_p4_pipeline.scan_validator import validate_scan

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def make_scan(root: Path, frames: int = 1) -> Path:
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)
    scan = root / "scan_test"
    _write(scan / "scan.json", {"schema_version": "deltar_scan_input.v1", "scan_id": "scan_test", "created_at": "2026-07-18T00:00:00Z",
           "frame_count": frames, "capture_mode": "single_frame" if frames == 1 else "multi_frame",
           "device": {"board": "esp32p4_function_ev", "camera": "SC2336", "tof": "VL53L5CX"}, "profiles": bundle.ids, "notes": None})
    for sequence in range(frames):
        frame_id = f"frame_{sequence:06d}"; folder = scan / "frames" / frame_id; folder.mkdir(parents=True)
        (folder / "image.rgb565").write_bytes(bytes(460800))
        _write(folder / "tof.json", {"schema_version": "rehab_tof_raw.v1", "sequence": sequence, "timestamp_us": sequence * 1000,
               "distance_mm": [1200] * 64, "range_sigma_mm": [20] * 64, "target_status": [5] * 64, "nb_target_detected": [1] * 64})
        _write(folder / "frame.json", {"schema_version": "deltar_scan_frame.v1", "frame_id": frame_id, "sequence": sequence,
               "camera_timestamp_us": sequence * 1000, "tof_timestamp_us": sequence * 1000, "pair_delta_us": 0,
               "image": {"filename": "image.rgb565", "width": 640, "height": 360, "pixel_format": "RGB565_LE", "stride_bytes": 1280, "payload_bytes": 460800},
               "tof": {"filename": "tof.json", "resolution": [8, 8]}})
    return scan


def observation(frame_id: str, bbox: list[float], *, source: str = "segmentation", complete: bool = True,
                class_name: str = "chair", point_count: int = 100) -> dict:
    class_id = {"chair": 56, "couch": 57}.get(class_name, 56)
    visibility = {"visibility_status": "complete" if complete else "partial", "top_visible": complete, "bottom_visible": True,
                  "full_extent_visible": complete, "touches_image_border": not complete}
    return {"frame_object_id": f"{frame_id}:{source}:{bbox[0]}", "class_id": class_id, "class_name": class_name,
            "class_scope": "core_now" if class_name == "chair" else "supported_but_not_core", "object_role": "operable_object",
            "object_source": source, "confidence": 0.8, "bbox_xyxy": bbox,
            "canvas": {"comparison": {"agreement_status": "high"}, "selected": "direct_640x360"},
            "distance_m": 1.2, "distance_semantics": "visible_surface_distance", "centroid_3d_m": None,
            "geometry_point_count": point_count, "depth_compactness": 0.9, "visibility": visibility,
            "visible_height": {"value_m": 0.5, "available": True, "absolute_valid": False},
            "physical_height": {"value_m": None, "available": False, "absolute_valid": False},
            "bottom_height": {"value_m": None, "available": False, "absolute_valid": False}, "placement_level": "unknown",
            "warnings": [] if complete else ["W_PARTIAL_OBJECT"], "rejection_reason": None}


def frame(frame_id: str, sequence: int, objects: list[dict]) -> dict:
    return {"frame_id": frame_id, "sequence": sequence, "status": "success", "objects": objects, "warnings": [], "processing_ms": {}}


def test_scan_contract_valid_and_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    scan = make_scan(tmp_path)
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)
    checked = validate_scan(scan, bundle)
    assert len(checked.frames) == 1 and checked.total_bytes > 460800
    assert cli_main(["scan-validate", "--scan-dir", str(scan), "--profile-bundle", str(ROOT / "profiles")]) == 0
    assert '"status": "valid"' in capsys.readouterr().out


@pytest.mark.parametrize("mutation,code", [
    (lambda scan: (scan / "frames/frame_000000/image.rgb565").write_bytes(b"bad"), "E_FRAME_IMAGE_SIZE"),
    (lambda scan: _mutate_json(scan / "frames/frame_000000/frame.json", lambda value: value["image"].update(filename="..\\image.rgb565")), "E_SCAN_PATH"),
    (lambda scan: _mutate_json(scan / "frames/frame_000000/tof.json", lambda value: value["distance_mm"].pop()), "E_FRAME_TOF_SCHEMA"),
])
def test_fail_closed_input(tmp_path: Path, mutation, code: str) -> None:
    scan = make_scan(tmp_path); mutation(scan)
    with pytest.raises(ScanContractError) as caught:
        validate_scan(scan, load_profile_bundle(ROOT / "profiles", validate_model_hashes=False))
    assert caught.value.code == code


def _mutate_json(path: Path, mutation) -> None:
    value = json.loads(path.read_text(encoding="utf-8")); mutation(value); _write(path, value)


def test_timestamp_and_pair_delta(tmp_path: Path) -> None:
    scan = make_scan(tmp_path)
    _mutate_json(scan / "frames/frame_000000/frame.json", lambda value: value.update(pair_delta_us=1))
    with pytest.raises(ScanContractError, match="E_FRAME_TIMESTAMP"):
        validate_scan(scan)


def test_profile_hashes_and_frozen_methods() -> None:
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=True)
    assert bundle.algorithm["segmentation_primary"]["region_method"] == "segment_eroded_3px"
    assert bundle.algorithm["detection_fallback"]["statistic_method"] == "median"
    assert bundle.calibration["physical_height_valid"] is False


def test_multiframe_same_object_and_best_complete() -> None:
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)
    first = observation("frame_000000", [100, 20, 200, 250], complete=False)
    second = observation("frame_000001", [102, 22, 202, 252], complete=True, point_count=500)
    objects, warnings, metrics = aggregate_frames([frame("frame_000000", 0, [first]), frame("frame_000001", 1, [second])], bundle)
    assert len(objects) == 1 and objects[0]["observation_count"] == 2
    assert objects[0]["best_observation_frame_id"] == "frame_000001"
    assert objects[0]["physical_height"]["value_m"] is None
    assert metrics["gt_used_for_association"] is False


def test_two_same_class_objects_do_not_merge() -> None:
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)
    frames = [
        frame("frame_000000", 0, [observation("frame_000000", [20, 20, 120, 250]), observation("frame_000000", [400, 20, 520, 250])]),
        frame("frame_000001", 1, [observation("frame_000001", [22, 20, 122, 250]), observation("frame_000001", [402, 20, 522, 250])]),
    ]
    objects, _, _ = aggregate_frames(frames, bundle)
    assert len(objects) == 2 and sorted(item["observation_count"] for item in objects) == [2, 2]


def test_same_location_different_classes_never_merge() -> None:
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)
    first = observation("frame_000000", [100, 20, 200, 250], class_name="bottle")
    second = observation("frame_000001", [100, 20, 200, 250], class_name="sports ball")
    # Retain the non-core observation so this test exercises association rather
    # than the separate scan-output filtering policy.
    bundle.algorithm["retain_non_core_scan_objects"] = True
    objects, _, metrics = aggregate_frames(
        [frame("frame_000000", 0, [first]), frame("frame_000001", 1, [second])],
        bundle,
    )
    assert len(objects) == 2
    assert {item["class_name"] for item in objects} == {"bottle", "sports ball"}
    assert metrics["association_group_count"] == 2


def test_ambiguous_association_does_not_merge() -> None:
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)
    frames = [frame("frame_000000", 0, [observation("frame_000000", [100, 20, 220, 250]), observation("frame_000000", [160, 20, 280, 250])]),
              frame("frame_000001", 1, [observation("frame_000001", [130, 20, 250, 250])])]
    objects, warnings, _ = aggregate_frames(frames, bundle)
    assert len(objects) == 3
    assert "W_MULTIFRAME_AMBIGUOUS_ASSOCIATION" in warnings


def test_non_core_filter_and_status() -> None:
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)
    result, warnings, _ = aggregate_frames([frame("frame_000000", 0, [observation("frame_000000", [1, 2, 3, 4], class_name="couch")])], bundle)
    assert result == [] and "W_NON_CORE_CLASS_FILTERED" in warnings
    assert determine_scan_status([frame("frame_000000", 0, [])], [], "unverified") == "success"


def test_atomic_json_and_strict_nonfinite(tmp_path: Path) -> None:
    path = tmp_path / "result.json"; atomic_write_json(path, {"value": 1}); assert load_strict_json(path, code="E") == {"value": 1}
    path.write_text('{"value": NaN}', encoding="utf-8")
    with pytest.raises(ScanContractError):
        load_strict_json(path, code="E_SCAN_SCHEMA")
