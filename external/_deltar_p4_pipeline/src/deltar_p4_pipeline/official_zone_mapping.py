from __future__ import annotations

from typing import Any

import numpy as np


def fr_centres(fr: np.ndarray) -> np.ndarray:
    rect = np.asarray(fr, dtype=np.float64)
    return np.column_stack(((rect[:, 1] + rect[:, 3]) / 2.0, (rect[:, 0] + rect[:, 2]) / 2.0))


def zones_for_bbox(bbox: list[float], fr: np.ndarray) -> list[int]:
    x1, y1, x2, y2 = bbox
    centres = fr_centres(fr)
    indices = np.flatnonzero((centres[:, 0] >= x1) & (centres[:, 0] <= x2) & (centres[:, 1] >= y1) & (centres[:, 1] <= y2))
    if indices.size == 0:
        target = np.asarray([(x1 + x2) / 2.0, (y1 + y2) / 2.0])
        indices = np.asarray([int(np.argmin(np.sum((centres - target) ** 2, axis=1)))])
    return indices.astype(int).tolist()


def hardcoded_flip_index(index: int) -> int:
    row, col = divmod(int(index), 8)
    return (7 - row) * 8 + (7 - col)


def mapping_audit(fr: np.ndarray) -> dict[str, Any]:
    rect = np.asarray(fr)
    centres = fr_centres(rect).reshape(8, 8, 2)
    row_major = bool(np.all(np.diff(centres[:, :, 0], axis=1) > 0) and np.all(np.diff(centres[:, :, 1], axis=0) > 0))
    row_y_constant = bool(np.all(np.ptp(centres[:, :, 1], axis=1) == 0))
    col_x_constant = bool(np.all(np.ptp(centres[:, :, 0], axis=0) == 0))
    return {
        "fr_shape": list(rect.shape),
        "rect_field_order": ["sy", "sx", "ey", "ex"],
        "official_zone_order": "row_major_top_to_bottom_left_to_right",
        "identity_mapping_verified": bool(row_major and row_y_constant and col_x_constant),
        "row_y_constant": row_y_constant,
        "column_x_constant": col_x_constant,
        "first_row_indices": list(range(8)),
        "first_row_centres_xy": centres[0].tolist(),
        "hardcoded_legacy_flip_both_map": [hardcoded_flip_index(i) for i in range(64)],
        "main_evaluation_mapping": "identity_official_fr_contract",
        "legacy_flip_is_main": False,
    }
