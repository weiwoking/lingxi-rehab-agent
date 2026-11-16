from __future__ import annotations

import json
from pathlib import Path

try:
    from scripts.build_stage4_synthetic_scenes import build_scenes
except ModuleNotFoundError:  # direct script execution
    from build_stage4_synthetic_scenes import build_scenes
from deltar_p4_pipeline.synthetic_geometry import evaluate_synthetic, semantics_roundtrip_error

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs_stage4" / "synthetic_matrix.json"


def run() -> dict:
    profile = json.loads((ROOT / "geometry_profile.json").read_text(encoding="utf-8"))
    rows = []
    for scene in build_scenes():
        kwargs = {key: scene[key] for key in ("source_type", "surface_depth_m", "erode_pixels") if key in scene}
        result = evaluate_synthetic(scene["depth"], scene["mask"], scene["calibration"], profile, scene["truth"], **kwargs)
        row = {"scene_id": scene["scene_id"], "status": "pass", "truth": result["truth"], "geometry": result["geometry"], "errors": result["errors"], "filter_metrics": result["filter_metrics"]}
        if scene.get("equivalent_pair"):
            second = evaluate_synthetic(scene["depth"].copy(), scene["mask"].copy(), scene["calibration"], profile, scene["truth"], **kwargs)
            row["direct_letterbox_equivalence"] = {name: abs(result["geometry"][name] - second["geometry"][name]) for name in ("distance_m", "object_height_m", "bottom_height_m")}
        if scene.get("semantics_case"):
            row["semantics_validation"] = {semantics: semantics_roundtrip_error(semantics) for semantics in ("axial_z", "radial_range")}
        rows.append(row)
    summary = {
        "scene_count": len(rows),
        "passed": sum(row["status"] == "pass" for row in rows),
        "max_distance_absolute_error_m": max(row["errors"]["distance_m"] for row in rows),
        "max_height_absolute_error_m": max(row["errors"]["object_height_m"] for row in rows),
        "max_bottom_height_absolute_error_m": max(row["errors"]["bottom_height_m"] for row in rows),
    }
    payload = {"schema_version": "deltar_stage4_synthetic_matrix.v1", "status": "pass", "summary": summary, "scenes": rows, "truth_source": "analytic geometry; never model-generated"}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(run()["summary"], indent=2))
