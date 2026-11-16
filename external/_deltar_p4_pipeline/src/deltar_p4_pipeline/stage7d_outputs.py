from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
import shutil
import time
from typing import Any

import numpy as np
from PIL import Image

from .horizontal_planes import extract_horizontal_planes, gravity_points_from_depth
from .scan_contracts import atomic_write_json
from .stage7d_contracts import (
    HORIZONTAL_PLANES_SCHEMA,
    SUMMARY_SCHEMA,
    Stage7DContext,
    validate_horizontal_planes_document,
    validate_stage7d_summary,
)


WORLD_FRAME = {
    "x_axis": "camera_x_metric_parameterization_only",
    "y_axis": "camera_y_metric_parameterization_only",
    "z_axis": "up",
    "origin": "world_z_ground_below_camera; x/y are not world-authoritative",
    "full_world_rotation_authority": False,
}


def _public_candidate(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if not key.startswith("_")}


def _save_world_height_visual(
    path: Path, world_points: np.ndarray, valid: np.ndarray, hypothesis: str
) -> None:
    import cv2

    height = world_points[..., 2]
    values = height[valid]
    image = np.full((*valid.shape, 3), 32, dtype=np.uint8)
    if values.size:
        lower = float(np.percentile(values, 2))
        upper = float(np.percentile(values, 98))
        if upper <= lower:
            upper = lower + 1.0
        scaled = np.clip((height - lower) / (upper - lower), 0.0, 1.0)
        scaled = np.where(valid, scaled, 0.0)
        coloured = cv2.applyColorMap(
            np.asarray(np.rint(scaled * 255.0), dtype=np.uint8), cv2.COLORMAP_TURBO
        )
        coloured = cv2.cvtColor(coloured, cv2.COLOR_BGR2RGB)
        image[valid] = coloured[valid]
        cv2.putText(
            image,
            f"{hypothesis} Z_world {lower:.0f}..{upper:.0f} mm",
            (10, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    else:
        cv2.putText(
            image,
            f"{hypothesis}: no valid depth",
            (10, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    Image.fromarray(image, mode="RGB").save(path)


def _save_overlay(
    path: Path,
    rgb: np.ndarray,
    hypothesis: str,
    planes: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> None:
    import cv2

    canvas = np.asarray(rgb, dtype=np.uint8).copy()
    colour_by_label = {
        "ground": np.asarray([30, 190, 70], dtype=np.float32),
        "support_surface": np.asarray([255, 145, 25], dtype=np.float32),
    }
    for plane in planes:
        mask = plane["_mask"]
        colour = colour_by_label[plane["label"]]
        canvas[mask] = np.asarray(
            0.55 * canvas[mask].astype(np.float32) + 0.45 * colour,
            dtype=np.uint8,
        )
        contour = np.asarray(plane["contour_xy"], dtype=np.int32)
        if contour.size:
            cv2.polylines(canvas, [contour], True, tuple(int(v) for v in colour), 2)
        x1, y1, _, _ = plane["bbox_xyxy"]
        diagnostics = plane["confidence_diagnostics"]
        text = (
            f"{plane['label']} h={plane['median_height_mm']:.0f}mm "
            f"tilt={diagnostics['normal_tilt_deg']:.1f} "
            f"rms={plane['residual_rms_mm']:.1f} n={plane['pixel_count']}"
        )
        cv2.putText(
            canvas,
            text,
            (max(4, x1), max(18, y1 - 5)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )
    for item in rejected:
        if item["reason"] != "non_horizontal":
            continue
        contour = np.asarray(item["contour_xy"], dtype=np.int32)
        if contour.size:
            cv2.polylines(canvas, [contour], True, (220, 60, 220), 1)
        x1, y1, _, _ = item["bbox_xyxy"]
        cv2.putText(
            canvas,
            "non_horizontal",
            (max(4, x1), max(18, y1 - 3)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (255, 210, 255),
            1,
            cv2.LINE_AA,
        )
    cv2.putText(
        canvas,
        f"Stage 7D hypothesis: {hypothesis}",
        (10, 352),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        (255, 255, 255),
        1,
        cv2.LINE_AA,
    )
    Image.fromarray(canvas, mode="RGB").save(path)


def _save_stage7d_visuals(
    world_path: Path,
    world_points: np.ndarray,
    valid: np.ndarray,
    overlay_path: Path,
    rgb: np.ndarray,
    hypothesis: str,
    planes: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    workers: int = 1,
) -> None:
    if workers < 1 or workers > 2:
        raise ValueError("Stage 7D visual PNG workers must be between 1 and 2")
    if workers == 1:
        _save_world_height_visual(world_path, world_points, valid, hypothesis)
        _save_overlay(overlay_path, rgb, hypothesis, planes, rejected)
        return
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="phase6-stage7d-png"
    ) as executor:
        futures = [
            executor.submit(
                _save_world_height_visual,
                world_path,
                world_points,
                valid,
                hypothesis,
            ),
            executor.submit(
                _save_overlay,
                overlay_path,
                rgb,
                hypothesis,
                planes,
                rejected,
            ),
        ]
        try:
            for future in futures:
                future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            raise


@dataclass
class Stage7DProcessor:
    context: Stage7DContext
    output_root: Path
    scan_id: str
    diagnostics_enabled: bool = False
    ransac_batch_bound: int = 1
    visual_png_workers: int = 1
    frame_records: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.ransac_batch_bound < 1 or self.ransac_batch_bound > 32:
            raise ValueError("ransac_batch_bound must be between 1 and 32")
        if self.visual_png_workers < 1 or self.visual_png_workers > 2:
            raise ValueError("Stage 7D visual PNG workers must be between 1 and 2")
        (self.output_root / "stage7d").mkdir(parents=True, exist_ok=True)

    def process_frame(
        self,
        frame_id: str,
        source_rgb: np.ndarray,
        direct_depth_m: np.ndarray,
        *,
        source_rgb_path: Path | None = None,
    ) -> dict[str, Any]:
        rgb = np.asarray(source_rgb, dtype=np.uint8)
        depth = np.asarray(direct_depth_m, dtype=np.float32)
        if rgb.shape != (360, 640, 3) or depth.shape != (360, 640):
            raise ValueError("Stage 7D requires source-aligned RGB/depth at 640x360")
        frame_root = self.output_root / "stage7d" / frame_id
        frame_root.mkdir(parents=True, exist_ok=False)
        total_started = time.perf_counter()
        debug_write_ms = 0.0
        write_started = time.perf_counter()
        destination_rgb_path = frame_root / "source_rgb.png"
        if source_rgb_path is None:
            Image.fromarray(rgb, mode="RGB").save(destination_rgb_path)
        else:
            shutil.copyfile(source_rgb_path, destination_rgb_path)
        debug_write_ms += (time.perf_counter() - write_started) * 1000.0
        hypothesis_documents: dict[str, dict[str, Any]] = {}
        compact: dict[str, dict[str, Any]] = {}
        formal_geometry_selected: dict[str, Any] | None = None
        axial_ms = 0.0
        hypotheses = ("axial_z",)
        for hypothesis in hypotheses:
            write_started = time.perf_counter()
            hypothesis_root = frame_root / hypothesis
            hypothesis_root.mkdir()
            np.save(
                hypothesis_root / "depth_source_640x360.npy",
                depth,
                allow_pickle=False,
            )
            debug_write_ms += (time.perf_counter() - write_started) * 1000.0
            compute_started = time.perf_counter()
            world, camera_axial_z_mm, valid = gravity_points_from_depth(
                depth,
                self.context.camera_model,
                self.context.gravity_up_camera,
                self.context.camera_height_mm,
                min_depth_m=float(self.context.profile["min_depth_m"]),
                max_depth_m=float(self.context.profile["max_depth_m"]),
            )
            analysis = extract_horizontal_planes(
                world,
                valid,
                self.context.profile,
                ransac_batch_bound=self.ransac_batch_bound,
            )
            compute_ms = (time.perf_counter() - compute_started) * 1000.0
            axial_ms += compute_ms
            planes = analysis["planes"]
            rejected = analysis["rejected_candidates"]
            if hypothesis == "axial_z":
                formal_geometry_selected = {
                    "selected_depth_semantics": "axial_z",
                    "camera_axial_z_mm": camera_axial_z_mm,
                    "world_points_mm": world,
                    "world_valid_mask": valid,
                    "support_planes": [
                        item for item in planes if item["label"] == "support_surface"
                    ],
                }
            write_started = time.perf_counter()
            world_height_mm = np.asarray(world[..., 2], dtype=np.float32)
            world_height_mm[~valid] = np.nan
            np.save(
                hypothesis_root / "world_height_mm.npy",
                world_height_mm,
                allow_pickle=False,
            )
            document = {
                "schema_version": HORIZONTAL_PLANES_SCHEMA,
                "scan_id": self.scan_id,
                "frame_id": frame_id,
                "depth_hypothesis": hypothesis,
                "depth_unit": "m",
                "world_geometry_unit": "mm",
                "camera_profile": self.context.camera_summary,
                "camera_world_profile": self.context.camera_world_summary,
                "camera_tof_profile": self.context.camera_tof_summary,
                "world_frame": WORLD_FRAME,
                "world_height_statistics": analysis["world_height_statistics"],
                "planes": [_public_candidate(item) for item in planes],
                "rejected_candidates": [_public_candidate(item) for item in rejected],
                "warnings": analysis["warnings"],
            }
            validate_horizontal_planes_document(document)
            atomic_write_json(hypothesis_root / "horizontal_planes.v1.json", document)
            _save_stage7d_visuals(
                hypothesis_root / "world_height_visual.png",
                world,
                valid,
                hypothesis_root / "horizontal_plane_overlay.png",
                rgb,
                hypothesis,
                planes,
                rejected,
                self.visual_png_workers,
            )
            masks: dict[str, np.ndarray] = {
                "ground_mask": np.zeros(depth.shape, dtype=bool),
                "support_surface_mask": np.zeros(depth.shape, dtype=bool),
            }
            for item in planes:
                masks[f"{item['plane_id']}_{item['label']}"] = item["_mask"]
                masks[f"{item['label']}_mask"] |= item["_mask"]
            for index, item in enumerate(rejected):
                masks[f"rejected_{index:02d}_{item['reason']}"] = item["_mask"]
            np.savez_compressed(hypothesis_root / "plane_masks.npz", **masks)
            hypothesis_documents[hypothesis] = document
            compact[hypothesis] = {
                "ground_candidates": [
                    {
                        "plane_id": item["plane_id"],
                        "median_height_mm": item["median_height_mm"],
                        "pixel_count": item["pixel_count"],
                    }
                    for item in document["planes"]
                    if item["label"] == "ground"
                ],
                "support_candidates": [
                    {
                        "plane_id": item["plane_id"],
                        "median_height_mm": item["median_height_mm"],
                        "pixel_count": item["pixel_count"],
                    }
                    for item in document["planes"]
                    if item["label"] == "support_surface"
                ],
                "fit_summaries": [
                    {
                        "plane_id": item["plane_id"],
                        "label": item["label"],
                        "normal_world": item["normal_world"],
                        "residual_rms_mm": item["residual_rms_mm"],
                        "residual_p95_mm": item["residual_p95_mm"],
                    }
                    for item in document["planes"]
                ],
                "rejected_candidates": document["rejected_candidates"],
                "world_height_statistics": document["world_height_statistics"],
                "warnings": document["warnings"],
            }
            debug_write_ms += (time.perf_counter() - write_started) * 1000.0
        record = {
            "frame_id": frame_id,
            "source_rgb": f"{frame_id}/source_rgb.png",
            "hypotheses": {
                name: {
                    "planes": len(document["planes"]),
                    "ground_candidates": sum(
                        item["label"] == "ground" for item in document["planes"]
                    ),
                    "support_candidates": sum(
                        item["label"] == "support_surface"
                        for item in document["planes"]
                    ),
                    "rejected_non_horizontal": sum(
                        item["reason"] == "non_horizontal"
                        for item in document["rejected_candidates"]
                    ),
                    "output_dir": f"{frame_id}/{name}",
                }
                for name, document in hypothesis_documents.items()
            },
        }
        self.frame_records.append(record)
        if formal_geometry_selected is None:
            raise ValueError("Stage 7D did not produce the required axial_z in-memory result")
        return {
            **record,
            "_formal_geometry_selected": formal_geometry_selected,
            "_stage7d_timing": {
                "stage7d_axial_ms": axial_ms,
                "stage7d_radial_ms": 0.0,
                "stage7d_debug_write_ms": debug_write_ms,
                "stage7d_total_critical_ms": (
                    time.perf_counter() - total_started
                )
                * 1000.0,
                "stage7d_diagnostics_enabled": float(self.diagnostics_enabled),
            },
        }

    def write_summary(self) -> dict[str, Any]:
        summary = {
            "schema_version": SUMMARY_SCHEMA,
            "scan_id": self.scan_id,
            "status": "formal_stage2_stage3a_active",
            "selection_status": "formal_stage2_stage3a",
            "absolute_geometry_valid": True,
            "profiles": {
                "camera": self.context.camera_summary,
                "camera_world": self.context.camera_world_summary,
                "camera_tof": self.context.camera_tof_summary,
                "stage7d_thresholds": {
                    "schema_version": self.context.profile["schema_version"],
                    "selection_status": self.context.profile["selection_status"],
                },
            },
            "frames": self.frame_records,
            "warnings": [],
        }
        validate_stage7d_summary(summary)
        atomic_write_json(
            self.output_root / "stage7d" / "stage7d_summary.v1.json", summary
        )
        return summary
