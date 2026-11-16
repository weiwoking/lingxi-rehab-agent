from __future__ import annotations

import hashlib
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from deltar_p4_pipeline.analytic_renderer import stage5_scene_family, write_scene_artifacts
from deltar_p4_pipeline.rgb565 import read_rgb565_le
from deltar_p4_pipeline.stage5_fr import fr_from_diagnostics, validate_fr_pair
from deltar_p4_pipeline.tof_simulator import simulate_tof
from deltar_p4_pipeline.zone_orientation import orientation_profile

CANVASES = ("direct_640x360", "letterbox_640x480")
SEMANTICS = ("radial_range", "camera_axial_z")


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def p4_profile(scene: dict[str, Any], strategy: str, tof: dict[str, Any], diagnostics: dict[str, Any], orientation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    mask = [int(c) > 0 and int(s) != 255 for c, s in zip(tof["nb_target_detected"], tof["target_status"], strict=True)]
    fr, fr_meta = fr_from_diagnostics(scene, diagnostics, strategy)
    profile = {
        "schema_version": "deltar_p4_profile.v1",
        "profile_id": f"stage5_1_{scene['scene_id']}_{diagnostics['measurement_semantics']}_{strategy}",
        "sample_kind": "stage5_1_corrected",
        "image": {"width": 640, "height": 360, "stride": 1280, "pixel_format": "RGB565_LE"},
        "canvas": {"strategy": strategy, "fill_rgb": [0, 0, 0]},
        "zone_mapping": {"strategy": orientation["strategy"], "explicit_index_map_64": orientation.get("explicit_index_map_64")},
        "histogram": {"mu_strategy": "distance_mm_to_m", "sigma_strategy": "range_sigma_mm_to_m"},
        "mask": {"strategy": "explicit_bool64", "values": mask},
        "fr": {"strategy": "explicit_model_canvas", "values": fr},
        "seed": 0,
        "test_intrinsics": {k: scene["camera"][k] for k in ("fx", "fy", "cx", "cy")},
        "gt_used_for_inference": False,
        "stage5_tof_profile": {
            "measurement_semantics": diagnostics["measurement_semantics"],
            "conversion_provenance": diagnostics["conversion_provenance"],
            "distance_mm_source": diagnostics["distance_mm_source"],
            "sigma_mm_source": diagnostics["sigma_mm_source"],
            "zone_orientation": orientation,
            "approximate_sensor_model": True,
            "T_camera_from_tof": scene["tof"]["T_camera_from_tof"],
            "physical_in_plane_rotation_deg": scene["tof"].get("in_plane_rotation_deg", 0),
        },
    }
    return profile, {"canvas": strategy, "zones": fr_meta}


def build(scenes: list[dict[str, Any]], output: Path, *, semantics: tuple[str, ...] = SEMANTICS) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    orientation = orientation_profile("identity")
    for sequence, scene in enumerate(scenes):
        for semantic in semantics:
            parent = output / f"{scene['scene_id']}__{semantic}"
            started = time.perf_counter()
            artifacts = write_scene_artifacts(scene, parent)
            render_ms = (time.perf_counter() - started) * 1000.0
            decoded = read_rgb565_le(parent / "image.rgb565")
            Image.fromarray(decoded, mode="RGB").save(parent / "source_rgb_from_rgb565.png")
            decoded_hash = hashlib.sha256(decoded.tobytes()).hexdigest()
            started = time.perf_counter()
            tof, diagnostics = simulate_tof(scene, sequence=sequence, seed=sequence, measurement_semantics=semantic)
            tof_ms = (time.perf_counter() - started) * 1000.0
            write_json(parent / "tof.json", tof)
            write_json(parent / "tof_diagnostics.json", diagnostics)
            write_json(parent / "scene_profile.json", {**scene["tof"], "measurement_semantics": semantic,
                       "scene_id": scene["scene_id"], "scenario_family": scene["scenario_family"],
                       "appearance_kind": scene["appearance_kind"], "appearance_asset_id": scene.get("appearance_asset_id"),
                       "zone_orientation": orientation})
            fr_by_canvas: dict[str, list[list[float]]] = {}
            for canvas in CANVASES:
                folder = parent / canvas
                folder.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(parent / "image.rgb565", folder / "image.rgb565")
                write_json(folder / "tof.json", tof)
                profile, fr_evidence = p4_profile(scene, canvas, tof, diagnostics, orientation)
                fr_by_canvas[canvas] = profile["fr"]["values"]
                write_json(folder / "profile.json", profile)
                write_json(folder / "fr_evidence.json", fr_evidence)
                write_json(folder / "source_metadata.json", {
                    "scene_id": scene["scene_id"], "formal_rgb_source": "../image.rgb565 -> read_rgb565_le",
                    "decoded_rgb_sha256": decoded_hash, "rgb888_to_rgb565_quantization": artifacts["metadata"]["rgb888_to_rgb565_quantization"],
                    "gt_used_for_inference": False,
                })
            valid_mask = [int(c) > 0 and int(s) != 255 for c, s in zip(tof["nb_target_detected"], tof["target_status"], strict=True)]
            fr_validation = validate_fr_pair(fr_by_canvas[CANVASES[0]], fr_by_canvas[CANVASES[1]], valid_mask)
            rows.append({
                "sample_id": parent.name, "scene_id": scene["scene_id"], "scenario_family": scene["scenario_family"],
                "appearance_kind": scene["appearance_kind"], "measurement_semantics": semantic,
                "object_count": len(scene["objects"]), "path": str(parent), "render_ms": render_ms,
                "tof_simulation_ms": tof_ms, "decoded_rgb_sha256": decoded_hash, "fr_validation": fr_validation,
                "gt_used_for_inference": False,
            })
    return rows


def main() -> None:
    output = ROOT / "samples" / "stage5_1_analytic"
    output.mkdir(parents=True, exist_ok=True)
    scenes = [s for s in stage5_scene_family() if s["appearance_kind"] == "analytic"]
    rows = build(scenes, output)
    print(json.dumps({"status": "pass", "sample_count": len(rows), "base_scene_count": len(scenes), "object_cases": sum(r["object_count"] for r in rows), "semantics": list(SEMANTICS)}, indent=2))


if __name__ == "__main__":
    main()
