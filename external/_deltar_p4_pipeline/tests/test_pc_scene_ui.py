from __future__ import annotations

import json
from pathlib import Path

import pytest

from deltar_p4_pipeline.pc_scene_ui_renderer import (
    SCHEMA_VERSION,
    VIEWER_ASSET_MODE,
    render_pc_scene_ui,
    validate_pc_scene_ui_input,
)


def ui_document() -> dict:
    levels = [
        ("L1", "pass_success", True, True, True, "ok"),
        ("L1", "pass_failure_fallback", True, True, True, "unavailable"),
        ("L2", "pass_success", True, False, False, "ok"),
        ("L3", "blocked_success", False, False, False, "degraded"),
        ("L4", "blocked_failure", False, True, False, "unavailable"),
    ]
    frames = []
    for sequence in range(11):
        objects = []
        if sequence == 0:
            for index, (level, outcome, gate1, gate2, selected, status) in enumerate(levels):
                numeric = status in {"ok", "degraded"}
                objects.append(
                    {
                        "logical_object_id": f"frame_000000:logical:{index:03d}",
                        "class_name": "bottle",
                        "confidence": 0.2,
                        "bbox_xyxy": [10.0 + index * 50, 20.0, 40.0 + index * 50, 80.0],
                        "display_level": level,
                        "gate1_outcome": outcome,
                        "gate1_passed": gate1,
                        "gate2_passed": gate2,
                        "selected_for_p4": selected,
                        "numeric_available": numeric,
                        "geometry_status": status,
                        "distance_mm": 1000.0 if numeric else None,
                        "object_height_mm": 100.0 if numeric else None,
                        "support_height_mm": 700.0 if numeric else None,
                        "top_height_above_floor_mm": 800.0 if numeric else None,
                        "geometry_confidence": (0.2 if status == "degraded" else 0.8) if numeric else None,
                        "degradation_reason": "fixture_degraded" if status == "degraded" else None,
                        "failure_reason": None if numeric else "fixture_failure",
                        "support_method": "fixture_support" if numeric else None,
                        "shape_method": "fixture_extent" if numeric else None,
                        "diagnostics": {},
                        "overlay_paths": {},
                    }
                )
        frames.append(
            {
                "frame_id": f"frame_{sequence:06d}",
                "sequence": sequence,
                "source_image_path": None,
                "processing_status": {
                    "capture_status": "CAPTURE_OK",
                    "tof_status": "TOF_NORMAL" if sequence != 5 else "TOF_UNAVAILABLE",
                    "deltar_status": "DELTAR_OK" if sequence != 5 else "DELTAR_UNAVAILABLE",
                    "detection_status": "DETECTION_OK",
                    "geometry_status": "GEOMETRY_OK" if sequence != 5 else "GEOMETRY_UNAVAILABLE",
                    "degradation_mode": "NORMAL" if sequence != 5 else "RGB_ONLY_GEOMETRY_UNAVAILABLE",
                    "reason_codes": [] if sequence != 5 else ["no_safe_tof"],
                    "tof_diagnostics": {},
                },
                "objects": objects,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "scan_id": "scan_ui_fixture",
        "frame_count": 11,
        "generated_at": "2026-07-31T00:00:00Z",
        "selected_object_identities": [],
        "scan_summary": {},
        "frames": frames,
    }


def write_input(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


def test_four_levels_render_from_display_level_only(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    output = tmp_path / "scene.html"
    write_input(source, ui_document())
    render_pc_scene_ui(source, output)
    rendered = output.read_text(encoding="utf-8")
    for level in ("l1", "l2", "l3", "l4"):
        assert f"level-{level}" in rendered
    assert "The renderer's only visual-policy input is display_level" in (
        Path(__file__).resolve().parents[1] / "src" / "deltar_p4_pipeline" / "pc_scene_ui_renderer.py"
    ).read_text(encoding="utf-8")


def test_vertical_11_frames_sticky_nav_and_one_fixed_detail_panel(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    output = tmp_path / "scene.html"
    write_input(source, ui_document())
    render_pc_scene_ui(source, output)
    rendered = output.read_text(encoding="utf-8")
    assert rendered.count('class="frame-section"') == 11
    assert rendered.count('href="#frame-') == 11
    assert "position:sticky" in rendered
    assert rendered.count('class="detail-panel"') == 1
    assert ".detail-panel{position:fixed" in rendered
    assert "TOF_UNAVAILABLE" in rendered
    assert "RGB_ONLY_GEOMETRY_UNAVAILABLE" in rendered


def test_detail_panel_shows_failure_fallback_and_selected_state(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    output = tmp_path / "scene.html"
    write_input(source, ui_document())
    render_pc_scene_ui(source, output)
    rendered = output.read_text(encoding="utf-8")
    assert "pass_failure_fallback" in rendered
    assert "selected_for_p4" in rendered
    assert "Gate 1 outcome" in rendered
    fallback = ui_document()["frames"][0]["objects"][1]
    assert fallback["display_level"] == "L1"
    assert fallback["geometry_status"] == "unavailable"
    assert fallback["selected_for_p4"] is True


def test_renderer_output_is_byte_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "input.json"
    output = tmp_path / "scene.html"
    write_input(source, ui_document())
    render_pc_scene_ui(source, output)
    first = output.read_bytes()
    render_pc_scene_ui(source, output)
    assert output.read_bytes() == first


def test_renderer_uses_only_portable_relative_canonical_sources(tmp_path: Path) -> None:
    root = tmp_path / "result"
    source = root / "pc_scene_ui_input.v1.json"
    output = root / "visualization" / "index.html"
    document = ui_document()
    for frame in document["frames"]:
        relative = Path("logs") / frame["frame_id"] / "source_rgb.png"
        image = root / relative
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        frame["source_image_path"] = relative.as_posix()
    write_input(source, document)

    render_pc_scene_ui(source, output, artifact_root=root)

    rendered = output.read_text(encoding="utf-8")
    expected = [f"../logs/frame_{index:06d}/source_rgb.png" for index in range(11)]
    assert [value for value in expected if value in rendered] == expected
    assert rendered.count("../logs/frame_") == 11
    assert f'content="{VIEWER_ASSET_MODE}"' in rendered
    assert f'"viewer_asset_mode":"{VIEWER_ASSET_MODE}"' in rendered
    assert "source_image_path" not in rendered
    assert "source_image_uri" not in rendered
    assert "file:///" not in rendered
    assert ".frozen_attempts" not in rendered
    for relative in expected:
        assert (output.parent / relative).resolve().is_file()


def test_renderer_rejects_source_outside_artifact_root(tmp_path: Path) -> None:
    root = tmp_path / "result"
    source = root / "pc_scene_ui_input.v1.json"
    output = root / "visualization" / "index.html"
    document = ui_document()
    external = tmp_path / "outside" / "logs" / "frame_000000" / "source_rgb.png"
    external.parent.mkdir(parents=True)
    external.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    document["frames"][0]["source_image_path"] = str(external)
    source.parent.mkdir(parents=True)
    write_input(source, document)

    with pytest.raises(ValueError, match="escapes the visualization artifact root"):
        render_pc_scene_ui(source, output, artifact_root=root)


def test_ui_contract_rejects_invalid_level_and_frame_mapping() -> None:
    document = ui_document()
    assert validate_pc_scene_ui_input(document) is document
    document["frames"][0]["objects"][0]["display_level"] = "selected"
    with pytest.raises(ValueError, match="display_level"):
        validate_pc_scene_ui_input(document)
    document = ui_document()
    document["frames"][1]["frame_id"] = "frame_000010"
    with pytest.raises(ValueError, match="mismatch"):
        validate_pc_scene_ui_input(document)
