from __future__ import annotations

import io
from pathlib import Path

import pytest

from deltar_p4_pipeline.scan_service_contracts import ServiceError
from deltar_p4_pipeline.scan_service_storage import ServiceStorage


def test_stream_write_is_exact_and_no_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "x.bin"
    assert ServiceStorage.write_stream(io.BytesIO(b"abcd"), target, maximum=4, exact=4, block_size=2) == 4
    assert target.read_bytes() == b"abcd"
    with pytest.raises(ServiceError):
        ServiceStorage.write_stream(io.BytesIO(b"zzzz"), target, maximum=4)


def test_stream_caps_and_image_exact_size(tmp_path: Path) -> None:
    with pytest.raises(ServiceError) as too_large:
        ServiceStorage.write_stream(io.BytesIO(b"abcde"), tmp_path / "large", maximum=4)
    assert too_large.value.code == "E_PART_TOO_LARGE"
    with pytest.raises(ServiceError) as wrong_size:
        ServiceStorage.write_stream(io.BytesIO(b"abc"), tmp_path / "short", maximum=4, exact=4)
    assert wrong_size.value.code == "E_IMAGE_SIZE"


def test_atomic_json_and_strict_read(tmp_path: Path) -> None:
    path = tmp_path / "record.json"
    ServiceStorage.atomic_json(path, {"a": 1}, replace=False)
    assert ServiceStorage.read_json(path) == {"a": 1}
    with pytest.raises(FileExistsError):
        ServiceStorage.atomic_json(path, {"a": 2}, replace=False)


def test_recover_upload_never_resumes(tmp_path: Path) -> None:
    storage = ServiceStorage(tmp_path)
    stale = storage.incoming / "scan.svc_dead.uploading"
    stale.mkdir()
    moved = storage.recover_uploads()
    assert len(moved) == 1
    assert not stale.exists()
    assert (moved[0] / "service_error.json").is_file()


def test_paths_remain_inside_root(tmp_path: Path) -> None:
    storage = ServiceStorage(tmp_path)
    assert storage.result_dir("safe").is_relative_to(tmp_path.resolve())
    with pytest.raises(ServiceError):
        storage.ensure_inside(tmp_path / ".." / "escape")
