from __future__ import annotations

import json

import pytest

from deltar_p4_pipeline.scan_service_contracts import (
    ServiceError, expected_fields, field_spec, load_json_bytes, scan_identity,
    validate_content_type, validate_filename,
)


def test_expected_one_five_and_eleven_frame_sets() -> None:
    assert len(expected_fields(1)) == 4
    assert len(expected_fields(5)) == 16
    assert len(expected_fields(11)) == 34
    assert "frame_000004_image_rgb565" in expected_fields(5)
    assert "frame_000010_image_rgb565" in expected_fields(11)


def test_field_mapping_is_server_controlled() -> None:
    assert field_spec("frame_000003_tof_json")[0] == "frames/frame_000003/tof.json"
    with pytest.raises(ServiceError) as caught:
        field_spec("../../scan_json")
    assert caught.value.code == "E_MULTIPART_FIELD"


@pytest.mark.parametrize("filename", ["../scan.json", r"x\scan.json", "/scan.json", "other.json"])
def test_filename_traversal_and_mismatch_rejected(filename: str) -> None:
    with pytest.raises(ServiceError) as caught:
        validate_filename("scan_json", filename)
    assert caught.value.code == "E_PART_FILENAME"


def test_content_types_are_strict() -> None:
    validate_content_type("scan_json", None)
    validate_content_type("scan_json", "application/json")
    validate_content_type("frame_000000_image_rgb565", "application/octet-stream")
    with pytest.raises(ServiceError):
        validate_content_type("frame_000000_image_rgb565", "image/png")


def test_strict_json_rejects_nonfinite_and_nonobject() -> None:
    for raw in (b'{"x":NaN}', b"[]", b"\xff"):
        with pytest.raises(ServiceError):
            load_json_bytes(raw)


@pytest.mark.parametrize("count,mode", [(1, "multi_frame"), (2, "single_frame"), (0, "multi_frame"), (12, "multi_frame")])
def test_scan_identity_rejects_bad_count_or_mode(count: int, mode: str) -> None:
    with pytest.raises(ServiceError):
        scan_identity({"scan_id": "safe", "frame_count": count, "capture_mode": mode})


def test_scan_identity_accepts_frozen_modes() -> None:
    assert scan_identity({"scan_id": "safe_1", "frame_count": 1, "capture_mode": "single_frame"}) == ("safe_1", 1)
    assert scan_identity({"scan_id": "safe_5", "frame_count": 5, "capture_mode": "multi_frame"}) == ("safe_5", 5)
    assert scan_identity({"scan_id": "safe_10", "frame_count": 10, "capture_mode": "multi_frame"}) == ("safe_10", 10)
    assert scan_identity({"scan_id": "safe_11", "frame_count": 11, "capture_mode": "multi_frame"}) == ("safe_11", 11)
