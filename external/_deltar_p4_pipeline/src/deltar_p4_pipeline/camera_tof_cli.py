from __future__ import annotations

import argparse
import json
from pathlib import Path

from .camera_tof_calibration import (
    calibrate_capture,
    manifest_template,
    validate_capture_with_profile,
)
from .camera_tof_contracts import CameraTofError
from .camera_calibration_contracts import CameraCalibrationError
from .errors import PipelineError
from .scan_contracts import ScanContractError

EXIT_SUCCESS = 0
EXIT_INPUT = 2
EXIT_CALIBRATION_FAILED = 8

DEFAULT_CAMERA_PROFILE = Path(r"C:\calibration\result_20260719_172703\calibration_profile.v2.json")
DEFAULT_BOARD_MANIFEST = Path(__file__).resolve().parents[2] / "board_assets" / "board_manifest.json"


def add_camera_tof_parsers(subparsers: argparse._SubParsersAction) -> None:
    template = subparsers.add_parser(
        "camera-tof-manifest-template",
        help="write a strict 12-pose Stage 7B capture manifest template",
    )
    template.add_argument("--output", type=Path, required=True)
    template.add_argument("--camera-profile", type=Path, default=DEFAULT_CAMERA_PROFILE)
    template.add_argument("--board-manifest", type=Path, default=DEFAULT_BOARD_MANIFEST)

    calibrate = subparsers.add_parser(
        "camera-tof-calibrate",
        help="solve and held-out validate SC2336-to-VL53L5CX extrinsics",
    )
    calibrate.add_argument("--capture-manifest", type=Path, required=True)
    calibrate.add_argument("--camera-profile", type=Path, required=True)
    calibrate.add_argument("--board-manifest", type=Path, required=True)
    calibrate.add_argument("--output-dir", type=Path, required=True)
    calibrate.add_argument("--profile-bundle", type=Path, default=Path(__file__).resolve().parents[2] / "profiles")
    calibrate.add_argument("--include-status-6", action="store_true", help="explicit ablation only; default remains statuses 5/9")

    validate = subparsers.add_parser(
        "camera-tof-validate",
        help="score a frozen PASS extrinsics profile without re-optimizing",
    )
    validate.add_argument("--profile", type=Path, required=True)
    validate.add_argument("--capture-manifest", type=Path, required=True)
    validate.add_argument("--output-dir", type=Path, required=True)
    validate.add_argument("--profile-bundle", type=Path, default=Path(__file__).resolve().parents[2] / "profiles")


def dispatch_camera_tof_command(args: argparse.Namespace) -> int | None:
    if args.command not in {
        "camera-tof-manifest-template", "camera-tof-calibrate", "camera-tof-validate",
    }:
        return None
    try:
        if args.command == "camera-tof-manifest-template":
            value = manifest_template(
                args.output, camera_profile=args.camera_profile,
                board_manifest=args.board_manifest,
            )
            print(json.dumps({
                "status": "created", "schema_version": value["schema_version"],
                "poses": len(value["poses"]), "output": str(args.output.resolve()),
            }, ensure_ascii=False))
            return EXIT_SUCCESS
        if args.command == "camera-tof-calibrate":
            report = calibrate_capture(
                args.capture_manifest, args.camera_profile, args.board_manifest,
                args.output_dir, profile_bundle_path=args.profile_bundle,
                include_status_6=args.include_status_6,
            )
        else:
            report = validate_capture_with_profile(
                args.profile, args.capture_manifest, args.output_dir,
                profile_bundle_path=args.profile_bundle,
            )
        print(json.dumps({
            "status": report["status"], "validation_status": report["validation_status"],
            "output": str(args.output_dir.resolve()),
            "formal_profile_generated": report["formal_profile_generated"],
        }, ensure_ascii=False))
        return EXIT_SUCCESS if report["status"] == "PASS" else EXIT_CALIBRATION_FAILED
    except CameraTofError as exc:
        print(json.dumps({"status": "failed", "error": exc.as_dict()}, ensure_ascii=False))
        return EXIT_INPUT if exc.code.startswith(("E_CAMERA_TOF_JSON", "E_CAMERA_TOF_SCHEMA", "E_CAMERA_TOF_PATH", "E_CAMERA_TOF_HASH", "E_CAMERA_TOF_OUTPUT")) else EXIT_CALIBRATION_FAILED
    except (CameraCalibrationError, ScanContractError, PipelineError) as exc:
        error = {
            "code": "E_CAMERA_TOF_CAPTURE",
            "message": str(getattr(exc, "message", exc)),
            "field": getattr(exc, "field", None),
            "details": getattr(exc, "details", None),
        }
        print(json.dumps({"status": "failed", "error": error}, ensure_ascii=False))
        return EXIT_CALIBRATION_FAILED
