from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .camera_calibration_contracts import (
    BOARD_SCHEMA,
    canonical_sha256,
    fail,
    sha256_file,
    validate_board_manifest,
    write_json,
)


SUPPORTED_DICTIONARIES = {
    name: getattr(cv2.aruco, name)
    for name in (
        "DICT_4X4_50", "DICT_4X4_100", "DICT_5X5_50", "DICT_5X5_100",
        "DICT_5X5_250", "DICT_6X6_100", "DICT_6X6_250",
    )
    if hasattr(cv2.aruco, name)
}


def dictionary_by_name(name: str) -> Any:
    if name not in SUPPORTED_DICTIONARIES:
        fail("E_BOARD_DICTIONARY", "unsupported or unavailable ArUco dictionary", "dictionary", sorted(SUPPORTED_DICTIONARIES))
    return cv2.aruco.getPredefinedDictionary(SUPPORTED_DICTIONARIES[name])


def charuco_board_from_manifest(manifest: dict[str, Any]) -> Any:
    validate_board_manifest(manifest, require_print_measurement=False)
    return cv2.aruco.CharucoBoard(
        (manifest["squares_x"], manifest["squares_y"]),
        float(manifest["square_length_mm"]) / 1000.0,
        float(manifest["marker_length_mm"]) / 1000.0,
        dictionary_by_name(manifest["dictionary"]),
    )


def generate_charuco_board(
    output_dir: Path,
    *,
    squares_x: int = 7,
    squares_y: int = 5,
    square_length_mm: float = 25.0,
    marker_length_mm: float = 18.0,
    dictionary: str = "DICT_5X5_100",
    page_size: str = "A4",
    render_dpi: int = 300,
) -> dict[str, Any]:
    if page_size != "A4":
        fail("E_BOARD_PRINT", "only A4 is supported in Stage 7A", "page_size")
    if type(squares_x) is not int or type(squares_y) is not int or squares_x < 3 or squares_y < 3:
        fail("E_BOARD_DIMENSIONS", "squares_x/y must be integers >= 3", "squares_x/squares_y")
    if square_length_mm <= 0 or marker_length_mm <= 0 or marker_length_mm >= square_length_mm:
        fail("E_BOARD_DIMENSIONS", "require 0 < marker_length_mm < square_length_mm", "square_length_mm/marker_length_mm")
    if type(render_dpi) is not int or render_dpi < 150 or render_dpi > 1200:
        fail("E_BOARD_PRINT", "render DPI must be in 150..1200", "render_dpi")
    board_width_mm = squares_x * square_length_mm
    board_height_mm = squares_y * square_length_mm
    page_width_mm, page_height_mm = 210.0, 297.0
    margin_mm = 10.0
    if board_width_mm > page_width_mm - 2 * margin_mm or board_height_mm > page_height_mm - 2 * margin_mm:
        fail("E_BOARD_PRINT", "board does not fit A4 with 10 mm margins", "board dimensions", {
            "board_width_mm": board_width_mm, "board_height_mm": board_height_mm,
        })
    pixels_per_mm = render_dpi / 25.4
    board_pixels = (round(board_width_mm * pixels_per_mm), round(board_height_mm * pixels_per_mm))
    page_pixels = (round(page_width_mm * pixels_per_mm), round(page_height_mm * pixels_per_mm))
    dictionary_object = dictionary_by_name(dictionary)
    board = cv2.aruco.CharucoBoard(
        (squares_x, squares_y), square_length_mm / 1000.0, marker_length_mm / 1000.0, dictionary_object,
    )
    rendered = board.generateImage(board_pixels, marginSize=0, borderBits=1)
    page = np.full((page_pixels[1], page_pixels[0]), 255, dtype=np.uint8)
    left = (page.shape[1] - rendered.shape[1]) // 2
    top = (page.shape[0] - rendered.shape[0]) // 2
    page[top:top + rendered.shape[0], left:left + rendered.shape[1]] = rendered
    output_dir.mkdir(parents=True, exist_ok=True)
    image_path = output_dir / "board.png"
    if not cv2.imwrite(str(image_path), page, [cv2.IMWRITE_PNG_COMPRESSION, 9]):
        fail("E_BOARD_WRITE", "OpenCV failed to write board PNG", str(image_path))
    parameters = {
        "family": "opencv_charuco", "dictionary": dictionary, "squares_x": squares_x, "squares_y": squares_y,
        "square_length_mm": float(square_length_mm), "marker_length_mm": float(marker_length_mm),
        "page_size": page_size, "print_scale": 1.0, "render_dpi": render_dpi,
    }
    board_id = "charuco_" + canonical_sha256(parameters)[:16]
    manifest = {
        "schema_version": BOARD_SCHEMA,
        "board_id": board_id,
        **parameters,
        "board_image": image_path.name,
        "board_sha256": sha256_file(image_path),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "print_measurement": {
            "confirmed": False,
            "measured_square_length_mm": None,
            "measured_board_width_mm": None,
            "measured_board_height_mm": None,
            "measured_at": None,
            "tolerance_percent": 1.0,
        },
    }
    validate_board_manifest(manifest)
    write_json(output_dir / "board_manifest.json", manifest)
    (output_dir / "print_check.md").write_text(
        "# ChArUco print check\n\n"
        "Print `board.png` on A4 at **100% / actual size**. Disable ‘fit to page’, scaling, and borderless enlargement.\n\n"
        f"OpenCV `squares_x={squares_x}` and `squares_y={squares_y}` mean chessboard squares across and down; "
        f"there are `{(squares_x - 1) * (squares_y - 1)}` internal ChArUco corners.\n\n"
        "After printing, use a reliable ruler or caliper and record:\n\n"
        f"- one square side (nominal `{square_length_mm:.3f} mm`);\n"
        f"- complete active board width (nominal `{board_width_mm:.3f} mm`);\n"
        f"- complete active board height (nominal `{board_height_mm:.3f} mm`).\n\n"
        "Copy the measured values into `board_manifest.json`, set `print_measurement.confirmed=true`, and add an RFC3339 "
        "`measured_at`. The calibration command rejects an unconfirmed or out-of-tolerance print. Do not trust printer settings alone.\n",
        encoding="utf-8",
    )
    return manifest

