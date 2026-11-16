import copy

import cv2
import numpy as np
import pytest

from deltar_p4_pipeline.camera_calibration import detect_observation
from deltar_p4_pipeline.camera_calibration_contracts import CameraCalibrationError, validate_board_manifest
from deltar_p4_pipeline.charuco_board import charuco_board_from_manifest, generate_charuco_board


def test_board_generation_is_pixel_reproducible(tmp_path):
    first = generate_charuco_board(tmp_path / "a")
    second = generate_charuco_board(tmp_path / "b")
    assert first["board_id"] == second["board_id"]
    assert first["board_sha256"] == second["board_sha256"]
    assert first["squares_x"] == 7 and first["squares_y"] == 5


def test_generated_board_has_real_opencv_detector_path(tmp_path):
    manifest = generate_charuco_board(tmp_path / "asset")
    board = charuco_board_from_manifest(manifest)
    board_image = board.generateImage((500, 330), marginSize=10)
    canvas = np.full((360, 640), 255, np.uint8)
    canvas[15:345, 70:570] = board_image
    path = tmp_path / "detector.png"
    assert cv2.imwrite(str(path), canvas)
    observation = detect_observation(path, board, min_charuco_corners=8)
    assert observation.corner_count >= 20


def test_print_measurement_is_fail_closed(tmp_path):
    manifest = generate_charuco_board(tmp_path / "asset")
    with pytest.raises(CameraCalibrationError, match="measure"):
        validate_board_manifest(manifest, require_print_measurement=True)


def test_marker_must_be_smaller_than_square(tmp_path):
    manifest = generate_charuco_board(tmp_path / "asset")
    manifest["marker_length_mm"] = manifest["square_length_mm"]
    with pytest.raises(CameraCalibrationError):
        validate_board_manifest(manifest)


def test_out_of_scale_print_rejected(tmp_path):
    manifest = generate_charuco_board(tmp_path / "asset")
    manifest["print_measurement"] = {
        "confirmed": True, "measured_square_length_mm": 23.0,
        "measured_board_width_mm": 161.0, "measured_board_height_mm": 115.0,
        "measured_at": "2026-07-19T00:00:00Z", "tolerance_percent": 1.0,
    }
    with pytest.raises(CameraCalibrationError, match="tolerance"):
        validate_board_manifest(manifest, require_print_measurement=True)
