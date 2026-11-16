from __future__ import annotations

from types import SimpleNamespace

import pytest

from deltar_p4_pipeline.tof_degraded import build_tof_decisions, formal_valid_count


def _frame(
    sequence: int,
    *,
    camera_us: int,
    tof_us: int,
    statuses: list[int],
    counts: list[int],
    yaw: float | None = 0.0,
    x_mm: float = 0.0,
) -> SimpleNamespace:
    frame = {
        "frame_id": f"frame_{sequence:06d}",
        "sequence": sequence,
        "camera_timestamp_us": camera_us,
    }
    if yaw is not None:
        frame["capture_pose"] = {"yaw_deg": yaw, "translation_mm": [x_mm, 0.0, 0.0]}
    return SimpleNamespace(
        frame=frame,
        tof={
            "timestamp_us": tof_us,
            "target_status": statuses,
            "nb_target_detected": counts,
            "distance_mm": [1000.0] * len(statuses),
        },
    )


def test_formal_valid_statuses_include_status_10_and_sparse_current_is_degraded() -> None:
    tof = {"target_status": [5, 6, 9, 10, 4], "nb_target_detected": [1, 1, 1, 1, 9]}
    assert formal_valid_count(tof) == 4
    decision = build_tof_decisions([
        _frame(0, camera_us=1_000_000, tof_us=1_000_000, statuses=[10], counts=[1])
    ])["frame_000000"]
    assert decision["tof_status"] == "TOF_DEGRADED"
    assert decision["source"] == "current"
    assert decision["tof"] is not None


def test_safe_nearest_tof_is_borrowed_with_age_and_pose_gates() -> None:
    source = _frame(0, camera_us=1_000_000, tof_us=1_000_000, statuses=[5] * 8, counts=[1] * 8)
    missing = _frame(1, camera_us=1_050_000, tof_us=1_050_000, statuses=[0], counts=[0], yaw=1.0, x_mm=10.0)
    decision = build_tof_decisions([source, missing])["frame_000001"]
    assert decision["tof_status"] == "TOF_BORROWED"
    assert decision["borrowed_from_frame_id"] == "frame_000000"
    assert decision["borrowed_age_ms"] == pytest.approx(50.0)
    assert decision["pose_gate"]["pose_available"] is True


@pytest.mark.parametrize(
    ("camera_us", "yaw", "x_mm", "reason"),
    [
        (1_150_000, 0.0, 0.0, "nearest_tof_age_gate_failed"),
        (1_050_000, 3.0, 0.0, "nearest_tof_pose_gate_failed"),
        (1_050_000, 0.0, 30.0, "nearest_tof_pose_gate_failed"),
        (1_050_000, None, 0.0, "nearest_tof_pose_gate_failed"),
    ],
)
def test_unsafe_borrow_is_rgb_only_not_a_dropped_capture(
    camera_us: int, yaw: float | None, x_mm: float, reason: str
) -> None:
    source = _frame(0, camera_us=1_000_000, tof_us=1_000_000, statuses=[5] * 8, counts=[1] * 8)
    missing = _frame(1, camera_us=camera_us, tof_us=camera_us, statuses=[0], counts=[0], yaw=yaw, x_mm=x_mm)
    decision = build_tof_decisions([source, missing])["frame_000001"]
    assert decision["tof_status"] == "TOF_UNAVAILABLE"
    assert decision["degradation_mode"] == "RGB_ONLY_GEOMETRY_UNAVAILABLE"
    assert decision["tof"] is None
    assert "no_safe_tof" in decision["reason_codes"]
    assert reason in decision["reason_codes"]


def test_current_partial_tof_is_never_replaced_by_a_neighbour() -> None:
    rich = _frame(0, camera_us=1_000_000, tof_us=1_000_000, statuses=[5] * 16, counts=[1] * 16)
    sparse = _frame(1, camera_us=1_010_000, tof_us=1_010_000, statuses=[10], counts=[1])
    decision = build_tof_decisions([rich, sparse])["frame_000001"]
    assert decision["source"] == "current"
    assert decision["tof"] is sparse.tof
    assert decision["tof_status"] == "TOF_DEGRADED"


def test_finite_raw_status_invalid_tof_is_explicit_degraded_raw_numeric_path() -> None:
    frame = _frame(
        0, camera_us=1_000_000, tof_us=1_000_000,
        statuses=[255] * 64, counts=[0] * 64,
    )
    frame.tof["range_sigma_mm"] = [20.0] * 64
    decision = build_tof_decisions([frame])["frame_000000"]
    assert decision["tof_status"] == "TOF_DEGRADED_RAW"
    assert decision["source"] == "degraded_raw"
    assert decision["formal_valid_count"] == 0
    assert decision["raw_metric_count"] == 64
    assert decision["confidence"] == pytest.approx(0.05)
    assert decision["tof"]["distance_mm"] == frame.tof["distance_mm"]
    assert set(decision["tof"]["target_status"]) == {10}
    assert set(decision["tof"]["nb_target_detected"]) == {1}
