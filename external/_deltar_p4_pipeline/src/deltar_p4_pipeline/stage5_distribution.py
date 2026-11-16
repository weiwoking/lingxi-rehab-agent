from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np


def describe(values: list[float] | np.ndarray) -> dict[str, Any]:
    data = np.asarray(values, dtype=np.float64)
    data = data[np.isfinite(data)]
    if not data.size:
        return {key: None for key in ("count", "min", "p10", "p25", "median", "p75", "p90", "p95", "max")}
    return {"count": int(data.size), "min": float(data.min()), "p10": float(np.percentile(data, 10)),
            "p25": float(np.percentile(data, 25)), "median": float(np.median(data)), "p75": float(np.percentile(data, 75)),
            "p90": float(np.percentile(data, 90)), "p95": float(np.percentile(data, 95)), "max": float(data.max())}


def ks_like(left: list[float], right: list[float]) -> float | None:
    a = np.sort(np.asarray(left, dtype=np.float64)); b = np.sort(np.asarray(right, dtype=np.float64))
    a = a[np.isfinite(a)]; b = b[np.isfinite(b)]
    if not a.size or not b.size: return None
    points = np.unique(np.concatenate((a, b)))
    return float(np.max(np.abs(np.searchsorted(a, points, side="right") / a.size - np.searchsorted(b, points, side="right") / b.size)))


def official_distribution(room: Path) -> tuple[dict[str, Any], dict[str, list[float]]]:
    import h5py
    raw = {key: [] for key in ("mu_m", "sigma_m", "valid_count", "fr_width_px", "fr_height_px", "fr_center_x_px", "fr_center_y_px", "fr_oob", "hypothesis_min_m", "hypothesis_max_m")}
    files = sorted(room.glob("*.h5"))
    for path in files:
        with h5py.File(path, "r") as handle:
            hist = np.asarray(handle["hist_data"]); fr = np.asarray(handle["fr"]); mask = np.asarray(handle["mask"], dtype=bool)
        raw["valid_count"].append(float(mask.sum()))
        raw["mu_m"].extend(hist[mask, 0].tolist()); raw["sigma_m"].extend(hist[mask, 1].tolist())
        raw["hypothesis_min_m"].extend((hist[mask, 0] - 3 * hist[mask, 1]).tolist())
        raw["hypothesis_max_m"].extend((hist[mask, 0] + 3 * hist[mask, 1]).tolist())
        sy, sx, ey, ex = (fr[:, i] for i in range(4))
        raw["fr_width_px"].extend((ex - sx).tolist()); raw["fr_height_px"].extend((ey - sy).tolist())
        raw["fr_center_x_px"].extend(((sx + ex) / 2).tolist()); raw["fr_center_y_px"].extend(((sy + ey) / 2).tolist())
        raw["fr_oob"].extend(((sy < 0) | (sx < 0) | (ey > 480) | (ex > 640)).astype(float).tolist())
    return {"schema_version": "deltar_stage5_1_official_input_distribution.v1", "file_count": len(files),
            "zone_case_count": len(files) * 64, "fr_order": "sy_sx_ey_ex", "hypothesis_rule": "uniform 16 points from mu-3sigma to mu+3sigma",
            "statistics": {key: describe(values) for key, values in raw.items()}, "zone_valid_rate": float(np.mean(np.asarray(raw["valid_count"]) / 64))}, raw


def simulated_distribution(sample_roots: list[Path]) -> tuple[dict[str, Any], dict[str, list[float]]]:
    import json
    raw = {key: [] for key in ("mu_m", "sigma_m", "valid_count", "fr_width_px", "fr_height_px", "fr_center_x_px", "fr_center_y_px", "fr_oob", "hypothesis_min_m", "hypothesis_max_m")}
    semantics: dict[str, int] = {}
    for parent in sample_roots:
        tof = json.loads((parent / "tof.json").read_text(encoding="utf-8")); profile = json.loads((parent / "letterbox_640x480" / "profile.json").read_text(encoding="utf-8"))
        valid = np.asarray(profile["mask"]["values"], dtype=bool); mu = np.asarray(tof["distance_mm"], dtype=float) / 1000; sigma = np.asarray(tof["range_sigma_mm"], dtype=float) / 1000
        fr = np.asarray(profile["fr"]["values"], dtype=float); semantic = profile["stage5_tof_profile"]["measurement_semantics"]; semantics[semantic] = semantics.get(semantic, 0) + 1
        raw["valid_count"].append(float(valid.sum())); raw["mu_m"].extend(mu[valid].tolist()); raw["sigma_m"].extend(sigma[valid].tolist())
        raw["hypothesis_min_m"].extend((mu[valid] - 3 * sigma[valid]).tolist()); raw["hypothesis_max_m"].extend((mu[valid] + 3 * sigma[valid]).tolist())
        sy, sx, ey, ex = (fr[:, i] for i in range(4)); raw["fr_width_px"].extend((ex - sx).tolist()); raw["fr_height_px"].extend((ey - sy).tolist())
        raw["fr_center_x_px"].extend(((sx + ex) / 2).tolist()); raw["fr_center_y_px"].extend(((sy + ey) / 2).tolist())
        raw["fr_oob"].extend(((sy < 0) | (sx < 0) | (ey > 480) | (ex > 640)).astype(float).tolist())
    return {"schema_version": "deltar_stage5_1_simulated_input_distribution.v1", "sample_count": len(sample_roots), "semantics": semantics,
            "zone_case_count": len(sample_roots) * 64, "fr_order": "sy_sx_ey_ex", "statistics": {key: describe(values) for key, values in raw.items()},
            "zone_valid_rate": float(np.mean(np.asarray(raw["valid_count"]) / 64))}, raw


def compare_distributions(official: dict[str, list[float]], simulated: dict[str, list[float]]) -> dict[str, Any]:
    return {"schema_version": "deltar_stage5_1_input_distribution_comparison.v1", "method": "two-sample maximum empirical CDF distance",
            "metrics": {key: {"ks_like_cdf_distance": ks_like(official[key], simulated[key]),
                              "official": describe(official[key]), "simulated": describe(simulated[key])}
                        for key in official}}
