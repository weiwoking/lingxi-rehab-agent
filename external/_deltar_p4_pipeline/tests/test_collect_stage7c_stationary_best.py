from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np
import pytest


TOOL = Path(__file__).resolve().parents[1] / "tools" / "collect_stage7c_from_scan.py"
SPEC = importlib.util.spec_from_file_location("collect_stage7c_from_scan", TOOL)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)


def row(
    sequence: int,
    *,
    corners: int = 24,
    markers: int = 17,
    rms: float = 0.15,
    pair_delta: int = 1000,
    rotation: np.ndarray | None = None,
    detector_pass: bool = True,
) -> dict[str, object]:
    return {
        "frame_id": f"frame_{sequence:06d}",
        "sequence": sequence,
        "direction_index": sequence,
        "charuco_corner_count": corners,
        "detected_marker_count": markers,
        "reprojection_rms_px": rms,
        "pair_delta_us": pair_delta,
        "detector_pass": detector_pass,
        "usable": detector_pass,
        "rejection_reason": None if detector_pass else "synthetic_single_frame_fail",
        "rotation_world_from_camera": (
            np.eye(3) if rotation is None else rotation
        ).tolist(),
    }


def rotation_y(degrees: float) -> np.ndarray:
    rvec = np.asarray([0.0, np.deg2rad(degrees), 0.0], dtype=np.float64)
    return cv2.Rodrigues(rvec)[0]


def test_legacy_direction0_mode_is_unchanged() -> None:
    assert collector.frame_is_usable(
        detector_pass=True,
        is_frozen_11=True,
        direction_index=0,
        stationary_best=False,
    )
    assert not collector.frame_is_usable(
        detector_pass=True,
        is_frozen_11=True,
        direction_index=1,
        stationary_best=False,
    )


def test_stationary_best_all_pass_still_selects_one() -> None:
    rows = [row(index, rms=0.20 - index * 0.001) for index in range(11)]
    stationary, ranking, chosen = collector.select_stationary_best(rows)
    assert stationary["status"] == "PASS"
    assert len(ranking) == 11
    assert chosen is ranking[0]
    assert sum(bool(item["usable"]) for item in rows) == 1
    assert ranking[0]["frame_id"] == "frame_000010"


def test_stationary_best_rejects_non_11_frame_input() -> None:
    with pytest.raises(
        collector.CollectorError, match="STATIONARY_SCAN_FRAME_COUNT_MISMATCH"
    ):
        collector.select_stationary_best([row(index) for index in range(10)])


def test_best_of_11_ten_fail_one_pass_selects_the_one_pass() -> None:
    rows = [row(index, detector_pass=False) for index in range(10)]
    rows.append(row(10, rms=0.49))
    ranking, chosen = collector.select_best_of_11(rows)
    assert chosen is rows[10]
    assert ranking == [rows[10]]
    assert sum(bool(item["usable"]) for item in rows) == 1


def test_best_of_11_bad_frames_do_not_influence_good_frame() -> None:
    rows = [
        row(index, detector_pass=False, rotation=rotation_y(index * 30.0))
        for index in range(10)
    ]
    rows.append(row(10, rotation=rotation_y(170.0)))
    ranking, chosen = collector.select_best_of_11(rows)
    assert chosen is ranking[0]
    assert chosen["frame_id"] == "frame_000010"


def test_best_of_11_all_pass_selects_deterministic_rank_one() -> None:
    rows = [row(index, rms=0.20 - index * 0.001) for index in range(11)]
    ranking, chosen = collector.select_best_of_11(rows)
    assert len(ranking) == 11
    assert chosen is ranking[0]
    assert chosen["frame_id"] == "frame_000010"


