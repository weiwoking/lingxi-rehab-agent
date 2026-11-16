from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from deltar_p4_pipeline.scan_service_contracts import ServiceError, scan_identity
from deltar_p4_pipeline.scan_service_worker import WorkerConfig, validate_scan as validate_service_scan
from tests.test_stage6_scan import make_scan


ROOT = Path(__file__).resolve().parents[1]


def config() -> WorkerConfig:
    return WorkerConfig(
        pipeline_root=ROOT,
        worker_python=Path("python"),
        profile_bundle=ROOT / "profiles",
    )


def validate(scan: Path) -> dict:
    return validate_service_scan(config(), scan)


def mutate_json(path: Path, mutation) -> None:
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_authoritative_service_validation_accepts_normal_11_frames(tmp_path: Path) -> None:
    scan = make_scan(tmp_path, frames=11)
    result = validate(scan)
    assert result["returncode"] == 0
    assert result["frame_count"] == 11
    assert result["subprocess_startup"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda scan: shutil.rmtree(scan / "frames" / "frame_000010"),
        lambda scan: (scan / "scan.json").write_text("{", encoding="utf-8"),
        lambda scan: (scan / "frames" / "frame_000000" / "image.rgb565").write_bytes(b"bad"),
        lambda scan: mutate_json(
            scan / "frames" / "frame_000000" / "tof.json",
            lambda value: value["distance_mm"].pop(),
        ),
    ],
    ids=["fewer_frames", "malformed_manifest", "rgb565_size", "tof_count"],
)
def test_authoritative_service_validation_preserves_invalid_rejection(
    tmp_path: Path, mutation
) -> None:
    scan = make_scan(tmp_path, frames=11)
    mutation(scan)
    with pytest.raises(ServiceError) as caught:
        validate(scan)
    assert caught.value.code == "E_SCAN_VALIDATE"
    assert caught.value.status_code == 422


def test_authoritative_service_validation_rejects_more_than_service_limit(tmp_path: Path) -> None:
    scan = make_scan(tmp_path, frames=12)
    with pytest.raises(ServiceError) as caught:
        scan_identity(json.loads((scan / "scan.json").read_text(encoding="utf-8")))
    assert caught.value.code == "E_SCAN_JSON"
    assert caught.value.status_code == 422
