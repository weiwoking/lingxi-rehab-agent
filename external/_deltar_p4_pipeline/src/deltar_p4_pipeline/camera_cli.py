from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .camera_calibration import calibrate_camera_charuco
from .camera_calibration_contracts import CameraCalibrationError
from .camera_orientation import create_orientation_manifest, inspect_orientation_image
from .charuco_board import generate_charuco_board


def add_camera_parsers(subparsers: argparse._SubParsersAction) -> None:
    orientation = subparsers.add_parser("camera-orientation-check", help="check the frozen 640x360 landscape image contract")
    orientation.add_argument("--image", type=Path, required=True)
    orientation.add_argument("--confirm-upright", action="store_true")
    orientation.add_argument("--output-manifest", type=Path)
    orientation.add_argument("--notes", default="")
    board = subparsers.add_parser("generate-charuco-board", help="generate a reproducible A4 OpenCV ChArUco board")
    board.add_argument("--squares-x", type=int, default=7)
    board.add_argument("--squares-y", type=int, default=5)
    board.add_argument("--square-length-mm", type=float, default=25.0)
    board.add_argument("--marker-length-mm", type=float, default=18.0)
    board.add_argument("--dictionary", default="DICT_5X5_100")
    board.add_argument("--output-dir", type=Path, required=True)
    board.add_argument("--page-size", choices=("A4",), default="A4")
    board.add_argument("--render-dpi", type=int, default=300)
    calibration = subparsers.add_parser("camera-calibrate-charuco", help="fit Brown intrinsics and verify on a deterministic held-out set")
    calibration.add_argument("--images-dir", type=Path, required=True)
    calibration.add_argument("--board-manifest", type=Path, required=True)
    calibration.add_argument("--orientation-manifest", type=Path, required=True)
    calibration.add_argument("--output-dir", type=Path, required=True)
    calibration.add_argument("--seed", type=int, default=7)
    calibration.add_argument("--heldout-ratio", type=float, default=0.2)
    calibration.add_argument("--min-valid-images", type=int, default=25)
    calibration.add_argument("--min-charuco-corners", type=int, default=8)
    calibration.add_argument("--accept-demo-only", action="store_true")


def dispatch_camera_command(args: argparse.Namespace) -> int | None:
    if args.command not in {"camera-orientation-check", "generate-charuco-board", "camera-calibrate-charuco"}:
        return None
    try:
        if args.command == "camera-orientation-check":
            result = inspect_orientation_image(args.image)
            if args.confirm_upright or args.output_manifest is not None:
                output = args.output_manifest or Path("camera_orientation_manifest.v1.json")
                manifest = create_orientation_manifest(args.image, output, confirm_upright=args.confirm_upright, notes=args.notes)
                result["manifest"] = str(output.resolve())
                result["scene_upright_confirmed"] = manifest["scene_upright_confirmed"]
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "generate-charuco-board":
            manifest = generate_charuco_board(
                args.output_dir, squares_x=args.squares_x, squares_y=args.squares_y,
                square_length_mm=args.square_length_mm, marker_length_mm=args.marker_length_mm,
                dictionary=args.dictionary, page_size=args.page_size, render_dpi=args.render_dpi,
            )
            print(json.dumps({"status": "generated", "board_id": manifest["board_id"], "output_dir": str(args.output_dir.resolve()), "sha256": manifest["board_sha256"]}, ensure_ascii=False))
            return 0
        result = calibrate_camera_charuco(
            args.images_dir, args.board_manifest, args.orientation_manifest, args.output_dir,
            seed=args.seed, heldout_ratio=args.heldout_ratio, min_valid_images=args.min_valid_images,
            min_charuco_corners=args.min_charuco_corners, accept_demo_only=args.accept_demo_only,
        )
        print(json.dumps({"status": result["report"]["status"], "profile_id": result["profile"]["profile_id"], "output_dir": str(args.output_dir.resolve())}, ensure_ascii=False))
        return 0
    except CameraCalibrationError as exc:
        print(json.dumps({"status": "failed", "error": exc.as_dict()}, ensure_ascii=False), file=sys.stderr)
        return 2


def _standalone(command: str, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_camera_parsers(subparsers)
    return int(dispatch_camera_command(parser.parse_args([command, *(argv if argv is not None else sys.argv[1:])])) or 0)


def main_generate_board() -> int:
    return _standalone("generate-charuco-board")


def main_orientation() -> int:
    return _standalone("camera-orientation-check")


def main_calibrate() -> int:
    return _standalone("camera-calibrate-charuco")

