from __future__ import annotations

from typing import Any

import numpy as np

from .errors import ErrorCode, PipelineError

SCHEMA_VERSION = "deltar_stage5_zone_orientation.v1"
D4_STRATEGIES = (
    "identity",
    "rotate_90_cw",
    "rotate_180",
    "rotate_270_cw",
    "flip_horizontal",
    "flip_vertical",
    "transpose_main_diagonal",
    "transpose_anti_diagonal",
)
# rotate_90/rotate_270 and flip_both are retained as compatibility aliases.
# They are deliberately not members of D4_STRATEGIES and therefore cannot
# become duplicate calibration candidates.
STRATEGIES = set(D4_STRATEGIES) | {
    "rotate_90", "rotate_270", "flip_both", "explicit_index_map_64",
}


def consumer_index_map(strategy: str, explicit_index_map_64: list[int] | None = None) -> np.ndarray:
    grid = np.arange(64, dtype=np.int64).reshape(8, 8)
    if strategy == "identity": mapped = grid
    elif strategy == "flip_horizontal": mapped = np.fliplr(grid)
    elif strategy == "flip_vertical": mapped = np.flipud(grid)
    elif strategy in {"flip_both", "rotate_180"}: mapped = np.rot90(grid, 2)
    elif strategy in {"rotate_90", "rotate_90_cw"}: mapped = np.rot90(grid, -1)
    elif strategy in {"rotate_270", "rotate_270_cw"}: mapped = np.rot90(grid, 1)
    elif strategy == "transpose_main_diagonal": mapped = grid.T
    elif strategy == "transpose_anti_diagonal": mapped = np.rot90(grid.T, 2)
    elif strategy == "explicit_index_map_64":
        if explicit_index_map_64 is None:
            raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE, "explicit orientation requires explicit_index_map_64")
        mapped = np.asarray(explicit_index_map_64, dtype=np.int64)
        if mapped.shape != (64,):
            raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE, "explicit_index_map_64 must contain 64 values")
    else:
        raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE, "unsupported zone orientation", details=strategy)
    result = mapped.reshape(-1)
    if sorted(result.tolist()) != list(range(64)):
        raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE, "zone orientation must be a permutation of 0..63")
    return result


def inverse_index_map(index_map: np.ndarray | list[int]) -> np.ndarray:
    mapping = np.asarray(index_map, dtype=np.int64)
    inverse = np.empty(64, dtype=np.int64)
    inverse[mapping] = np.arange(64, dtype=np.int64)
    return inverse


def raw_index_for_canonical_index(strategy: str) -> np.ndarray:
    """Return the frozen Stage 7B raw->canonical consumption contract.

    Element ``c`` is the raw array index consumed by canonical zone ``c``.
    """
    return consumer_index_map(strategy)


def canonical_index_for_raw_index(strategy: str) -> np.ndarray:
    """Return the inverse mapping: element ``r`` is canonical index for raw ``r``."""
    return inverse_index_map(raw_index_for_canonical_index(strategy))


def canonical_values_from_raw(values: Any, strategy: str) -> np.ndarray:
    array = np.asarray(values)
    if array.shape[0] != 64:
        raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE, "raw zone array must have leading length 64")
    return array[raw_index_for_canonical_index(strategy)]


def raw_values_from_canonical(values: Any, strategy: str) -> np.ndarray:
    array = np.asarray(values)
    if array.shape[0] != 64:
        raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE, "canonical zone array must have leading length 64")
    return array[canonical_index_for_raw_index(strategy)]


def apply_consumer_map(values: Any, orientation: dict[str, Any]) -> np.ndarray:
    mapping = consumer_index_map(orientation["strategy"], orientation.get("explicit_index_map_64"))
    return np.asarray(values)[mapping]


def validate_orientation(profile: dict[str, Any]) -> dict[str, Any]:
    if profile.get("schema_version") != SCHEMA_VERSION or profile.get("canonical_simulator_order") != "row_major_top_left_to_bottom_right":
        raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE, "invalid Stage 5.1 zone orientation contract")
    strategy = profile.get("strategy")
    mapping = consumer_index_map(strategy, profile.get("explicit_index_map_64"))
    declared = profile.get("consumer_index_map")
    if declared is not None and list(declared) != mapping.tolist():
        raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE, "declared consumer_index_map does not match strategy")
    return {**profile, "consumer_index_map": mapping.tolist()}


def orientation_profile(strategy: str = "identity", explicit_index_map_64: list[int] | None = None) -> dict[str, Any]:
    mapping = consumer_index_map(strategy, explicit_index_map_64)
    return {"schema_version": SCHEMA_VERSION, "canonical_simulator_order": "row_major_top_left_to_bottom_right",
            "strategy": strategy, "consumer_index_map": mapping.tolist(), "explicit_index_map_64": explicit_index_map_64}
