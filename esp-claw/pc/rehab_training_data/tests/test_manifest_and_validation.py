import hashlib
from pathlib import Path

from pc.rehab_training_data.manifest import build_manifest, load_manifest, sha256_file, write_manifest
from pc.rehab_training_data.validation import compare_trees, validate_manifest


def test_sha256_and_manifest_are_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    payload = b"fixture payload\n"
    (source / "task.json").write_bytes(payload)

    manifest = build_manifest(source, target=tmp_path / "target", session_id="fixture_001")

    assert sha256_file(source / "task.json") == hashlib.sha256(payload).hexdigest()
    assert manifest["file_count"] == 1
    assert manifest["total_bytes"] == len(payload)
    assert manifest["files"] == [
        {
            "path": "task.json",
            "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
    ]

    manifest_path = tmp_path / "manifest.json"
    write_manifest(manifest, manifest_path)
    assert load_manifest(manifest_path) == manifest


def test_tree_and_manifest_validation_compare_size_hash_and_file_list(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "result.json").write_text('{"score": 1}\n', encoding="utf-8")
    (target / "result.json").write_text('{"score": 1}\n', encoding="utf-8")

    manifest = build_manifest(source)
    assert compare_trees(source, target).matches
    assert validate_manifest(target, manifest).matches

    (target / "result.json").write_text('{"score": 2}\n', encoding="utf-8")
    result = compare_trees(source, target)
    assert not result.matches
    assert result.hash_mismatches == ("result.json",)
