from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from deltar_p4_pipeline.scan_contracts import ScanContractError
from deltar_p4_pipeline.scan_profiles import load_profile_bundle
from deltar_p4_pipeline.scan_validator import validate_scan
from deltar_p4_pipeline.validation_receipt import (
    RECEIPT_FILENAME,
    build_validation_receipt,
    capture_input_manifest,
    load_receipted_scan,
    verify_validation_receipt,
    write_validation_receipt,
)
from tests.test_stage6_scan import make_scan


ROOT = Path(__file__).resolve().parents[1]


def receipted_scan(tmp_path: Path, *, frames: int = 1) -> Path:
    scan = make_scan(tmp_path, frames=frames)
    bundle = load_profile_bundle(ROOT / "profiles", validate_model_hashes=False)
    validated = validate_scan(scan, bundle)
    manifest = capture_input_manifest(scan)
    receipt = build_validation_receipt(
        scan,
        scan_id=validated.scan["scan_id"],
        expected_frame_count=validated.scan["frame_count"],
        authoritative_manifest=manifest,
    )
    write_validation_receipt(scan / RECEIPT_FILENAME, receipt)
    return scan


def mutate_receipt(scan: Path, mutation) -> None:
    path = scan / RECEIPT_FILENAME
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_receipt_proves_exact_authoritatively_validated_input(tmp_path: Path) -> None:
    scan = receipted_scan(tmp_path, frames=2)
    receipt = verify_validation_receipt(scan, expected_scan_id="scan_test", expected_frame_count=2)
    loaded = load_receipted_scan(scan, expected_scan_id="scan_test")
    assert receipt["validation_success"] is True
    assert len(loaded.frames) == 2
    assert loaded.rejected_frames == ()


def test_receipt_missing_is_fail_closed(tmp_path: Path) -> None:
    scan = make_scan(tmp_path)
    with pytest.raises(ScanContractError, match="E_VALIDATION_RECEIPT_MISSING"):
        verify_validation_receipt(scan)


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda value: value.update(schema_version="wrong"), "E_VALIDATION_RECEIPT_SCHEMA"),
        (lambda value: value.update(scan_id="another_scan"), "E_VALIDATION_RECEIPT_DIGEST"),
        (lambda value: value.update(receipt_sha256="0" * 64), "E_VALIDATION_RECEIPT_DIGEST"),
    ],
)
def test_receipt_tampering_is_fail_closed(tmp_path: Path, mutation, code: str) -> None:
    scan = receipted_scan(tmp_path)
    mutate_receipt(scan, mutation)
    with pytest.raises(ScanContractError, match=code):
        verify_validation_receipt(scan)


def test_receipt_rejects_file_content_change_with_same_size_and_mtime(tmp_path: Path) -> None:
    scan = receipted_scan(tmp_path)
    image = scan / "frames" / "frame_000000" / "image.rgb565"
    stat = image.stat()
    with image.open("r+b") as handle:
        handle.seek(0)
        handle.write(b"\x01")
    os.utime(image, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(ScanContractError, match="E_VALIDATION_RECEIPT_IDENTITY"):
        verify_validation_receipt(scan)


def test_receipt_rejects_file_size_or_mtime_change(tmp_path: Path) -> None:
    scan = receipted_scan(tmp_path)
    tof = scan / "frames" / "frame_000000" / "tof.json"
    tof.write_bytes(tof.read_bytes() + b" ")
    with pytest.raises(ScanContractError, match="E_VALIDATION_RECEIPT_IDENTITY"):
        verify_validation_receipt(scan)


def test_receipt_cannot_be_reused_for_another_root(tmp_path: Path) -> None:
    first = receipted_scan(tmp_path / "first")
    second = make_scan(tmp_path / "second")
    (second / RECEIPT_FILENAME).write_bytes((first / RECEIPT_FILENAME).read_bytes())
    with pytest.raises(ScanContractError, match="E_VALIDATION_RECEIPT_IDENTITY"):
        verify_validation_receipt(second)


def test_receipt_rejects_unbound_extra_input_file(tmp_path: Path) -> None:
    scan = receipted_scan(tmp_path)
    (scan / "frames" / "frame_000000" / "extra.bin").write_bytes(b"unexpected")
    with pytest.raises(ScanContractError, match="E_VALIDATION_RECEIPT_IDENTITY"):
        verify_validation_receipt(scan)
