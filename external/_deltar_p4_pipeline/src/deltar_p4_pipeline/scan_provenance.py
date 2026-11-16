from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .scan_contracts import canonical_sha256, sha256_file
from .scan_profiles import ProfileBundle, runtime_summary

PIPELINE_VERSION = "6.1.0"
UPSTREAM_COMMITS = {
    "esp_claw": "8aa863e07fe1ef4b3e4a21004f87a89a9beb9644",
    "deltar": "f3c517562bcb2dfb9e4bbf74965f2ee76a2dd096",
    "calib_deltar": "0dd16ef96fa76598cf542bba9b57f96548d27e35",
}


def package_manifest(root: Path) -> dict[str, Any]:
    paths = sorted(path for path in (root / "src" / "deltar_p4_pipeline").glob("*.py") if path.is_file())
    hashes = {path.name: sha256_file(path) for path in paths}
    return {"files": hashes, "sha256": canonical_sha256(hashes)}


def provenance(root: Path, bundle: ProfileBundle) -> dict[str, Any]:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "package": package_manifest(root),
        "models": {item["task"]: {"sha256": item["sha256"], "upstream_commit": item.get("upstream_commit")} for item in bundle.model_manifest["models"]},
        "profiles": bundle.public_provenance(),
        "upstream_commits": UPSTREAM_COMMITS,
        "runtime": runtime_summary(),
        "gt_used_for_inference": False,
        "local_debug_paths_recorded": False,
    }
