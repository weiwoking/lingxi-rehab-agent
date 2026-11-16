from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .frame_pipeline import FramePipelineError
from .scan_contracts import RESULT_SCHEMA, ScanContractError, load_strict_json, validate_result_shape
from .scan_job import run_scan
from .scan_profiles import load_profile_bundle
from .scan_validator import validate_scan
from .stage6_schema import SCAN_RESULT_SCHEMA, validate_scan_result_v1_1

EXIT_SUCCESS = 0
EXIT_INPUT = 2
EXIT_PROFILE = 3
EXIT_MODEL = 4
EXIT_FRAME = 5
EXIT_AGGREGATION = 6
EXIT_OUTPUT = 7


def add_scan_parsers(subparsers: argparse._SubParsersAction) -> None:
    validate = subparsers.add_parser("scan-validate", help="strictly validate a frozen Stage 6 scan directory")
    validate.add_argument("--scan-dir", type=Path, required=True)
    validate.add_argument("--profile-bundle", type=Path, default=Path(__file__).resolve().parents[2] / "profiles")
    run = subparsers.add_parser("scan-run", help="run one scan through the frozen batch pipeline")
    run.add_argument("--scan-dir", type=Path, required=True)
    run.add_argument("--output-dir", type=Path, required=True)
    run.add_argument("--profile-bundle", type=Path, default=Path(__file__).resolve().parents[2] / "profiles")
    run.add_argument("--device", default="cuda:0")
    run.add_argument("--overwrite", choices=("false",), default="false")
    run.add_argument("--resume", action="store_true")
    run.add_argument(
        "--require-validation-receipt",
        action="store_true",
        help="require the internal service validation receipt; never falls back to full validation",
    )
    run.add_argument(
        "--defer-visualization",
        action="store_true",
        help="finish cached decision artifacts first; Stage 8B-3 is rendered by the service after package publication",
    )
    run.add_argument(
        "--stage7d-horizontal-planes",
        action="store_true",
        help="opt in to Stage 7D axial authority horizontal-plane processing",
    )
    run.add_argument(
        "--stage7d-diagnostics",
        action="store_true",
        help="generate additive Stage3A world-Z debug artifacts",
    )
    run.add_argument("--stage7d-profile", type=Path)
    inspect = subparsers.add_parser("scan-inspect", help="inspect and validate a Stage 6 result")
    inspect.add_argument("--result", type=Path, required=True)
    profile = subparsers.add_parser("profile-validate", help="validate profiles and all frozen model hashes")
    profile.add_argument("--profile-bundle", type=Path, default=Path(__file__).resolve().parents[2] / "profiles")


def dispatch_scan_command(args: argparse.Namespace) -> int | None:
    if args.command not in {"scan-validate", "scan-run", "scan-inspect", "profile-validate"}:
        return None
    try:
        if args.command == "profile-validate":
            bundle = load_profile_bundle(args.profile_bundle, validate_model_hashes=True)
            calibration_status = bundle.calibration["calibration_status"]
            print(json.dumps({
                "status": "valid", "validation_class": f"{calibration_status}_valid",
                "calibration_status": calibration_status, "profiles": bundle.ids, "model_hashes": "verified",
            }, ensure_ascii=False))
            return EXIT_SUCCESS
        if args.command == "scan-validate":
            bundle = load_profile_bundle(args.profile_bundle, validate_model_hashes=False)
            scan = validate_scan(args.scan_dir, bundle, max_frames=int(bundle.algorithm["limits"]["max_frames_per_scan"]),
                                 max_scan_bytes=int(bundle.algorithm["limits"]["max_scan_bytes"]))
            print(json.dumps({"status": "valid", "scan_id": scan.scan["scan_id"], "frame_count": len(scan.frames)}, ensure_ascii=False))
            return EXIT_SUCCESS
        if args.command == "scan-inspect":
            result = load_strict_json(args.result, code="E_SCAN_RESULT_SCHEMA")
            schema = result.get("schema_version")
            legacy = schema == RESULT_SCHEMA
            if legacy:
                validate_result_shape(result)
            elif schema == SCAN_RESULT_SCHEMA:
                validate_scan_result_v1_1(result)
            else:
                raise ScanContractError("E_RESULT_SCHEMA_VERSION", "unsupported result schema version", "schema_version")
            print(json.dumps({"status": result["status"], "scan_id": result["scan_id"], "objects": len(result["objects"]), "warnings": len(result["warnings"]), "schema_version": schema, "legacy": legacy}, ensure_ascii=False))
            return EXIT_SUCCESS
        result, _ = run_scan(args.scan_dir, args.output_dir, args.profile_bundle, device=args.device,
                             resume=args.resume, overwrite=args.overwrite != "false",
                             validation_receipt_required=args.require_validation_receipt,
                             stage7d_horizontal_planes=args.stage7d_horizontal_planes,
                             stage7d_diagnostics=args.stage7d_diagnostics,
                             stage7d_profile=args.stage7d_profile,
                              generate_full_visualization=not args.defer_visualization)
        print(json.dumps({"status": result["status"], "scan_id": result["scan_id"], "objects": len(result["objects"]), "output": str(args.output_dir)}, ensure_ascii=False))
        return EXIT_SUCCESS
    except ScanContractError as exc:
        print(json.dumps({"status": "failed", "error": exc.as_dict()}, ensure_ascii=False))
        if exc.code in {"E_PROFILE_BUNDLE", "E_PROFILE_HASH", "E_PROFILE_SCHEMA", "E_PROFILE_SEMANTIC", "E_STAGE7D_PROFILE"}:
            return EXIT_PROFILE
        if exc.code in {"E_SCAN_OUTPUT_EXISTS", "E_SCAN_OUTPUT_WRITE", "E_SCAN_RESULT_SCHEMA", "E_FRAME_RESULT_SCHEMA", "E_JOB_REPORT_SCHEMA", "E_RESULT_SCHEMA_VERSION"}:
            return EXIT_OUTPUT
        if exc.code == "E_SCAN_AGGREGATION":
            return EXIT_AGGREGATION
        return EXIT_INPUT
    except FramePipelineError as exc:
        traceback.print_exception(exc.__cause__ or exc, file=sys.stderr)
        print(json.dumps({
            "status": "failed",
            "error": {
                "code": exc.code,
                "phase": exc.phase,
                "message": exc.message,
                "details": {**exc.details, "error_type": exc.error_type},
            },
        }, ensure_ascii=False))
        return EXIT_MODEL if exc.phase == "model_inference" else EXIT_FRAME
