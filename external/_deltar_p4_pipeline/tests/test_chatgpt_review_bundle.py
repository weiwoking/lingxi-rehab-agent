from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from deltar_p4_pipeline import chatgpt_review_bundle as bundle
from deltar_p4_pipeline.chatgpt_review_bundle import (
    ReviewBundleError,
    build_chatgpt_review_bundle,
    discover_bundle_sources,
    validate_review_zip,
)
from deltar_p4_pipeline.scan_service_storage import ServiceStorage
from deltar_p4_pipeline.scan_service_worker import (
    WorkerConfig,
    publish_terminal_review_bundle,
    run_pipeline,
)


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _scan_fixture(root: Path, *, previews: int = 11, status: str = "completed") -> Path:
    result = root / "scan_fixture_000"
    _write_json(
        result / "job_report.json",
        {
            "scan_id": result.name,
            "provenance": {
                "pipeline_version": "6.1.0",
                "profile_hashes": {"algorithm_profile.json": "a" * 64},
            },
        },
    )
    _write_json(
        result / "stage8b_all_object_geometry.v1.json",
        {
            "scan_id": result.name,
            "profile": {
                "profile_id": "formal_profile",
                "camera_authority": "STAGE2",
                "world_z_authority": "STAGE3A_GRAVITY",
                "geometry_authority": "ALLOBJECT_V2",
                "promoted_payload_set_sha256": "b" * 64,
            },
        },
    )
    _write_json(
        result / "stage8b4a_p4_decision_package.v1.json",
        {"scan_id": result.name, "status": "ready"},
    )
    _write_json(result / "result.json", {"scan_id": result.name, "status": "success"})
    _write_json(
        result / "service_job.json",
        {"scan_id": result.name, "status": status, "error": None},
    )
    progress = result / "logs" / "pipeline_progress.jsonl"
    progress.parent.mkdir(parents=True, exist_ok=True)
    progress.write_text('{"status":"completed"}\n', encoding="utf-8")
    for index in range(previews):
        preview = result / "logs" / f"frame_{index:06d}" / "source_rgb.png"
        preview.parent.mkdir(parents=True, exist_ok=True)
        preview.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([index]) * 16)
    return result


def _manifest_from_zip(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("review_manifest.json"))


def test_success_bundle_allowlist_manifest_hashes_and_all_previews(tmp_path: Path) -> None:
    result = _scan_fixture(tmp_path)
    _write_json(result / "validation_receipt.v1.json", {"valid": True})
    outcome = build_chatgpt_review_bundle(result)

    assert outcome.zip_path == result / f"{result.name}_chatgpt_review.zip"
    manifest = validate_review_zip(outcome.zip_path)
    assert manifest["bundle_status"] == "full_success"
    assert manifest["pipeline_version"] == "6.1.0"
    assert manifest["profile_authority_identifiers"]["geometry_authority"] == "ALLOBJECT_V2"
    assert manifest["preview_policy"]["mode"] == "all"
    assert manifest["preview_policy"]["included_count"] == 11
    with zipfile.ZipFile(outcome.zip_path) as archive:
        names = archive.namelist()
        assert all(required in names for required in bundle.CORE_ARTIFACTS)
        assert "validation_receipt.v1.json" in names
        assert sum(name.startswith("previews/") for name in names) == 11
        for entry in manifest["included_files"]:
            payload = archive.read(entry["archive_path"])
            assert hashlib.sha256(payload).hexdigest() == entry["sha256"]


def test_terminal_failure_creates_partial_bundle_without_fabrication(tmp_path: Path) -> None:
    result = _scan_fixture(tmp_path, status="failed")
    (result / "result.json").unlink()
    _write_json(
        result / "service_job.json",
        {
            "scan_id": result.name,
            "status": "failed",
            "error": {"error_code": "E_TEST", "message": "worker failed"},
        },
    )
    outcome = build_chatgpt_review_bundle(result)
    manifest = outcome.manifest
    assert manifest["bundle_status"] == "partial_failure"
    assert manifest["terminal_failure_reason"] == "worker failed"
    assert "result.json" in manifest["missing_optional_files"]
    with zipfile.ZipFile(outcome.zip_path) as archive:
        assert "result.json" not in archive.namelist()


