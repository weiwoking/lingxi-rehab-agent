from __future__ import annotations

import json
from pathlib import Path
import random
import sys
from typing import Any

import numpy as np


PIPELINE = Path(r"C:\ec\_deltar_p4_pipeline")
WORKSPACE = Path(__file__).resolve().parent
RESULTS = Path(r"C:\ec\_deltar_scan_service_data\results")
sys.path.insert(0, str(PIPELINE / "src"))

from deltar_p4_pipeline.all_object_geometry import (  # noqa: E402
    AllObjectGeometryProcessor,
    IMAGE_SHAPE,
    _load_cached_masks,
    _load_cached_planes,
    load_geometry_profile,
)
from deltar_p4_pipeline.scan_contracts import load_strict_json  # noqa: E402


REPLAYS = {
    "G001": "scan_20260818_092411_numericfirst01",
    "G002": "scan_20260818_092729_numericfirst01",
    "SCAN_161718": "scan_20260818_161718_numericfirst01",
    "SCAN_165011": "scan_20260818_165011_numericfirst01",
    "SCAN_070604": "scan_20260819_070604_numericfirst01",
    "SCAN_085223": "scan_20260819_085223_numericfirst01",
}
TOLERANCE = 1e-12


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def frame_signature(frame: dict[str, Any]) -> dict[str, Any]:
    output = {}
    for obj in frame["objects"]:
        diagnostic = (
            obj.get("diagnostics", {})
            .get("generic_support", {})
            .get("diagnostics", {})
            .get("m3_w4")
        )
        output[obj["logical_object_id"]] = {
            "class_name": obj["class_name"],
            "numeric_available": obj["numeric_available"],
            "geometry_status": obj["geometry_status"],
            "support_mm": obj.get("geometry", {}).get("support_height_mm"),
            "top_mm": obj.get("geometry", {}).get("top_height_above_floor_mm"),
            "object_height_mm": obj.get("geometry", {}).get("object_height_mm"),
            "context_members": diagnostic.get("member_object_ids") if diagnostic else None,
            "mode_centers_mm": (
                [row["center_mm"] for row in diagnostic["retained_modes"]]
                if diagnostic
                else None
            ),
            "mode_probabilities": (
                [row["probability"] for row in diagnostic["retained_modes"]]
                if diagnostic
                else None
            ),
            "w4_support_mm": diagnostic.get("support_mm") if diagnostic else None,
        }
    return output


def compare(expected: Any, actual: Any, path: str, differences: list[dict[str, Any]]) -> float:
    if isinstance(expected, dict) and isinstance(actual, dict):
        if set(expected) != set(actual):
            differences.append(
                {"path": path, "reason": "key_set", "expected": sorted(expected), "actual": sorted(actual)}
            )
            return float("inf")
        return max(
            (compare(expected[key], actual[key], f"{path}.{key}", differences) for key in expected),
            default=0.0,
        )
    if isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            differences.append(
                {"path": path, "reason": "length", "expected": len(expected), "actual": len(actual)}
            )
            return float("inf")
        return max(
            (compare(left, right, f"{path}[{index}]", differences) for index, (left, right) in enumerate(zip(expected, actual))),
            default=0.0,
        )
    if isinstance(expected, (int, float)) and not isinstance(expected, bool) and isinstance(actual, (int, float)) and not isinstance(actual, bool):
        difference = abs(float(expected) - float(actual))
        if difference > TOLERANCE:
            differences.append(
                {"path": path, "reason": "numeric", "expected": expected, "actual": actual, "abs_diff": difference}
            )
        return difference
    if expected != actual:
        differences.append({"path": path, "reason": "value", "expected": expected, "actual": actual})
        return float("inf")
    return 0.0


def process_one(
    processor: AllObjectGeometryProcessor,
    root: Path,
    frame_path: Path,
) -> dict[str, Any]:
    frame = load_strict_json(frame_path, code="E_M3_W4_INDEPENDENCE")
    frame_id = str(frame["frame_id"])
    sequence = int(frame["sequence"])
    stage7d = root / "stage7d" / frame_id / "axial_z"
    axial = np.asarray(
        np.load(stage7d / "depth_source_640x360.npy", mmap_mode="r"), dtype=np.float64
    ) * 1000.0
    world_z = np.asarray(
        np.load(stage7d / "world_height_mm.npy", mmap_mode="r"), dtype=np.float64
    )
    if axial.shape != IMAGE_SHAPE or world_z.shape != IMAGE_SHAPE:
        raise ValueError(f"invalid cached shape for {frame_id}")
    source_image = root / "logs" / frame_id / "source_rgb.png"
    if not source_image.exists():
        source_image = root / "stage7d" / frame_id / "source_rgb.png"
    return processor.process_frame(
        frame_id,
        sequence,
        list(frame.get("logical_object_groups", [])),
        _load_cached_masks(root, sequence),
        axial,
        world_z,
        _load_cached_planes(stage7d),
        source_image if source_image.exists() else None,
    )


