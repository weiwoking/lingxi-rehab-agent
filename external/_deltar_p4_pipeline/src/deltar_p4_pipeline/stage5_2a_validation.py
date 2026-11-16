from __future__ import annotations

from pathlib import Path
from typing import Any

EXPECTED_COMMITS = {
    "esp_claw": "8aa863e07fe1ef4b3e4a21004f87a89a9beb9644",
    "deltar": "f3c517562bcb2dfb9e4bbf74965f2ee76a2dd096",
    "calib_deltar": "0dd16ef96fa76598cf542bba9b57f96548d27e35",
}
EXPECTED_HASHES = {
    "deltar": "a402ffbffade70f0a0ef77a28b465d8fb625dd2fcdef0115b65c98193ce1e3ef",
    "yolo_detect": "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1",
    "yolo_segment": "55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152",
}


def acceptance(matched_count: int, paired_count: int, class_count: int, old: dict[str, Any], new: dict[str, Any], mixed: dict[str, Any]) -> dict[str, Any]:
    coverage_ok = paired_count >= 50 and class_count >= 4
    old_abs = old["absolute_error_m"]
    new_abs = new["absolute_error_m"]
    checks = {
        "paired_valid_at_least_50": paired_count >= 50,
        "classes_at_least_4": class_count >= 4,
        "median_absolute_error_improved": new_abs["median"] is not None and old_abs["median"] is not None and new_abs["median"] < old_abs["median"],
        "relative_error_median_improved": new["relative_error"]["median"] is not None and old["relative_error"]["median"] is not None and new["relative_error"]["median"] < old["relative_error"]["median"],
        "p90_not_materially_worse": new_abs["p90"] is not None and old_abs["p90"] is not None and new_abs["p90"] <= old_abs["p90"] * 1.05,
        "within_10_percent_rate_higher": new["within_10_percent_rate"] > old["within_10_percent_rate"],
        "mixed_zone_better": bool(mixed.get("new_better")),
    }
    passed = coverage_ok and all(checks.values())
    return {
        "schema_version": "deltar_stage5_2a_acceptance.v1",
        "verdict": "PASS" if passed else "PARTIAL",
        "matched_object_count": matched_count,
        "paired_valid_count": paired_count,
        "class_count": class_count,
        "checks": checks,
        "stage6_authorized": False,
        "hybrid_chain_implemented": False,
        "decision": "continue_new_deltar_without_default_hybrid" if passed else "investigate_inputs_and_regions_before_stage6",
    }
