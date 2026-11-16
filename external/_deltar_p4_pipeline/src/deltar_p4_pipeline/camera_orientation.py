from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from .camera_calibration_contracts import (
    EXPECTED_HEIGHT,
    EXPECTED_WIDTH,
    ORIENTATION_SCHEMA,
    CameraCalibrationError,
    fail,
    sha256_file,
    validate_orientation_manifest,
    write_json,
)


def inspect_orientation_image(image_path: Path) -> dict[str, Any]:
    if not image_path.is_file():
        fail("E_ORIENTATION_IMAGE", "image does not exist", str(image_path))
    try:
        with Image.open(image_path) as image:
            width, height = image.size
            exif_orientation = int(image.getexif().get(274, 1))
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        fail("E_ORIENTATION_IMAGE", f"cannot decode image: {exc}", str(image_path))
    result = {
        "width": width,
        "height": height,
        "orientation": "landscape" if width > height else "portrait" if height > width else "square",
        "expected_software_rotation_degrees": 0,
        "exif_orientation": exif_orientation,
        "resolution_valid": (width, height) == (EXPECTED_WIDTH, EXPECTED_HEIGHT),
        "exif_orientation_valid": exif_orientation == 1,
        "manual_scene_upright_confirmation_required": True,
        "manual_checklist": [
            "Scene verticals are visually upright.",
            "Text and asymmetric landmarks are not rotated or mirrored.",
            "This is the final P4 640x360 output, not a source-resolution image.",
            "No PC-side rotation, crop, or resampling was applied.",
        ],
    }
    if not result["resolution_valid"]:
        fail("E_ORIENTATION_RESOLUTION", "final image must be exactly 640x360", str(image_path), result)
    if not result["exif_orientation_valid"]:
        fail("E_ORIENTATION_EXIF", "EXIF orientation must be 1; bake the physical orientation into final pixels", str(image_path), result)
    return result


def create_orientation_manifest(
    image_path: Path,
    output_path: Path,
    *,
    confirm_upright: bool,
    notes: str = "",
) -> dict[str, Any]:
    inspection = inspect_orientation_image(image_path)
    manifest = {
        "schema_version": ORIENTATION_SCHEMA,
        "width": inspection["width"],
        "height": inspection["height"],
        "physical_orientation": "landscape",
        "software_rotation_degrees": 0,
        "scene_upright_confirmed": bool(confirm_upright),
        "confirmed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z") if confirm_upright else None,
        "source_image_sha256": sha256_file(image_path),
        "notes": notes,
        "exif_orientation": inspection["exif_orientation"],
    }
    validate_orientation_manifest(manifest, require_confirmed=False)
    write_json(output_path, manifest)
    return manifest

