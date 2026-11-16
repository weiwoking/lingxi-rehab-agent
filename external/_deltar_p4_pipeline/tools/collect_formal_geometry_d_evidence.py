from __future__ import annotations

import hashlib
import json
import platform
import statistics
import sys
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import scipy
import torch


ROOT = Path(__file__).resolve().parents[1]
RESEARCH = Path(
    r"C:\ec\_calibration_workspaces\deltar_fusion_final_ab_cde_20260818_01"
)
REPLAYS = Path(r"C:\ec\_formal_d_replays_20260818")
REPLAY_NAMES = {
    "G001": "G001_release",
    "G002": "G002_release",
    "H001": "H001_release",
    "H002": "H002_release",
    "H003": "H003_release",
}


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stats(values: list[float]) -> dict[str, float | int]:
    array = np.asarray(values, dtype=np.float64)
    return {
        "count": int(array.size),
        "median": float(np.median(array)),
        "p95": float(np.percentile(array, 95)),
        "minimum": float(np.min(array)),
        "maximum": float(np.max(array)),
    }


def geometry_records(document: dict[str, Any]) -> dict[str, tuple[float, float, float]]:
    return {
        str(item["logical_object_id"]): (
            float(item["geometry"]["support_height_mm"]),
            float(item["geometry"]["top_height_above_floor_mm"]),
            float(item["geometry"]["object_height_mm"]),
        )
        for frame in document["frames"]
        for item in frame["objects"]
        if item["geometry_status"] == "success"
    }


def research_records(scan: str) -> dict[str, tuple[float, float, float]]:
    rows = load(
        RESEARCH
        / "results"
        / scan
        / "geometry_v2"
        / "D_TRUE_READ_OFFICIAL_WRITE"
        / "object_records.geometry_v2.json"
    )
    return {
        str(item["logical_object_id"]): (
            float(item["final"]["support_mm"]),
            float(item["final"]["top_mm"]),
            float(item["final"]["object_height_mm"]),
        )
        for item in rows
    }


def scan_evidence(scan: str, replay_name: str) -> dict[str, Any]:
    replay = REPLAYS / replay_name
    production_geometry = load(replay / "stage8b_all_object_geometry.v1.json")
    actual = geometry_records(production_geometry)
    expected = research_records(scan)
    deltas = [
        abs(actual[object_id][index] - expected[object_id][index])
        for object_id in sorted(expected)
        for index in range(3)
    ]
    dense_path = RESEARCH / "results" / scan / "full_dense_depth_arrays.npz"
    dense_deltas: list[float] = []
    a_d_frame_mean: list[float] = []
    a_d_frame_p95: list[float] = []
    a_d_max_pixels: list[float] = []
    nan_pixels = 0
    negative_pixels = 0
    valid_zone_counts: list[int] = []
    timing_rows: list[dict[str, float]] = []
    with np.load(dense_path, allow_pickle=False) as archive:
        for sequence in range(11):
            frame_id = f"frame_{sequence:06d}"
            official_a = np.asarray(archive[f"m0_{frame_id}"], dtype=np.float64)
            research_d = np.asarray(archive[f"m3_{frame_id}"], dtype=np.float64)
            production_d = np.load(
                replay
                / "stage7d"
                / frame_id
                / "axial_z"
                / "depth_source_640x360.npy",
                allow_pickle=False,
            ).astype(np.float64)
            dense_deltas.append(float(np.max(np.abs(production_d - research_d))))
            difference = np.abs(research_d - official_a)
            a_d_frame_mean.append(float(np.mean(difference)))
            a_d_frame_p95.append(float(np.percentile(difference, 95)))
            a_d_max_pixels.append(float(np.max(difference)))
            nan_pixels += int(np.count_nonzero(~np.isfinite(production_d)))
            negative_pixels += int(np.count_nonzero(production_d < 0.0))
            frame = load(replay / "frame_results" / f"{frame_id}.json")
            timing_rows.append(frame["processing_ms"])
            valid_zone_counts.append(
                int(
                    frame["deltar_repeatability"]["direct_640x360"]
                    ["formal_d_fusion"]["cross_atten1"]["valid_zone_count"]
                )
            )
    package_path = replay / "stage8b4a_p4_decision_package.v1.json"
    sidecar = package_path.with_name(package_path.name + ".sha256")
    package = load(package_path)
    job = load(replay / "job_report.json")
    heights = [value[2] for value in actual.values()]
    top_support = [value[1] - value[0] for value in actual.values()]
    timing = {
        name: stats([float(row[name]) for row in timing_rows])
        for name in (
            "deltar_direct_ms",
            "deltar_letterbox_ms",
            "deltar_total_ms",
            "stage7d_total_critical_ms",
            "all_object_geometry_ms",
            "frame_total_ms",
        )
    }
    return {
        "scan_id": job["scan_id"],
        "job_status": job["status"],
        "result_status": job["result_status"],
        "processed_frames": len(job["processed_frames"]),
        "per_scan_total_ms": float(job["processing"]["total_ms"]),
        "production_vs_research_d": {
            "dense_max_abs_delta_m": max(dense_deltas),
            "geometry_identity_sets_equal": set(actual) == set(expected),
            "geometry_max_abs_delta_mm": max(deltas, default=0.0),
        },
        "official_a_to_d_dense_risk": {
            "maximum_frame_mean_abs_delta_m": max(a_d_frame_mean),
            "maximum_frame_p95_abs_delta_m": max(a_d_frame_p95),
            "maximum_pixel_abs_delta_m": max(a_d_max_pixels),
        },
        "business_geometry": {
            "object_count": len(actual),
            "finite_positive": bool(
                heights
                and all(np.isfinite(heights))
                and all(value > 0.0 for value in heights)
                and all(value > 0.0 for value in top_support)
            ),
            "nan_dense_pixels": nan_pixels,
            "negative_dense_pixels": negative_pixels,
            "same_surface_consensus_count": int(
                production_geometry["summary"]["same_surface_consensus_count"]
            ),
        },
        "valid_zone_count": stats([float(value) for value in valid_zone_counts]),
        "timing_ms": timing,
        "p4_package": {
            "schema_version": package["schema_version"],
            "status": package["status"],
            "bytes": package_path.stat().st_size,
            "object_count": len(package["objects"]),
            "sha256": sha256(package_path),
            "sidecar_matches": sidecar.read_text(encoding="ascii").strip()
            == sha256(package_path),
            "required_geometry_fields_present": all(
                {
                    "valid",
                    "support_height_mm",
                    "object_height_mm",
                    "top_height_above_floor_mm",
                    "distance_mm",
                    "distance_status",
                }
                <= set(item["geometry"])
                for item in package["objects"]
            ),
        },
    }


