from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

import pandas as pd

from feature_extractors import lf_detector, tremor_detector
from feature_extractors.lateral_transfer_profile import LF, LF_VERSION, TREMOR, TREMOR_VERSION


@contextmanager
def _lateral_tremor_profile() -> Iterator[None]:
    original = {
        "TREMOR_BAND_RATIO_THRESHOLD": tremor_detector.TREMOR_BAND_RATIO_THRESHOLD,
        "TREMOR_BAND_RMS_THRESHOLD": tremor_detector.TREMOR_BAND_RMS_THRESHOLD,
        "TREMOR_FRACTION_THRESHOLD": tremor_detector.TREMOR_FRACTION_THRESHOLD,
    }
    tremor_detector.TREMOR_BAND_RATIO_THRESHOLD = TREMOR.band_ratio_threshold
    tremor_detector.TREMOR_BAND_RMS_THRESHOLD = TREMOR.band_rms_threshold
    tremor_detector.TREMOR_FRACTION_THRESHOLD = TREMOR.fraction_threshold
    try:
        yield
    finally:
        tremor_detector.TREMOR_BAND_RATIO_THRESHOLD = original["TREMOR_BAND_RATIO_THRESHOLD"]
        tremor_detector.TREMOR_BAND_RMS_THRESHOLD = original["TREMOR_BAND_RMS_THRESHOLD"]
        tremor_detector.TREMOR_FRACTION_THRESHOLD = original["TREMOR_FRACTION_THRESHOLD"]


def detect_lateral_transfer_lf(frame: pd.DataFrame) -> dict[str, Any]:
    result = lf_detector.detect_drink_water_lf_from_rows(frame)
    return {
        **result,
        "version": LF_VERSION,
        "algorithm_source": "drink_water_lf_v2",
        "motion_family": "lateral_transfer",
        "axis": LF.axis,
        "threshold_deg": LF.threshold_deg,
        "min_duration_s": LF.min_duration_s,
        "neutral_window_s": LF.neutral_window_s,
        "median_window_s": LF.median_window_s,
        "lateral_profile_override": False,
        "provisional": False,
    }


def detect_lateral_transfer_tremor(frame: pd.DataFrame) -> dict[str, Any]:
    with _lateral_tremor_profile():
        result = tremor_detector.detect_drink_water_tremor_from_rows(frame)
    return {
        **result,
        "version": TREMOR_VERSION,
        "algorithm_source": "drink_water_tremor_v1_1",
        "motion_family": "lateral_transfer",
        "frequency_band_hz": list(TREMOR.frequency_band_hz),
        "window_seconds": TREMOR.window_seconds,
        "step_seconds": TREMOR.step_seconds,
        "band_ratio_threshold": TREMOR.band_ratio_threshold,
        "band_rms_threshold": TREMOR.band_rms_threshold,
        "tremor_fraction_threshold": TREMOR.fraction_threshold,
        "fraction_threshold": TREMOR.fraction_threshold,
        "lateral_profile_override": {
            "band_ratio_threshold": TREMOR.band_ratio_threshold,
            "band_rms_threshold": TREMOR.band_rms_threshold,
            "positive_window_fraction": TREMOR.fraction_threshold,
        },
        "provisional": False,
    }