def test_best_of_11_has_no_cross_frame_stationary_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [row(index, rotation=rotation_y(index * 17.0)) for index in range(11)]

    def unexpected_stationary_gate(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("cross-frame stationary gate must not run")

    monkeypatch.setattr(
        collector, "evaluate_stationary_consistency", unexpected_stationary_gate
    )
    ranking, chosen = collector.select_best_of_11(rows)
    assert chosen is ranking[0]


def test_best_of_11_zero_pass_has_no_selection() -> None:
    rows = [row(index, detector_pass=False) for index in range(11)]
    ranking, chosen = collector.select_best_of_11(rows)
    assert ranking == []
    assert chosen is None
    assert not any(bool(item["usable"]) for item in rows)


def test_ranking_is_deterministic_and_lexicographic() -> None:
    rows = [
        row(9, corners=23, markers=17, rms=0.01),
        row(8, corners=24, markers=16, rms=0.01),
        row(7, corners=24, markers=17, rms=0.30),
        row(6, corners=24, markers=17, rms=0.20, pair_delta=2000),
        row(5, corners=24, markers=17, rms=0.20, pair_delta=1000),
        row(4, corners=24, markers=17, rms=0.20, pair_delta=1000),
    ]
    expected = [4, 5, 6, 7, 8, 9]
    first = [item["sequence"] for item in collector.rank_stationary_frames(rows)]
    second = [item["sequence"] for item in collector.rank_stationary_frames(rows)]
    assert first == expected
    assert second == expected


def test_movement_fails_stationary_gate() -> None:
    rows = [row(index, rotation=rotation_y(0.02 * index)) for index in range(10)]
    rows.append(row(10, rotation=rotation_y(8.0)))
    stationary = collector.evaluate_stationary_consistency(rows)
    assert stationary["status"] == "FAIL"
    assert stationary["failure_code"] == "STATIONARY_POSE_CONSISTENCY_FAIL"


def test_duplicate_scan_id_is_rejected() -> None:
    session = {"collections": [{"scan_id": "scan_same"}]}
    with pytest.raises(collector.CollectorError, match="DUPLICATE_SCAN_ID_REJECTED"):
        collector.reject_duplicate_scan_id(session, "scan_same")


def test_backup_is_complete_and_new_session_starts_empty(tmp_path: Path) -> None:
    source = tmp_path / "stage7c_recalibration_20260813"
    (source / "logs").mkdir(parents=True)
    (source / "manifest").mkdir()
    compatibility = source / "near" / "stage7c_near_old"
    compatibility.mkdir(parents=True)
    (source / "session.json").write_text(
        json.dumps(
            {
                "collection_policy": {"accepted_direction_index": 0},
                "collections": [{"scan_id": "old"}],
            }
        ),
        encoding="utf-8",
    )
    (source / "logs" / "old_collector.json").write_text("legacy", encoding="utf-8")
    (source / "manifest" / "old_scan.json").write_text("manifest", encoding="utf-8")
    (compatibility / "scan.json").write_text("compatibility", encoding="utf-8")
    before = {
        path.relative_to(source): collector.file_sha256(path)
        for path in source.rglob("*")
        if path.is_file()
    }

    result = collector.backup_existing_direction0_session(
        source, timestamp="20990101_010203"
    )
    target = Path(result["target"])
    after = {
        path.relative_to(target): collector.file_sha256(path)
        for path in target.rglob("*")
        if path.is_file()
    }
    assert not source.exists()
    assert before == after
    assert result["before"]["key_sha256"]["near\\stage7c_near_old\\scan.json"]
    assert result["before"]["key_sha256"] == result["after"]["key_sha256"]

    contracts = {
        "board_manifest": {
            "board_id": "test",
            **collector.EXPECTED_BOARD_FIELDS,
        },
        "board_path": tmp_path / "board_manifest.json",
        "board_image": tmp_path / "board.png",
        "camera_path": tmp_path / "camera.json",
        "camera": {"profile_id": "camera"},
        "camera_sha256": "a",
        "stage7b_path": tmp_path / "stage7b.json",
        "stage7b": {"profile_id": "stage7b"},
        "stage7b_sha256": "b",
        "stage7c_path": tmp_path / "stage7c.json",
        "stage7c": {"profile_id": "stage7c"},
        "stage7c_sha256": "c",
    }
    contracts["board_path"].write_text("{}", encoding="utf-8")
    contracts["board_image"].write_bytes(b"png")
    session = collector.load_or_create_session(
        source,
        contracts,
        selection_mode=collector.SELECTION_STATIONARY_BEST,
    )
    assert session["collections"] == []
    assert session["usable_counts"] == {"near": 0, "mid": 0, "far": 0}
    assert session["status"]["total"] == "0/15"


def test_source_hash_is_not_changed_by_reading(tmp_path: Path) -> None:
    source = tmp_path / "image.rgb565"
    source.write_bytes(bytes(range(256)) * 8)
    before = collector.file_sha256(source)
    assert source.read_bytes()
    assert collector.file_sha256(source) == before


def test_calibrate_session_reopens_stationary_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_root = tmp_path / "session"
    session_root.mkdir()
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "collection_policy": {
                    "selection_mode": collector.SELECTION_BEST_OF_11
                }
            }
        ),
        encoding="utf-8",
    )
    observed: dict[str, str] = {}

    def fake_load(
        root: Path,
        contracts: dict[str, object],
        *,
        selection_mode: str,
    ) -> dict[str, object]:
        observed["selection_mode"] = selection_mode
        return {"collections": []}

    monkeypatch.setattr(collector, "load_or_create_session", fake_load)
    args = type("Args", (), {"session_root": session_root})()
    with pytest.raises(collector.CollectorError, match="role near has 0 usable frames"):
        collector.calibrate_session(args, {})
    assert observed["selection_mode"] == collector.SELECTION_BEST_OF_11


def test_empty_stationary_session_safely_upgrades_to_best_of_11(
    tmp_path: Path,
) -> None:
    session_root = tmp_path / "session"
    session_root.mkdir()
    contract = {"test": "contract"}
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "contract": contract,
                "collection_policy": {
                    "selection_mode": collector.SELECTION_STATIONARY_BEST,
                    "accepted_direction_index": None,
                },
                "collections": [],
                "usable_counts": {"near": 0, "mid": 0, "far": 0},
                "status": {
                    "near": "0/5", "mid": "0/5", "far": "0/5", "total": "0/15"
                },
            }
        ),
        encoding="utf-8",
    )
    monkey_contracts: dict[str, object] = {}
    original = collector.contract_record
    collector.contract_record = lambda _contracts: contract
    try:
        session = collector.load_or_create_session(
            session_root,
            monkey_contracts,
            selection_mode=collector.SELECTION_BEST_OF_11,
        )
    finally:
        collector.contract_record = original
    policy = session["collection_policy"]
    assert policy["selection_mode"] == collector.SELECTION_BEST_OF_11
    assert policy["cross_frame_pose_gate"] == collector.CROSS_FRAME_POSE_GATE_DISABLED
    assert session["collections"] == []
    assert session["status"]["total"] == "0/15"


def test_nonempty_stationary_session_cannot_be_auto_upgraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_root = tmp_path / "session"
    session_root.mkdir()
    contract = {"test": "contract"}
    (session_root / "session.json").write_text(
        json.dumps(
            {
                "contract": contract,
                "collection_policy": {
                    "selection_mode": collector.SELECTION_STATIONARY_BEST
                },
                "collections": [{"scan_id": "already_collected"}],
                "usable_counts": {"near": 1, "mid": 0, "far": 0},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(collector, "contract_record", lambda _contracts: contract)
    with pytest.raises(collector.CollectorError, match="selection mode differs"):
        collector.load_or_create_session(
            session_root,
            {},
            selection_mode=collector.SELECTION_BEST_OF_11,
        )
