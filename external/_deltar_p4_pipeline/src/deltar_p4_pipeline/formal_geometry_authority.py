from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


AUTHORITY_SCHEMA = "deltar_formal_geometry_authority.v2"
AUTHORITY_PROFILE_ID = "formal_geometry_stage2_d_stage3a_allobject_v2_20260818"
DEFAULT_AUTHORITY_DIR = Path(__file__).resolve().parents[2] / "profiles" / "formal_geometry_v2"


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"formal authority payload is not an object: {path}")
    return value, raw


@dataclass(frozen=True)
class FormalGeometryAuthority:
    root: Path
    manifest: dict[str, Any]
    stage2: dict[str, Any]
    stage3a: dict[str, Any]
    geometry_v2: dict[str, Any]

    @property
    def camera_model(self) -> dict[str, Any]:
        intr = self.stage2["camera_intrinsics"]["camera"]["intrinsics"]
        distortion = intr["distortion"]
        return {
            "model": "opencv_brown",
            "fx": float(intr["fx"]),
            "fy": float(intr["fy"]),
            "cx": float(intr["cx"]),
            "cy": float(intr["cy"]),
            "distortion": [
                float(distortion[key]) for key in ("k1", "k2", "p1", "p2", "k3")
            ],
        }

    @property
    def gravity_up_camera(self) -> np.ndarray:
        return np.asarray(self.stage3a["gravity_authority"]["u_up_camera"], dtype=np.float64)

    @property
    def camera_height_mm(self) -> float:
        return float(self.stage3a["camera_height_mm"])

    def provenance(self) -> dict[str, Any]:
        return {
            "schema_version": self.manifest["schema_version"],
            "profile_id": self.manifest["profile_id"],
            "fusion": self.manifest["fusion"],
            "range_semantics": self.manifest["range_semantics"],
            "promoted_payload_set_sha256": self.manifest["promoted_payload_set_sha256"],
            "payloads": {
                name: {
                    "source_sha256": item["source_sha256"],
                    "promoted_sha256": item["promoted_sha256"],
                }
                for name, item in self.manifest["payloads"].items()
            },
        }


def load_formal_geometry_authority(root: Path | None = None) -> FormalGeometryAuthority:
    authority_root = (DEFAULT_AUTHORITY_DIR if root is None else Path(root)).resolve()
    manifest, _ = _read_json(authority_root / "authority.json")
    if manifest.get("schema_version") != AUTHORITY_SCHEMA:
        raise ValueError("formal geometry authority schema mismatch")
    if manifest.get("profile_id") != AUTHORITY_PROFILE_ID:
        raise ValueError("formal geometry authority profile_id mismatch")
    if manifest.get("fusion") != "D_TRUE_READ_OFFICIAL_WRITE":
        raise ValueError("formal geometry fusion must be D true-read/official-write")
    if manifest.get("range_semantics") != "axial_z":
        raise ValueError("formal geometry range semantics must be axial_z")

    loaded: dict[str, dict[str, Any]] = {}
    raw_payloads: list[bytes] = []
    for name in ("stage2", "stage3a", "all_object_v2"):
        item = manifest["payloads"][name]
        payload, raw = _read_json(authority_root / item["path"])
        actual = _sha256_bytes(raw)
        if actual != item["promoted_sha256"]:
            raise ValueError(f"promoted {name} SHA-256 mismatch: {actual}")
        loaded[name] = payload
        raw_payloads.append(raw)
    payload_set_hash = _sha256_bytes(b"".join(raw_payloads))
    if payload_set_hash != manifest["promoted_payload_set_sha256"]:
        raise ValueError("formal promoted payload-set SHA-256 mismatch")

    stage2 = loaded["stage2"]
    camera_tof = stage2.get("camera_tof", {})
    fr = stage2.get("deltar_fr", {})
    checks = {
        "stage2_schema": stage2.get("schema_version") == "stage2_final_calibration_bundle.v1",
        "stage2_axial": camera_tof.get("range_semantics", {}).get("selected") == "axial_z",
        "fr_axial": fr.get("measurement_semantics") == "axial_z",
        "fr_shape": fr.get("shape") == [64, 4],
        "zone_order": fr.get("zone_order") == "raw index 0..63 (identity canonical gauge)",
        "mapping_count": len(camera_tof.get("raw_zone_camera_mapping", [])) == 64,
        "raw_identity": [
            item.get("raw_index") for item in camera_tof.get("raw_zone_camera_mapping", [])
        ] == list(range(64)),
        "gauge_identity": camera_tof.get("gauge", {}).get("raw_index_for_canonical_index")
        == list(range(64)),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("formal Stage2 contract failed: " + ", ".join(failed))

    stage3a = loaded["stage3a"]
    gravity = stage3a.get("gravity_authority", {})
    up = np.asarray(gravity.get("u_up_camera"), dtype=np.float64)
    if (
        stage3a.get("schema_version") != "camera_gravity.stage3a_candidate.v1"
        or up.shape != (3,)
        or not np.isfinite(up).all()
        or not np.isclose(np.linalg.norm(up), 1.0, atol=1e-9)
        or gravity.get("full_world_rotation_authority") is not False
        or float(stage3a.get("camera_height_mm")) != 972.0
    ):
        raise ValueError("formal Stage3A gravity contract failed")

    config = loaded["all_object_v2"]
    if config.get("schema_version") != "stage3b_all_object_geometry_v2_config.v1":
        raise ValueError("formal all-object v2 config schema mismatch")
    for key in (
        "ground_truth_used_for_fit",
        "ground_truth_used_for_parameter_selection",
        "class_specific_fixed_compensation",
        "scene_target_name_special_cases",
    ):
        if config.get(key) is not False:
            raise ValueError(f"formal all-object v2 requires {key}=false")

    return FormalGeometryAuthority(
        authority_root, manifest, stage2, stage3a, config
    )