def test_optional_root_contract_discovery_is_small_and_secret_safe(tmp_path: Path) -> None:
    result = _scan_fixture(tmp_path, previews=0)
    _write_json(result / "scan_manifest.v2.json", {"ok": True})
    _write_json(result / "credentials_receipt.json", {"token": "do-not-copy"})
    (result / "oversized_status.json").write_bytes(b"x" * (bundle.MAX_OPTIONAL_FILE_BYTES + 1))
    sources, _, _ = discover_bundle_sources(result)
    paths = {source.source_relative_path for source in sources}
    assert "scan_manifest.v2.json" in paths
    assert "credentials_receipt.json" not in paths
    assert "oversized_status.json" not in paths


def test_preview_fallback_selects_first_middle_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _scan_fixture(tmp_path, previews=11)
    monkeypatch.setattr(bundle, "MAX_ALL_PREVIEWS_BYTES", 1)
    outcome = build_chatgpt_review_bundle(result)
    manifest = outcome.manifest
    assert manifest["preview_policy"]["mode"] == "representative_first_middle_last"
    assert manifest["preview_policy"]["included_count"] == 3
    with zipfile.ZipFile(outcome.zip_path) as archive:
        previews = sorted(name for name in archive.namelist() if name.startswith("previews/"))
    assert previews == [
        "previews/frame_000000_source_rgb.png",
        "previews/frame_000005_source_rgb.png",
        "previews/frame_000010_source_rgb.png",
    ]


@pytest.mark.parametrize(
    "relative",
    [
        "capture.rgb565",
        "tof_raw.bin",
        "depth.npy",
        "dense.npz",
        "model.pt",
        "checkpoint.ckpt",
        "old_review.zip",
    ],
)
def test_forbidden_artifacts_are_excluded(tmp_path: Path, relative: str) -> None:
    result = _scan_fixture(tmp_path, previews=0)
    target = result / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"forbidden")
    outcome = build_chatgpt_review_bundle(result)
    with zipfile.ZipFile(outcome.zip_path) as archive:
        assert relative not in archive.namelist()


def test_archive_path_traversal_and_duplicate_guards() -> None:
    with pytest.raises(ReviewBundleError):
        bundle._validate_archive_path("../escape.json")
    with pytest.raises(ReviewBundleError):
        bundle._validate_archive_path("C:/absolute.json")


def test_validator_rejects_duplicate_archive_paths(tmp_path: Path) -> None:
    path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("same.json", b"one")
        archive.writestr("same.json", b"two")
        archive.writestr("review_manifest.json", b"{}")
    with pytest.raises(ReviewBundleError, match="duplicate"):
        validate_review_zip(path)


def test_atomic_replace_and_idempotent_regeneration(tmp_path: Path) -> None:
    result = _scan_fixture(tmp_path)
    first = build_chatgpt_review_bundle(result)
    first.zip_path.write_bytes(b"old invalid zip")
    second = build_chatgpt_review_bundle(result)
    assert second.zip_path == first.zip_path
    assert validate_review_zip(second.zip_path)["scan_id"] == result.name
    assert not list(result.glob(f"{result.name}_chatgpt_review_*.zip"))
    assert not list(result.glob(".*.tmp"))


def test_broken_candidate_is_not_published_over_existing_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _scan_fixture(tmp_path)
    path = build_chatgpt_review_bundle(result).zip_path
    before = path.read_bytes()
    monkeypatch.setattr(
        bundle,
        "validate_review_zip",
        lambda *args, **kwargs: (_ for _ in ()).throw(ReviewBundleError("injected")),
    )
    with pytest.raises(ReviewBundleError, match="injected"):
        build_chatgpt_review_bundle(result)
    assert path.read_bytes() == before
    assert not list(result.glob(".*.tmp"))


def test_authoritative_artifact_hashes_are_unchanged(tmp_path: Path) -> None:
    result = _scan_fixture(tmp_path)
    sidecar = result / "stage8b4a_p4_decision_package.v1.json.sha256"
    sidecar.write_text("c" * 64 + "\n", encoding="ascii")
    protected = [result / relative for relative in bundle.CORE_ARTIFACTS[:-1]] + [sidecar]
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    build_chatgpt_review_bundle(result)
    after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
    assert after == before


