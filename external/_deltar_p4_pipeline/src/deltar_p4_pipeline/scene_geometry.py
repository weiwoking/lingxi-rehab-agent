from __future__ import annotations

from typing import Any

import numpy as np

from .errors import ErrorCode, PipelineError


def normalize(vectors: np.ndarray) -> np.ndarray:
    value = np.asarray(vectors, dtype=np.float64)
    lengths = np.linalg.norm(value, axis=-1, keepdims=True)
    if np.any(lengths <= 0):
        raise PipelineError(ErrorCode.STAGE5_RENDER, "ray direction cannot be zero")
    return value / lengths


def ray_plane(origin: np.ndarray, directions: np.ndarray, point: np.ndarray, normal: np.ndarray) -> np.ndarray:
    directions = np.asarray(directions, dtype=np.float64)
    normal = normalize(np.asarray(normal, dtype=np.float64).reshape(1, 3))[0]
    denominator = directions @ normal
    numerator = float((np.asarray(point, dtype=np.float64) - np.asarray(origin, dtype=np.float64)) @ normal)
    with np.errstate(divide="ignore", invalid="ignore"):
        distance = numerator / denominator
    return np.where((np.abs(denominator) > 1e-10) & (distance > 1e-8), distance, np.inf)


def ray_box(origin: np.ndarray, directions: np.ndarray, minimum: np.ndarray, maximum: np.ndarray) -> np.ndarray:
    origin = np.asarray(origin, dtype=np.float64)
    directions = np.asarray(directions, dtype=np.float64)
    minimum = np.asarray(minimum, dtype=np.float64)
    maximum = np.asarray(maximum, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        t1 = (minimum - origin) / directions
        t2 = (maximum - origin) / directions
    near = np.max(np.minimum(t1, t2), axis=-1)
    far = np.min(np.maximum(t1, t2), axis=-1)
    return np.where((far >= np.maximum(near, 0.0)) & (far > 1e-8), np.where(near > 1e-8, near, far), np.inf)


def camera_rays(camera: dict[str, Any]) -> tuple[np.ndarray, np.ndarray]:
    width, height = int(camera["width"]), int(camera["height"])
    u, v = np.meshgrid(np.arange(width, dtype=np.float64) + 0.5, np.arange(height, dtype=np.float64) + 0.5)
    rays_camera = normalize(np.stack(((u - float(camera["cx"])) / float(camera["fx"]), (v - float(camera["cy"])) / float(camera["fy"]), np.ones_like(u)), axis=-1))
    transform = np.asarray(camera["T_ground_from_camera"], dtype=np.float64)
    return transform[:3, 3], rays_camera @ transform[:3, :3].T


def intersect_scene(origin: np.ndarray, directions: np.ndarray, scene: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    shape = directions.shape[:-1]
    flat = normalize(np.asarray(directions, dtype=np.float64).reshape(-1, 3))
    best = np.full(flat.shape[0], np.inf, dtype=np.float64)
    labels = np.full(flat.shape[0], -1, dtype=np.int32)
    uv_local = np.zeros((flat.shape[0], 2), dtype=np.float64)

    floor_t = ray_plane(origin, flat, np.array([0.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]))
    with np.errstate(invalid="ignore"):
        floor_hit = origin + floor_t[:, None] * flat
    floor_t[(np.abs(floor_hit[:, 0]) > 8.0) | (floor_hit[:, 1] < 0.0) | (floor_hit[:, 1] > 8.0)] = np.inf
    update = floor_t < best
    best[update], labels[update] = floor_t[update], 0

    wall_y = float(scene.get("background_wall_y_m", 6.0))
    wall_t = ray_plane(origin, flat, np.array([0.0, wall_y, 0.0]), np.array([0.0, -1.0, 0.0]))
    wall_hit = origin + wall_t[:, None] * flat
    wall_t[(np.abs(wall_hit[:, 0]) > 8.0) | (wall_hit[:, 2] < 0.0) | (wall_hit[:, 2] > 5.0)] = np.inf
    update = wall_t < best
    best[update], labels[update] = wall_t[update], 1

    for index, item in enumerate(scene["objects"], start=2):
        x0, y0, z0 = (float(x) for x in item["center_bottom_ground_m"])
        width, height = float(item["width_m"]), float(item["height_m"])
        if item["primitive"] == "box":
            depth = float(item.get("depth_m", 0.25))
            t = ray_box(origin, flat, np.array([x0-width/2, y0-depth/2, z0]), np.array([x0+width/2, y0+depth/2, z0+height]))
            with np.errstate(invalid="ignore"):
                hit = origin + t[:, None] * flat
            local = np.column_stack(((hit[:, 0]-(x0-width/2))/width, (z0+height-hit[:, 2])/height))
        else:
            slope = float(item.get("slant_y_per_x", 0.0))
            normal = np.array([-slope, 1.0, 0.0])
            t = ray_plane(origin, flat, np.array([x0, y0, z0]), normal)
            hit = origin + t[:, None] * flat
            inside = (np.abs(hit[:, 0]-x0) <= width/2) & (hit[:, 2] >= z0) & (hit[:, 2] <= z0+height)
            t[~inside] = np.inf
            local = np.column_stack(((hit[:, 0]-(x0-width/2))/width, (z0+height-hit[:, 2])/height))
        update = t < best
        best[update], labels[update], uv_local[update] = t[update], index, local[update]
    points = np.asarray(origin, dtype=np.float64) + best[:, None] * flat
    points[~np.isfinite(best)] = np.nan
    return best.reshape(shape), labels.reshape(shape), uv_local.reshape((*shape, 2))
