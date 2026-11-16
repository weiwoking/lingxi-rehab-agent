from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import h5py
import numpy as np

from deltar_p4_pipeline.official_object_benchmark import OFFICIAL_INDEX, OFFICIAL_ROOT, official_samples, read_json, sha256_file, write_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("outputs_stage5_2a/official_dataset_audit.json"))
    args = parser.parse_args()
    samples = official_samples()
    records = []
    missing = []
    corrupt = []
    shape_counts = {name: Counter() for name in ("rgb", "depth", "hist_data", "fr", "mask")}
    depth_min, depth_max, total_valid_depth = [], [], 0
    for sample in samples:
        path = Path(sample["hdf5_path"])
        if not path.is_file():
            missing.append(sample["relative_path"])
            continue
        try:
            with h5py.File(path, "r") as handle:
                fields = sorted(handle.keys())
                required = {"rgb", "depth", "hist_data", "fr", "mask"}
                if not required.issubset(fields):
                    raise ValueError(f"missing fields {sorted(required - set(fields))}")
                arrays = {name: handle[name][:] for name in required}
            for name, value in arrays.items():
                shape_counts[name][str(list(value.shape))] += 1
            valid = np.isfinite(arrays["depth"]) & (arrays["depth"] > 1e-3) & (arrays["depth"] < 10.0)
            valid_values = arrays["depth"][valid]
            if valid_values.size:
                depth_min.append(float(valid_values.min()))
                depth_max.append(float(valid_values.max()))
            total_valid_depth += int(valid.sum())
            records.append({
                **sample,
                "bytes": path.stat().st_size,
                "fields": fields,
                "shapes": {name: list(value.shape) for name, value in arrays.items()},
                "dtypes": {name: str(value.dtype) for name, value in arrays.items()},
                "valid_zone_count": int(np.asarray(arrays["mask"], dtype=bool).sum()),
                "valid_depth_count": int(valid.sum()),
                "valid_depth_min_m": float(valid_values.min()) if valid_values.size else None,
                "valid_depth_max_m": float(valid_values.max()) if valid_values.size else None,
                "patch_info": "recomputable from official fr with src.utils.dataloader.patch_info_from_rect_data",
            })
        except Exception as exc:
            corrupt.append({"relative_path": sample["relative_path"], "error": f"{type(exc).__name__}: {exc}"})
    evidence = Path(r"C:\ec\_deltar_formal\evidence")
    document = {
        "schema_version": "deltar_stage5_2a_official_dataset_audit.v1",
        "source": {
            "dataset_root": str(OFFICIAL_ROOT),
            "index_path": str(OFFICIAL_INDEX),
            "index_sha256": sha256_file(OFFICIAL_INDEX),
            "demo_zip_manifest": read_json(evidence / "demo_asset_manifest.json"),
            "extracted_manifest_path": str(evidence / "extracted_demo_sha256_manifest.json"),
            "extracted_manifest_sha256": sha256_file(evidence / "extracted_demo_sha256_manifest.json"),
            "official_worktree_commit": "f3c517562bcb2dfb9e4bbf74965f2ee76a2dd096",
        },
        "sample_total": len(samples),
        "runnable_sample_count": len(records),
        "missing_sample_count": len(missing),
        "corrupt_sample_count": len(corrupt),
        "missing_samples": missing,
        "corrupt_samples": corrupt,
        "shape_counts": {name: dict(counts) for name, counts in shape_counts.items()},
        "depth_contract": {
            "unit": "metre",
            "valid_rule": "finite and 0.001 < depth < 10.0",
            "dataset_valid_min_m": min(depth_min) if depth_min else None,
            "dataset_valid_max_m": max(depth_max) if depth_max else None,
            "total_valid_pixels": total_valid_depth,
        },
        "hist_contract": {"shape": [64, 2], "columns": ["mu_m", "sigma_m"]},
        "fr_contract": {"shape": [64, 4], "columns": ["sy", "sx", "ey", "ex"], "order": "row-major top-to-bottom left-to-right"},
        "mask_contract": {"shape": [64], "meaning": "official valid zone boolean"},
        "samples": records,
        "stage5_simulator_used": False,
    }
    write_json(args.output, document)
    print(f"official audit: {len(records)}/{len(samples)} runnable -> {args.output}")
    return 0 if not missing and not corrupt and len(records) == 287 else 2


if __name__ == "__main__":
    raise SystemExit(main())
