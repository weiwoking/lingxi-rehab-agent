from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from .canvas import CanvasResult, map_depth_to_source
from .errors import ErrorCode, PipelineError
from .validation import file_sha256, quantize_depth_mm, validate_depth_png

KNOWN_OUTPUT_NAMES = (
    "decoded_rgb.png",
    "canvas_rgb.png",
    "valid_region.png",
    "zone_fr_overlay.png",
    "depth_m.npy",
    "depth_mm.png",
    "depth_visual.png",
    "depth_source_640x360.npy",
    "depth_source_visual.png",
    "result.json",
    "validation_report.json",
)
DEPTH_OUTPUT_NAMES = (
    "depth_m.npy",
    "depth_mm.png",
    "depth_visual.png",
    "depth_source_640x360.npy",
    "depth_source_visual.png",
)


def prepare_output_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        for name in KNOWN_OUTPUT_NAMES:
            target = path / name
            if target.exists():
                target.unlink()
    except OSError as exc:
        raise PipelineError(ErrorCode.OUTPUT_WRITE, f"failed to prepare output directory {path}: {exc}", str(path)) from exc


def _cleanup(paths: list[Path]) -> list[str]:
    failed: list[str] = []
    for path in paths:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            failed.append(str(path))
    return failed


def write_json(path: Path, data: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise PipelineError(ErrorCode.OUTPUT_WRITE, f"failed to write JSON: {exc}", str(path)) from exc


def _depth_visual(depth: np.ndarray, valid: np.ndarray | None = None) -> tuple[np.ndarray, dict[str, float | None]]:
    mask = np.isfinite(depth) & (depth > 0)
    if valid is not None:
        mask &= valid
    output = np.zeros((*depth.shape, 3), dtype=np.uint8)
    if not mask.any():
        return output, {"vmin_m": None, "vmax_m": None}
    vmin, vmax = np.percentile(depth[mask], [2, 98])
    if vmax <= vmin:
        vmax = vmin + 1e-6
    normalized = np.clip((depth - vmin) / (vmax - vmin), 0, 1)
    output[..., 0] = (255 * normalized).astype(np.uint8)
    output[..., 1] = (255 * (1 - np.abs(2 * normalized - 1))).astype(np.uint8)
    output[..., 2] = (255 * (1 - normalized)).astype(np.uint8)
    output[~mask] = 0
    return output, {"vmin_m": float(vmin), "vmax_m": float(vmax)}


def write_debug_images(output_dir: Path, decoded: np.ndarray, canvas: CanvasResult, fr: np.ndarray) -> None:
    targets = [output_dir / name for name in KNOWN_OUTPUT_NAMES[:4]]
    created: list[Path] = []
    try:
        Image.fromarray(decoded, mode="RGB").save(targets[0]); created.append(targets[0])
        Image.fromarray(canvas.rgb, mode="RGB").save(targets[1]); created.append(targets[1])
        Image.fromarray((canvas.valid_mask.astype(np.uint8) * 255), mode="L").save(targets[2]); created.append(targets[2])
        overlay = Image.fromarray(canvas.rgb, mode="RGB")
        draw = ImageDraw.Draw(overlay)
        for index, (sy, sx, ey, ex) in enumerate(fr):
            color = (255, 255, 0) if index in (0, 7, 56, 63) else (0, 255, 255)
            draw.rectangle((float(sx), float(sy), float(ex), float(ey)), outline=color, width=1)
            if index in (0, 7, 56, 63):
                draw.text((float(sx) + 2, float(sy) + 2), str(index), fill=color)
        overlay.save(targets[3]); created.append(targets[3])
    except (OSError, ValueError) as exc:
        cleanup_failed = _cleanup(created)
        raise PipelineError(
            ErrorCode.OUTPUT_WRITE,
            f"failed to write debug output under {output_dir}: {exc}",
            str(output_dir),
            {"partial_outputs_removed": [str(path) for path in created], "cleanup_failed": cleanup_failed},
        ) from exc


def write_depth_outputs(output_dir: Path, depth: np.ndarray, canvas: CanvasResult) -> dict[str, Any]:
    targets = [output_dir / name for name in DEPTH_OUTPUT_NAMES]
    created: list[Path] = []
    try:
        depth = depth.astype(np.float32, copy=False)
        np.save(targets[0], depth, allow_pickle=False); created.append(targets[0])
        mm = quantize_depth_mm(depth)
        Image.fromarray(mm, mode="I;16").save(targets[1]); created.append(targets[1])
        visual, visual_meta = _depth_visual(depth)
        Image.fromarray(visual, mode="RGB").save(targets[2]); created.append(targets[2])
        source, source_valid = map_depth_to_source(depth, canvas)
        np.save(targets[3], source, allow_pickle=False); created.append(targets[3])
        source_visual, source_visual_meta = _depth_visual(source, source_valid)
        Image.fromarray(source_visual, mode="RGB").save(targets[4]); created.append(targets[4])
    except (OSError, ValueError) as exc:
        cleanup_failed = _cleanup(created)
        raise PipelineError(
            ErrorCode.OUTPUT_WRITE,
            f"failed to write depth output under {output_dir}: {exc}",
            str(output_dir),
            {"partial_outputs_removed": [str(path) for path in created], "cleanup_failed": cleanup_failed},
        ) from exc
    return {
        "depth_mm_png": validate_depth_png(depth, output_dir / "depth_mm.png"),
        "depth_mm_png_sha256": file_sha256(output_dir / "depth_mm.png"),
        "depth_visualization": visual_meta,
        "source_visualization": source_visual_meta,
        "source_valid_ratio": float(source_valid.mean()),
        "source_mapping_rule": "inverse recorded canvas transform with bilinear depth and nearest valid mask",
    }
