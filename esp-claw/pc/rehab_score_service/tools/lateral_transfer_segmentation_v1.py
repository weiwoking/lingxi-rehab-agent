from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from feature_extractors.lateral_transfer_profile import SEGMENTATION_CORE_VERSION, SEGMENTATION_VERSION
from tools import drink_water_v1_pipeline as drink_pipeline


CSV_ENCODING = drink_pipeline.CSV_ENCODING
LATERAL_WRAPPER_VERSION = SEGMENTATION_VERSION
LATERAL_SEGMENTATION_STRATEGY = "REUSE_DRINK_HYBRID_SEGMENTATION_V2_1_1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _adapt_repetition(rep: dict[str, Any]) -> dict[str, Any]:
    adapted = dict(rep)
    adapted["motion_family"] = "lateral_transfer"
    adapted["segmentation_wrapper_version"] = LATERAL_WRAPPER_VERSION
    adapted["segmentation_core_version"] = SEGMENTATION_CORE_VERSION
    adapted["segmentation_strategy"] = LATERAL_SEGMENTATION_STRATEGY
    adapted["core_split_method"] = rep.get("split_method")
    adapted["split_method"] = LATERAL_WRAPPER_VERSION
    adapted["segmentation_source"] = "drink_hybrid_reuse"
    adapted["expected_count_used_by_segmenter"] = False
    adapted["task_id_used_by_segmenter"] = False
    return adapted


def segment_lateral_transfer_file(csv_path: Path) -> tuple[list[dict[str, Any]], dict[str, Any], pd.DataFrame]:
    reps, drink_diagnostic, df = drink_pipeline.segment_file(csv_path)
    adapted_reps = [_adapt_repetition(rep) for rep in reps]
    diagnostic = {
        "segmentation_version": LATERAL_WRAPPER_VERSION,
        "segmentation_wrapper_version": LATERAL_WRAPPER_VERSION,
        "segmentation_core_version": SEGMENTATION_CORE_VERSION,
        "segmentation_strategy": LATERAL_SEGMENTATION_STRATEGY,
        "segmentation_source": "drink_hybrid_reuse",
        "motion_family": "lateral_transfer",
        "source_csv": str(csv_path),
        "source_sha256": sha256_file(csv_path),
        "row_count": int(len(df)),
        "candidate_rep_count": len(adapted_reps),
        "valid_rep_count": sum(1 for rep in adapted_reps if rep.get("valid") is True),
        "expected_count_used_by_segmenter": False,
        "task_id_used_by_segmenter": False,
        "lateral_independent_segmenter_present": False,
        "compatibility_adapter": "metadata_only",
        "core_diagnostic": drink_pipeline.make_json_safe(drink_diagnostic),
        "notes": [
            "lateral_transfer intentionally reuses the frozen drink_hybrid_segmentation_v2_1_1 core",
            "this wrapper only adapts metadata/schema for lateral_transfer downstream tools",
            "expected labels and task ids are not inputs to the segmentation core",
        ],
    }
    return adapted_reps, diagnostic, df


def write_segmentation_json(csv_path: Path, output_path: Path) -> None:
    reps, diagnostic, _df = segment_lateral_transfer_file(csv_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"repetitions": reps, "diagnostic": diagnostic}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