def main() -> int:
    profile = load_geometry_profile(PIPELINE / "profiles" / "formal_geometry_region_profile.json")
    comparisons = []
    maximum = 0.0
    difference_count = 0
    for scan, result_name in REPLAYS.items():
        root = RESULTS / result_name
        baseline = json.loads(
            (WORKSPACE / "production_replays" / f"{scan}.stage8b_all_object_geometry.v1.json").read_text(
                encoding="utf-8"
            )
        )
        expected = {frame["frame_id"]: frame_signature(frame) for frame in baseline["frames"]}
        frame_paths = sorted((root / "frame_results").glob("frame_*.json"))
        orders = {
            "normal": list(frame_paths),
            "reverse": list(reversed(frame_paths)),
            "random_seed_20260819": random.Random(20260819).sample(frame_paths, len(frame_paths)),
        }
        for order_name, ordered_paths in orders.items():
            processor = AllObjectGeometryProcessor(root, result_name, 11, profile)
            for frame_path in ordered_paths:
                actual_frame = process_one(processor, root, frame_path)
                frame_id = actual_frame["frame_id"]
                differences: list[dict[str, Any]] = []
                frame_max = compare(expected[frame_id], frame_signature(actual_frame), frame_id, differences)
                maximum = max(maximum, frame_max)
                difference_count += len(differences)
                comparisons.append(
                    {
                        "scan": scan,
                        "run": order_name,
                        "frame_id": frame_id,
                        "max_abs_diff": frame_max,
                        "difference_count": len(differences),
                        "differences": differences[:10],
                        "pass": not differences,
                    }
                )
        for frame_path in frame_paths:
            processor = AllObjectGeometryProcessor(root, result_name, 11, profile)
            actual_frame = process_one(processor, root, frame_path)
            frame_id = actual_frame["frame_id"]
            differences = []
            frame_max = compare(expected[frame_id], frame_signature(actual_frame), frame_id, differences)
            maximum = max(maximum, frame_max)
            difference_count += len(differences)
            comparisons.append(
                {
                    "scan": scan,
                    "run": "isolated",
                    "frame_id": frame_id,
                    "max_abs_diff": frame_max,
                    "difference_count": len(differences),
                    "differences": differences[:10],
                    "pass": not differences,
                }
            )
    passed = difference_count == 0
    audit = {
        "schema_version": "m3_w4_cross_frame_independence_audit.v1",
        "runtime_uses_cross_frame_information": False,
        "tolerance": TOLERANCE,
        "scan_count": len(REPLAYS),
        "frame_count": 66,
        "run_forms": ["normal", "reverse", "random_seed_20260819", "isolated"],
        "comparison_count": len(comparisons),
        "maximum_abs_difference": maximum,
        "difference_count": difference_count,
        "single_frame_independence_pass": passed,
        "comparisons": comparisons,
    }
    write_json(WORKSPACE / "M3_W4_CROSS_FRAME_INDEPENDENCE_AUDIT.json", audit)
    (WORKSPACE / "M3_W4_CROSS_FRAME_INDEPENDENCE_AUDIT.md").write_text(
        "# M3 + W4 cross-frame independence audit\n\n"
        "Each of the 66 frozen frames was compared against the normal fresh-production replay "
        "under normal order, reverse order, a fixed random order, and a new processor containing "
        "only that isolated frame. The signature includes public support/extents/status plus M3 mode "
        "centers, probabilities, context members, and W4 output.\n\n"
        f"- Comparisons: {len(comparisons)}\n"
        f"- Difference count: {difference_count}\n"
        f"- Maximum absolute numeric difference: {maximum:.12g}\n"
        f"- Strict tolerance: {TOLERANCE:g}\n"
        f"- SINGLE_FRAME_INDEPENDENCE_PASS: **{passed}**\n",
        encoding="utf-8",
    )
    print(json.dumps({"pass": passed, "comparisons": len(comparisons), "differences": difference_count, "max": maximum}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
