"""Canonical paths for immutable scoring assets and calibration outputs.

Patient training facts belong under ``REHAB_TRAINING_DATA_ROOT``. Templates,
standards, calibration cohorts, and derived validation artifacts belong under
the independent scoring-assets authority. Resolving paths does not create or
modify files.
"""

from __future__ import annotations

import os
from pathlib import Path


DEFAULT_SCORING_ASSETS_ROOT = Path(__file__).resolve().parents[3] / "external" / "_rehab_scoring_assets"
SCORING_ASSETS_ROOT = Path(
    os.getenv("REHAB_SCORING_ASSETS_ROOT", str(DEFAULT_SCORING_ASSETS_ROOT))
).resolve()

TEMPLATE_ROOT = SCORING_ASSETS_ROOT / "templates"
DERIVED_ROOT = SCORING_ASSETS_ROOT / "derived"
STANDARD_ROOT = SCORING_ASSETS_ROOT / "standards"
CALIBRATION_SESSIONS_ROOT = SCORING_ASSETS_ROOT / "calibration_sessions"


def scoring_asset_path(*parts: str) -> Path:
    """Return a path contained by the configured scoring-assets authority."""
    candidate = SCORING_ASSETS_ROOT.joinpath(*parts).resolve()
    try:
        candidate.relative_to(SCORING_ASSETS_ROOT)
    except ValueError as exc:
        raise ValueError("scoring asset path escapes REHAB_SCORING_ASSETS_ROOT") from exc
    return candidate
