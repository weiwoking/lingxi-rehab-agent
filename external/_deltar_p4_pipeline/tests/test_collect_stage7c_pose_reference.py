from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest


TOOL = Path(__file__).resolve().parents[1] / "tools" / "collect_stage7c_from_scan.py"
SPEC = importlib.util.spec_from_file_location("collect_stage7c_pose_reference", TOOL)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def rotation_y(degrees: float) -> np.ndarray:
    return cv2.Rodrigues(
        np.asarray([0.0, np.deg2rad(degrees), 0.0], dtype=np.float64)
    )[0]


def contracts() -> dict[str, object]:
    return {
        "board": object(),
        "board_manifest_sha256": "board",
        "camera": {},
        "camera_sha256": "stage7a",
        "stage7b_sha256": "stage7b",
        "stage7c_sha256": "stage7c",
    }


def make_mid_session(root: Path, count: int = 5) -> None:
    collections = []
    for index in range(count):
        compatibility = root / "mid" / f"stage7c_mid_scan_{index}"
        frame = compatibility / "frames" / "frame_000000"
        frame.mkdir(parents=True)
        (frame / "image.rgb565").write_bytes(bytes([index]) * 32)
        collections.append(
            {
                "role": "mid",
                "scan_id": f"scan_{index}",
                "selected_frame_id": f"frame_{index:06d}",
                "compatibility_scan_dirs": [str(compatibility)],
            }
        )
    (root / "session.json").write_text(
        json.dumps({"collections": collections}), encoding="utf-8"
    )


def patch_reference_detection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        collector, "_camera_parameters", lambda _camera: (np.eye(3), np.zeros(5))
    )
    monkeypatch.setattr(
        collector,
        "read_rgb565_le",
        lambda _path: np.zeros((2, 2, 3), dtype=np.uint8),
    )
    monkeypatch.setattr(
        collector, "detect_observation_image", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(
        collector,
        "solve_board_pose",
        lambda *_args, **_kwargs: (np.eye(3), np.zeros(3), {}),
    )
    monkeypatch.setattr(
        collector,
        "rotation_world_from_camera_from_board_pose",
        lambda rotation: rotation,
    )


def test_mid_fewer_than_five_is_unavailable_without_failure(tmp_path: Path) -> None:
    make_mid_session(tmp_path, count=4)
    result = collector.load_or_refresh_mid_pose_reference(tmp_path, contracts())
    assert result == {
        "availability": "UNAVAILABLE",
        "reason": "requires_mid_5_of_5",
        "mid_sample_count": 4,
    }


def test_mid_five_reference_is_generated_with_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_mid_session(tmp_path)
    patch_reference_detection(monkeypatch)
    result = collector.load_or_refresh_mid_pose_reference(tmp_path, contracts())
    path = tmp_path / "analysis" / "mid_pose_reference.json"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert result["availability"] == "AVAILABLE"
    assert result["cache_status"] == "REBUILT"
    assert persisted["sample_count"] == 5
    assert persisted["aggregation"]["inlier_count"] == 5
    assert persisted["aggregation"]["rejected_count"] == 0
    assert persisted["board_manifest_sha256"] == "board"
    assert persisted["stage7a_profile_sha256"] == "stage7a"
    assert persisted["stage7b_profile_sha256"] == "stage7b"
    assert persisted["formal_stage7c_sha256"] == "stage7c"
    assert len(persisted["source_samples"]) == 5


def test_mid_source_change_rebuilds_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_mid_session(tmp_path)
    patch_reference_detection(monkeypatch)
    first = collector.load_or_refresh_mid_pose_reference(tmp_path, contracts())
    image = (
        tmp_path
        / "mid"
        / "stage7c_mid_scan_4"
        / "frames"
        / "frame_000000"
        / "image.rgb565"
    )
    image.write_bytes(b"changed source")
    second = collector.load_or_refresh_mid_pose_reference(tmp_path, contracts())
    assert first["source_session_fingerprint"] != second["source_session_fingerprint"]
    assert second["cache_status"] == "REBUILT"


def test_so3_geodesic_and_pose_errors_are_correct() -> None:
    reference = {
        "availability": "AVAILABLE",
        "sample_count": 5,
        "source_session_fingerprint": "fingerprint",
        "rotation_world_from_camera_reference": np.eye(3).tolist(),
    }
    frames = [
        {
            "frame_id": f"frame_{index:06d}",
            "detector_pass": True,
            "rotation_world_from_camera": rotation_y(2.0).tolist(),
        }
        for index in range(5)
    ]
    inspection = {"selected_frame_id": "frame_000000", "frames": frames}
    result = collector.compute_pose_reference_diagnostic(inspection, reference)
    assert collector.rotation_distance_deg(np.eye(3), rotation_y(2.0)) == pytest.approx(2.0)
    assert result["selected_frame_error_vs_mid_deg"] == pytest.approx(2.0)
    assert result["scan_robust_pose_error_vs_mid_deg"] == pytest.approx(2.0)
    assert result["relative_orientation"]["relative_rotvec_deg"] == pytest.approx(
        [0.0, 2.0, 0.0]
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (0.8, "GOOD"),
        (0.8000001, "WITHIN_1_DEG"),
        (1.0, "WITHIN_1_DEG"),
        (1.0000001, "ADJUST"),
    ],
)
def test_pose_diagnostic_status_boundaries(error: float, expected: str) -> None:
    assert collector.pose_diagnostic_status(error) == expected


def test_pose_diagnostic_does_not_mutate_selection_or_materialization() -> None:
    reference = {
        "availability": "AVAILABLE",
        "sample_count": 5,
        "source_session_fingerprint": "fingerprint",
        "rotation_world_from_camera_reference": np.eye(3).tolist(),
    }
    inspection = {
        "status": "GPT_REVIEW_REQUIRED",
        "hard_gate_status": "PASS",
        "usable_frame_count": 1,
        "selected_frame_id": "frame_000000",
        "ranking": [{"frame_id": "frame_000000", "rank": 1}],
        "materialization": [{"file": "image.rgb565", "method": "hardlink"}],
        "frames": [
            {
                "frame_id": "frame_000000",
                "detector_pass": True,
                "rotation_world_from_camera": np.eye(3).tolist(),
            }
        ],
    }
    before = copy.deepcopy(inspection)
    result = collector.compute_pose_reference_diagnostic(inspection, reference)
    assert result["diagnostic_only"] is True
    assert result["affects_ranking"] is False
    assert result["affects_acceptance"] is False
    assert result["affects_materialization"] is False
    assert inspection == before


def test_pose_diagnostic_failure_is_isolated_from_collection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise RuntimeError("synthetic diagnostic failure")

    monkeypatch.setattr(collector, "load_or_refresh_mid_pose_reference", fail)
    inspection = {"selected_frame_id": "frame_000001", "ranking": ["unchanged"]}
    before = copy.deepcopy(inspection)
    result = collector.safe_pose_reference_diagnostic(
        tmp_path, contracts(), inspection
    )
    assert result["availability"] == "UNAVAILABLE"
    assert result["reason"] == "pose_reference_diagnostic_failed"
    assert result["affects_acceptance"] is False
    assert inspection == before
