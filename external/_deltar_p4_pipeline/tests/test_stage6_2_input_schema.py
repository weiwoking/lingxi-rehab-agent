from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from deltar_p4_pipeline.contracts import validate_tof
from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.scan_contracts import PROFILE_IDS, ScanContractError, require_rfc3339_datetime
from deltar_p4_pipeline.scan_validator import validate_frame_document, validate_scan_document
from tests.test_stage6_scan import make_scan
from deltar_p4_pipeline.scan_validator import validate_scan

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "stage6_2_input_contract"


def _load(group: str, name: str) -> dict:
    return json.loads((FIXTURES / group / name).read_text(encoding="utf-8"))


def test_valid_contract_documents() -> None:
    assert validate_scan_document(_load("valid", "scan.json"))["profiles"] == PROFILE_IDS
    assert validate_frame_document(_load("valid", "frame.json"))["sequence"] == 0
    assert validate_tof(_load("valid", "tof.json"), max_sequence=63)["distance_mm"][0] == 1200


@pytest.mark.parametrize(
    "name,validator,error",
    [
        ("frame_unknown_field.json", validate_frame_document, ScanContractError),
        ("frame_missing_image.json", validate_frame_document, ScanContractError),
        ("frame_bad_resolution.json", validate_frame_document, ScanContractError),
        ("tof_63_items.json", validate_tof, PipelineError),
        ("tof_65_items.json", validate_tof, PipelineError),
        ("tof_unknown_field.json", validate_tof, PipelineError),
        ("scan_bad_datetime.json", validate_scan_document, ScanContractError),
        ("scan_naive_datetime.json", validate_scan_document, ScanContractError),
        ("scan_unknown_profile.json", validate_scan_document, ScanContractError),
        ("scan_path_id.json", validate_scan_document, ScanContractError),
    ],
)
def test_invalid_fixture_rejected(name: str, validator, error) -> None:
    with pytest.raises(error):
        validator(_load("invalid", name), max_sequence=63) if validator is validate_tof else validator(_load("invalid", name))


@pytest.mark.parametrize("value", ["2026-07-18T15:30:45Z", "2026-07-18T15:30:45+08:00"])
def test_created_at_timezone_accepted(value: str) -> None:
    assert require_rfc3339_datetime(value, "created_at", code="E_SCAN_SCHEMA") == value


@pytest.mark.parametrize("value", ["2026-07-18", "2026-07-18T15:30:45", "ordinary text", 1, float("inf")])
def test_created_at_invalid_rejected(value) -> None:
    with pytest.raises(ScanContractError):
        require_rfc3339_datetime(value, "created_at", code="E_SCAN_SCHEMA")


@pytest.mark.parametrize("scan_id", [".", "..", "a/b", "a\\b", "has space", "C:\\absolute"])
def test_scan_id_path_forms_rejected(scan_id: str) -> None:
    value = _load("valid", "scan.json")
    value["scan_id"] = scan_id
    with pytest.raises(ScanContractError):
        validate_scan_document(value)


@pytest.mark.parametrize("field", ["filename", "width", "height", "pixel_format", "stride_bytes", "payload_bytes"])
def test_image_fixed_values_rejected(field: str) -> None:
    value = _load("valid", "frame.json")
    value["image"][field] = "wrong" if field in {"filename", "pixel_format"} else value["image"][field] + 1
    with pytest.raises(ScanContractError):
        validate_frame_document(value)


@pytest.mark.parametrize(
    "field,bad",
    [("distance_mm", -1), ("distance_mm", 65536), ("range_sigma_mm", 65536), ("target_status", 256), ("nb_target_detected", 256)],
)
def test_tof_numeric_ranges_rejected(field: str, bad: int) -> None:
    value = _load("valid", "tof.json")
    value[field][0] = bad
    with pytest.raises(PipelineError):
        validate_tof(value, max_sequence=63)


def test_cross_file_runtime_rejections(tmp_path: Path) -> None:
    for mutation in ("pair", "sequence", "timestamp"):
        scan = make_scan(tmp_path / mutation)
        frame_path = scan / "frames" / "frame_000000" / "frame.json"
        tof_path = scan / "frames" / "frame_000000" / "tof.json"
        frame = json.loads(frame_path.read_text(encoding="utf-8"))
        tof = json.loads(tof_path.read_text(encoding="utf-8"))
        if mutation == "pair":
            frame["pair_delta_us"] = 1
        elif mutation == "sequence":
            frame["sequence"] = 1
            tof["sequence"] = 1
        else:
            tof["timestamp_us"] = 1
        frame_path.write_text(json.dumps(frame), encoding="utf-8")
        tof_path.write_text(json.dumps(tof), encoding="utf-8")
        with pytest.raises(ScanContractError):
            validate_scan(scan)