def main() -> int:
    comparison = load(RESEARCH / "metrics" / "FINAL_A_B_C_D_E_COMPARISON.json")
    performance = load(RESEARCH / "metrics" / "PERFORMANCE_A_B_C_D_E.json")
    scans = {
        scan: scan_evidence(scan, replay_name)
        for scan, replay_name in REPLAY_NAMES.items()
    }
    d_performance = {
        scan: performance["scans"][scan]["D_TRUE_READ_OFFICIAL_WRITE"]
        for scan in REPLAY_NAMES
    }
    sampler_values = [
        float(item["component_probe_sampler_ms"])
        for item in d_performance.values()
    ]
    gpu_values = [
        float(item["gpu_peak_mb"]["median"]) for item in d_performance.values()
    ]
    payload = {
        "schema_version": "formal_geometry_d_cutover_evidence.v1",
        "exact_interpreter": {
            "sys_executable": sys.executable,
            "python": platform.python_version(),
            "numpy": np.__version__,
            "opencv": cv2.__version__,
            "scipy": scipy.__version__,
            "torch": torch.__version__,
            "cuda_runtime": torch.version.cuda,
            "cuda_available": torch.cuda.is_available(),
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        },
        "scans": scans,
        "support_acceptance": {
            scan: comparison["competition"][scan]["modes"]
            ["D_TRUE_READ_OFFICIAL_WRITE"]
            for scan in ("G001", "G002")
        },
        "cross_distance": comparison["cross_distance"][
            "D_TRUE_READ_OFFICIAL_WRITE"
        ],
        "research_d_performance": {
            "by_scan": d_performance,
            "sampler_ms": stats(sampler_values),
            "gpu_peak_median_mb": stats(gpu_values),
        },
        "all_five_dense_bit_exact_to_research_d": all(
            item["production_vs_research_d"]["dense_max_abs_delta_m"] == 0.0
            for item in scans.values()
        ),
        "all_five_geometry_bit_exact_to_research_d": all(
            item["production_vs_research_d"]["geometry_max_abs_delta_mm"] == 0.0
            and item["production_vs_research_d"]["geometry_identity_sets_equal"]
            for item in scans.values()
        ),
        "all_five_p4_contracts_pass": all(
            item["p4_package"]["sidecar_matches"]
            and item["p4_package"]["required_geometry_fields_present"]
            and item["p4_package"]["status"] == "decision_ready"
            for item in scans.values()
        ),
    }
    destination = ROOT / "tmp" / "formal_cutover_metrics.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "path": str(destination),
                "dense_exact": payload["all_five_dense_bit_exact_to_research_d"],
                "geometry_exact": payload[
                    "all_five_geometry_bit_exact_to_research_d"
                ],
                "p4_contracts": payload["all_five_p4_contracts_pass"],
                "h001_max_frame_mean_abs_delta_m": scans["H001"]
                ["official_a_to_d_dense_risk"]
                ["maximum_frame_mean_abs_delta_m"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