def test_bundle_failure_cannot_change_terminal_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = ServiceStorage(tmp_path / "data")
    result = _scan_fixture(storage.results)
    config = WorkerConfig(
        pipeline_root=tmp_path,
        worker_python=Path(sys.executable),
        profile_bundle=tmp_path,
    )
    record = {"scan_id": result.name, "status": "completed", "error": None}
    before = dict(record)
    monkeypatch.setattr(
        "deltar_p4_pipeline.scan_service_worker.build_chatgpt_review_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected")),
    )
    with pytest.warns(RuntimeWarning, match="scan result remains unchanged"):
        assert publish_terminal_review_bundle(config, storage, record) is None
    assert record == before


def _worker_config(tmp_path: Path) -> WorkerConfig:
    executable = Path(sys.executable)
    return WorkerConfig(
        pipeline_root=tmp_path,
        worker_python=executable,
        profile_bundle=tmp_path,
        auto_open_visualization=False,
    )


def test_failure_finalize_automatically_builds_partial_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _worker_config(tmp_path)
    storage = ServiceStorage(tmp_path / "data")
    result = _scan_fixture(storage.results, status="failed")
    record = {
        "scan_id": result.name,
        "service_job_id": "svc_test",
        "status": "running",
        "frame_count": 11,
    }

    def fail(*args, **kwargs):
        from deltar_p4_pipeline.scan_service_contracts import ServiceError

        raise ServiceError("E_TEST", "fixture failure", 409, scan_id=result.name)

    transitions: list[str] = []

    def transition(value: dict) -> None:
        transitions.append(str(value["status"]))
        storage.atomic_json(result / "service_job.json", value)

    monkeypatch.setattr("deltar_p4_pipeline.scan_service_worker._run_process", fail)
    run_pipeline(config, storage, record, transition)
    path = result / f"{result.name}_chatgpt_review.zip"
    assert path.is_file()
    assert _manifest_from_zip(path)["bundle_status"] == "partial_failure"
    assert transitions[-1] == "failed"


def test_success_finalize_automatically_builds_full_zip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _worker_config(tmp_path)
    storage = ServiceStorage(tmp_path / "data")
    result = _scan_fixture(storage.results)
    record = {
        "scan_id": result.name,
        "service_job_id": "svc_test",
        "status": "running",
        "frame_count": 11,
        "job_role": "user",
        "visibility": "user",
    }
    transitions: list[str] = []

    def transition(value: dict) -> None:
        transitions.append(str(value["status"]))
        storage.atomic_json(result / "service_job.json", value)

    monkeypatch.setattr(
        "deltar_p4_pipeline.scan_service_worker._run_process",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b"", b""),
    )
    monkeypatch.setattr(
        "deltar_p4_pipeline.scan_service_worker.validate_result_files",
        lambda *args, **kwargs: ({"job_id": "job_test", "status": "success"}, b"{}"),
    )
    monkeypatch.setattr(
        "deltar_p4_pipeline.scan_service_worker.validate_decision_package",
        lambda *args, **kwargs: ({}, b"{}", hashlib.sha256(b"{}").hexdigest()),
    )

    def finish(_config, _storage, value, callback, **kwargs):
        value.update(
            status="completed",
            finished_at="2026-08-19T00:00:00.000Z",
            full_scene_status="ready",
            full_scene_ready=True,
        )
        callback(value)

    monkeypatch.setattr("deltar_p4_pipeline.scan_service_worker._finish_full_scene", finish)
    run_pipeline(config, storage, record, transition)
    path = result / f"{result.name}_chatgpt_review.zip"
    assert path.is_file()
    assert _manifest_from_zip(path)["bundle_status"] == "full_success"
    assert transitions[-1] == "completed"


def test_module_import_does_not_load_heavy_dependencies() -> None:
    code = (
        "import sys; import deltar_p4_pipeline.chatgpt_review_bundle; "
        "assert 'scipy' not in sys.modules; assert 'torch' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, completed.stderr
