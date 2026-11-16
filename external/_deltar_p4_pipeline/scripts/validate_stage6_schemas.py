from __future__ import annotations

import json
from pathlib import Path

from deltar_p4_pipeline.scan_profiles import load_profile_bundle
from deltar_p4_pipeline.scan_validator import validate_scan

ROOT = Path(__file__).resolve().parents[1]
SCANS = Path(r"C:\ec\_deltar_stage6_test_scans")


def main() -> None:
    bundle = load_profile_bundle(ROOT / "profiles")
    valid = []
    invalid = {}
    for name in ("single_valid", "multi_same_object", "multi_two_objects_same_class", "multi_partial_best_frame", "unmeasured_calibration"):
        scan = validate_scan(SCANS / name, bundle)
        valid.append({"scan_id": scan.scan["scan_id"], "frames": len(scan.frames)})
    for name in ("invalid_image_size", "invalid_tof_length", "invalid_path"):
        try:
            validate_scan(SCANS / name, bundle)
        except Exception as exc:
            invalid[name] = {"rejected": True, "error": str(exc)}
        else:
            invalid[name] = {"rejected": False}
    payload = {"schema_version": "deltar_stage6_schema_validation.v1", "status": "pass" if all(item["rejected"] for item in invalid.values()) else "fail",
               "schemas": ["deltar_scan_input.v1", "deltar_scan_frame.v1", "rehab_tof_raw.v1", "deltar_scan_result.v1", "deltar_pc_job_report.v1"],
               "valid_scans": valid, "invalid_scans": invalid}
    historical = ROOT / "outputs_stage6" / "schema_validation.json"
    if not historical.is_file():
        raise SystemExit("historical outputs_stage6/schema_validation.json is missing; refusing to recreate it in Stage 6.1")
    print(json.dumps({"status": payload["status"], "valid": len(valid), "invalid_rejected": sum(item["rejected"] for item in invalid.values())}))
    raise SystemExit(0 if payload["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
