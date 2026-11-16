import json

import pytest
from PIL import Image

from deltar_p4_pipeline.camera_calibration_contracts import CameraCalibrationError, validate_orientation_manifest
from deltar_p4_pipeline.camera_orientation import create_orientation_manifest, inspect_orientation_image


def test_landscape_orientation_manifest_requires_explicit_confirmation(tmp_path):
    image = tmp_path / "final.png"
    Image.new("RGB", (640, 360), "white").save(image)
    result = inspect_orientation_image(image)
    assert result["manual_scene_upright_confirmation_required"] is True
    manifest = create_orientation_manifest(image, tmp_path / "orientation.json", confirm_upright=True, notes="manual check")
    assert validate_orientation_manifest(manifest, require_confirmed=True)["software_rotation_degrees"] == 0


def test_portrait_resolution_rejected(tmp_path):
    image = tmp_path / "portrait.png"
    Image.new("RGB", (360, 640), "white").save(image)
    with pytest.raises(CameraCalibrationError, match="640x360"):
        inspect_orientation_image(image)


def test_unconfirmed_upright_rejected_for_calibration(tmp_path):
    image = tmp_path / "final.png"
    Image.new("RGB", (640, 360), "white").save(image)
    manifest = create_orientation_manifest(image, tmp_path / "orientation.json", confirm_upright=False)
    with pytest.raises(CameraCalibrationError, match="upright"):
        validate_orientation_manifest(manifest, require_confirmed=True)


def test_software_rotation_rejected(tmp_path):
    image = tmp_path / "final.png"
    Image.new("RGB", (640, 360), "white").save(image)
    manifest = create_orientation_manifest(image, tmp_path / "orientation.json", confirm_upright=True)
    manifest["software_rotation_degrees"] = 90
    with pytest.raises(CameraCalibrationError, match="zero"):
        validate_orientation_manifest(manifest, require_confirmed=True)


def test_orientation_unknown_field_rejected(tmp_path):
    image = tmp_path / "final.png"
    Image.new("RGB", (640, 360), "white").save(image)
    manifest = create_orientation_manifest(image, tmp_path / "orientation.json", confirm_upright=True)
    manifest["unknown"] = 1
    with pytest.raises(CameraCalibrationError, match="additional"):
        validate_orientation_manifest(manifest)
