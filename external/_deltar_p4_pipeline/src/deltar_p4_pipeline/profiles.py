from __future__ import annotations

import numpy as np

from .zone_orientation import consumer_index_map


def zone_permutation(strategy: str, explicit_index_map_64: list[int] | None = None) -> np.ndarray:
    return consumer_index_map(strategy, explicit_index_map_64)


def reorder(values: np.ndarray, strategy: str, explicit_index_map_64: list[int] | None = None) -> np.ndarray:
    return np.asarray(values)[zone_permutation(strategy, explicit_index_map_64)]
