from __future__ import annotations

import argparse
import json
from pathlib import Path

from .camera_calibration_contracts import CameraCalibrationError
from .camera_tof_contracts import CameraTofError
from .camera_world_calibration import (
    calibrate_capture,
    manifest_template,
    validate_capture_with_profile,
)
from .camera_world_contracts import (
    FROZEN_CAMERA_TOF_PROFILE,
    CameraWorldError,
)
from .errors import PipelineError
from .scan_contracts import ScanContractError


EXIT_SUCCESS = 0
EXIT_INPUT = 2
EXIT_CALIBRATION_FAILED = 8
DEFAULT_CAMERA_PROFILE = Path(
    r"C:\calibration\result_20260719_172703\calibration_profile.v2.json"
)
DEFAULT_BOARD_MANIFEST = (
    Path(__file__).resolve().parents[2]
    / "board_assets"
    / "board_manifest.json"
)
DEFAULT_PROFILE_BUNDLE = Path(__file__).resolve().parents[2] / "profiles"


def add_camera_world_parsers(
    subparsers: argparse._SubParsersAction,
) -> None:
    template = subparsers.add_parser(
        "camera-world-manifest-template",
        help=(
            "write a strict Stage 7C-1 near/mid/far x 5-frame capture "
            "manifest bound to the frozen Stage 7A/7B profiles"
        ),
    )
    template.add_argument(
        "--output", type=Path, required=True,
        help="new JSON path; existing files are never overwritten",
    )
    template.add_argument(
        "--camera-profile", type=Path, default=DEFAULT_CAMERA_PROFILE,
        help="frozen Stage 7A Camera intrinsics profile",
    )
    template.add_argument(
        "--camera-tof-profile", type=Path,
        default=FROZEN_CAMERA_TOF_PROFILE,
        help="must be the one frozen absolute Stage 7B profile path",
    )
    template.add_argument(
        "--board-manifest", type=Path, default=DEFAULT_BOARD_MANIFEST,
        help="measured ChArUco board manifest",
    )

    calibrate = subparsers.add_parser(
        "camera-world-calibrate",
        help=(
            "solve robust Camera world orientation, compose the frozen "
            "Camera-ToF transform and validate the vertical board"
        ),
    )
    calibrate.add_argument(
        "--capture-manifest", type=Path, required=True,
        help="strict camera_world_capture.v1 manifest",
    )
    calibrate.add_argument(
        "--camera-profile", type=Path, required=True,
        help="Stage 7A profile exactly matching the manifest path/hash/id",
    )
    calibrate.add_argument(
        "--camera-tof-profile", type=Path, required=True,
        help=(
            "must be C:\\calibration\\stage7b_result_unitfix_20260724_012041"
            "\\camera_tof_extrinsics.v1.json with the frozen SHA256"
        ),
    )
    calibrate.add_argument(
        "--board-manifest", type=Path, required=True,
        help="board manifest exactly matching the capture manifest",
    )
    calibrate.add_argument(
        "--output-dir", type=Path, required=True,
        help="must not exist or must be an empty directory",
    )
    calibrate.add_argument(
        "--profile-bundle", type=Path, default=DEFAULT_PROFILE_BUNDLE,
        help="Stage 6 scan-validation profile bundle",
    )

    validate = subparsers.add_parser(
        "camera-world-validate",
        help=(
            "strictly validate a formal PASS camera_world_pose.v1 profile "
            "against its frozen near/mid/far capture without re-optimizing "
            "Camera-ToF extrinsics"
        ),
    )
    validate.add_argument(
        "--profile", type=Path, required=True,
        help="formal camera_world_pose.v1.json",
    )
    validate.add_argument(
        "--capture-manifest", type=Path, required=True,
        help="original strict camera_world_capture.v1 manifest",
    )
    validate.add_argument(
        "--output-dir", type=Path, required=True,
        help="must not exist or must be an empty directory",
    )
    validate.add_argument(
        "--profile-bundle", type=Path, default=DEFAULT_PROFILE_BUNDLE,
        help="Stage 6 scan-validation profile bundle",
    )


def _error_code(exc: CameraWorldError) -> int:
    input_prefixes = (
        "E_CAMERA_WORLD_JSON",
        "E_CAMERA_WORLD_SCHEMA",
        "E_CAMERA_WORLD_PATH",
        "E_CAMERA_WORLD_HASH",
        "E_CAMERA_WORLD_OUTPUT",
        "E_CAMERA_WORLD_STAGE7B_BINDING",
        "E_CAMERA_WORLD_PROFILE_ID",
        "E_CAMERA_WORLD_CAMERA_PROFILE",
        "E_CAMERA_WORLD_BOARD",
        "E_CAMERA_WORLD_FRAME",
        "E_CAMERA_WORLD_HEIGHT",
        "E_CAMERA_WORLD_ALIGNMENT",
    )
    return EXIT_INPUT if exc.code.startswith(input_prefixes) else EXIT_CALIBRATION_FAILED


def dispatch_camera_world_command(args: argparse.Namespace) -> int | None:
    commands = {
        "camera-world-manifest-template",
        "camera-world-calibrate",
        "camera-world-validate",
    }
    if args.command not in commands:
        return None
    try:
        if args.command == "camera-world-manifest-template":
            value = manifest_template(
                args.output,
                camera_profile=args.camera_profile,
                camera_tof_profile=args.camera_tof_profile,
                board_manifest=args.board_manifest,
            )
            print(
                json.dumps(
                    {
                        "status": "created",
                        "schema_version": value["schema_version"],
                        "pose_count": len(value["poses"]),
                        "required_frame_count": 15,
                        "camera_height_mm": value["camera_height_mm"],
                        "output": str(args.output.resolve()),
                    },
                    ensure_ascii=False,
                )
            )
            return EXIT_SUCCESS
        if args.command == "camera-world-calibrate":
            report = calibrate_capture(
                args.capture_manifest,
                args.camera_profile,
                args.camera_tof_profile,
                args.board_manifest,
                args.output_dir,
                profile_bundle_path=args.profile_bundle,
            )
        else:
            report = validate_capture_with_profile(
                args.profile,
                args.capture_manifest,
                args.output_dir,
                profile_bundle_path=args.profile_bundle,
            )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "validation_status": report["validation_status"],
                    "output": str(args.output_dir.resolve()),
                    "formal_profile_generated": report[
                        "formal_profile_generated"
                    ],
                    "absolute_geometry_valid": False,
                },
                ensure_ascii=False,
            )
        )
        return (
            EXIT_SUCCESS
            if report["status"] == "PASS"
            else EXIT_CALIBRATION_FAILED
        )
    except CameraWorldError as exc:
        print(
            json.dumps(
                {"status": "failed", "error": exc.as_dict()},
                ensure_ascii=False,
            )
        )
        return _error_code(exc)
    except (
        CameraCalibrationError,
        CameraTofError,
        ScanContractError,
        PipelineError,
    ) as exc:
        error = {
            "code": "E_CAMERA_WORLD_CAPTURE",
            "message": str(getattr(exc, "message", exc)),
            "field": getattr(exc, "field", None),
            "details": getattr(exc, "details", None),
        }
        print(
            json.dumps(
                {"status": "failed", "error": error},
                ensure_ascii=False,
            )
        )
        return EXIT_CALIBRATION_FAILED
