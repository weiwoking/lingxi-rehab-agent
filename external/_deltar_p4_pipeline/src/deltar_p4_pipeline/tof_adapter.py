from __future__ import annotations

import random
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import torch

from .canvas import CanvasResult
from .deltar_bridge import import_official_module
from .errors import ErrorCode, PipelineError
from .formal_geometry_authority import FormalGeometryAuthority
from .stage2_projection import dynamic_rectangles, official_envelope_grid


@dataclass(frozen=True)
class DeltarInputs:
    rgb: torch.Tensor
    raw_hist: torch.Tensor
    hypotheses: torch.Tensor
    fr: torch.Tensor
    mask: torch.Tensor
    patch_info: dict[Any, Any]
    warnings: tuple[str, ...]
    validation: dict[str, Any]


@dataclass
class OfficialUtilsPerJobCache:
    """Memoize one immutable official utility module for one scan job."""

    _key: str | None = None
    _module: Any = None
    hits: int = 0
    misses: int = 0

    def get(self, worktree: Path):
        key = str(Path(worktree).resolve())
        if self._module is None or self._key != key:
            self._module = _official_utils(Path(worktree))
            self._key = key
            self.misses += 1
        else:
            self.hits += 1
        return self._module


def seed_everything(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _official_utils(worktree: Path):
    return import_official_module("src.utils.dataloader", worktree)


def adapt_inputs(
    canvas_rgb: np.ndarray,
    canvas_result: CanvasResult,
    tof: dict[str, Any],
    profile: dict[str, Any],
    worktree: Path,
    authority: FormalGeometryAuthority,
    *,
    device: str = "cpu",
    official_utils_cache: OfficialUtilsPerJobCache | None = None,
) -> DeltarInputs:
    seed_everything(profile["seed"])
    distance = np.asarray(tof["distance_mm"], dtype=np.float64)
    sigma = np.asarray(tof["range_sigma_mm"], dtype=np.float64)
    raw_hist_np = np.stack((distance / 1000.0, sigma / 1000.0), axis=1)
    fr_np, mask_np, projection_audit = dynamic_rectangles(
        authority, tof, canvas_result
    )
    warnings: tuple[str, ...] = ()
    valid_zone_count = int(mask_np.sum())
    if valid_zone_count == 0:
        raise PipelineError(
            ErrorCode.NO_VALID_ZONES,
            "the effective mask contains no valid ToF zones; DELTAR inference and geometry consumption are rejected",
            "profile.mask",
            {"valid_zone_count": 0},
        )
    invalid_sigma_indices = np.flatnonzero(mask_np & (sigma <= 0.0)).astype(int).tolist()
    if invalid_sigma_indices:
        raise PipelineError(
            ErrorCode.EFFECTIVE_SIGMA_NONPOSITIVE,
            f"effective valid zones require sigma_mm > 0; invalid model-zone indices: {invalid_sigma_indices}",
            "range_sigma_mm",
            {"effective_zone_indices": invalid_sigma_indices},
        )

    rgb_float = torch.from_numpy(np.ascontiguousarray(canvas_rgb.transpose(2, 0, 1))).float() / 255.0
    mean = torch.tensor([0.485, 0.456, 0.406], dtype=torch.float32).view(3, 1, 1)
    std = torch.tensor([0.229, 0.224, 0.225], dtype=torch.float32).view(3, 1, 1)
    rgb_tensor = ((rgb_float - mean) / std).unsqueeze(0)
    raw_hist = torch.from_numpy(raw_hist_np).to(torch.float64)
    mask = torch.from_numpy(mask_np)
    fr = torch.from_numpy(fr_np).to(torch.float32)

    official = (
        _official_utils(worktree)
        if official_utils_cache is None
        else official_utils_cache.get(worktree)
    )
    sample_config = SimpleNamespace(zone_sample_num=16, sample_uniform=False)
    hypotheses = official.sample_point_from_hist_parallel(raw_hist, mask, sample_config)
    write_layout_fr = torch.from_numpy(official_envelope_grid(fr_np)).to(torch.float32)
    patch_info = official.patch_info_from_rect_data(write_layout_fr)

    validation = {
        "mu_sigma_mapping": "FORMAL_STAGE2_AXIAL_Z_AND_SENSOR_SIGMA",
        "sigma_is_standard_deviation_not_variance": True,
        "mask_mapping": "FORMAL_RAW_ZONE_VALIDITY",
        "fr_mapping": "STAGE2_TRUE_PER_ZONE_PROJECTED_RECTANGLES",
        "zone_orientation": "STAGE2_RAW_IDENTITY_CANONICAL_GAUGE",
        "valid_zone_count": valid_zone_count,
        "hypothesis_count": 16,
        "hypotheses_masked_zero": bool(torch.all(hypotheses[~mask] == 0).item()) if (~mask).any() else True,
        "patch_info_source": "official src.utils.dataloader.patch_info_from_rect_data",
        "patch_info_geometry": "stage2_projected_envelope_official_8x8_write_layout",
        "projection": projection_audit,
    }
    moved_patch: dict[Any, Any] = {}
    for key, data in patch_info.items():
        if isinstance(data, dict):
            moved_patch[key] = {subkey: value.unsqueeze(0).to(device) for subkey, value in data.items()}
        else:
            moved_patch[key] = torch.tensor([data], dtype=torch.int64, device=device)
    return DeltarInputs(
        rgb_tensor.to(device), raw_hist, hypotheses.unsqueeze(0).to(device), fr.unsqueeze(0).to(device), mask.unsqueeze(0).to(device), moved_patch, warnings, validation
    )
