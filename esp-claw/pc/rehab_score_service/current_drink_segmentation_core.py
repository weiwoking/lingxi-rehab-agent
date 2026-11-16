"""Current drink-water complete-cycle segmentation core.

This module is mechanically extracted from the former mixed session pipeline
to let current drink/lateral scoring avoid importing the legacy DTW path. Keep
the algorithm body identical unless a dedicated segmentation change is
explicitly requested.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class ActionSegment:
    action_index: int
    start_index: int
    end_index: int
    start_time_s: float
    end_time_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_time_s - self.start_time_s)


@dataclass(frozen=True)
class DrinkCycleSegmentationConfig:
    """Timing and pose thresholds for the drink complete-cycle state machine."""

    initial_baseline_candidate_s: float = 0.5
    stable_baseline_window_s: float = 0.25
    baseline_search_limit_s: float = 2.0
    movement_confirm_s: float = 0.18
    quiet_confirm_s: float = 3.0
    session_tail_quiet_s: float = 0.5
    segment_pre_padding_s: float = 0.4
    segment_tail_padding_s: float = 0.5
    next_baseline_window_s: float = 0.8
    minimum_departure_deg: float = 10.0
    baseline_stability_radius_deg: float = 8.0
    quiet_pose_radius_deg: float = 24.0
    return_peak_ratio: float = 0.65
    minimum_peak_deg: float = 20.0
    minimum_start_motion_deg_s: float = 25.0
    maximum_quiet_motion_deg_s: float = 90.0


DRINK_CYCLE_CONFIG = DrinkCycleSegmentationConfig()


def _smooth(values: np.ndarray, window: int) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return values
    window = max(1, min(int(window), len(values)))
    if window <= 1:
        return values
    kernel = np.ones(window, dtype=float) / float(window)
    return np.convolve(values, kernel, mode="same")


def _imu_prefix(imu_segments: Dict[str, str], segment: str, default: str) -> str:
    if segment in ("hand", "wrist", "hand_wrist"):
        return imu_segments.get(
            "hand_wrist",
            imu_segments.get("hand", imu_segments.get("wrist", default)),
        )
    return imu_segments.get(segment, default)


def _circular_angle_delta_deg(values: np.ndarray, reference: float) -> np.ndarray:
    """Return signed shortest-path angle differences in [-180, 180)."""
    return (
        np.asarray(values, dtype=float) - float(reference) + 180.0
    ) % 360.0 - 180.0


def _circular_center_deg(values: np.ndarray) -> float:
    radians = np.radians(np.asarray(values, dtype=float))
    return float(
        np.degrees(
            np.arctan2(np.mean(np.sin(radians)), np.mean(np.cos(radians)))
        )
    )


def _drink_time_seconds(df: pd.DataFrame) -> np.ndarray:
    """Prefer time_ms while repairing missing, duplicate, or decreasing timestamps."""
    if len(df) == 0:
        return np.asarray([], dtype=float)
    if "time_ms" not in df.columns:
        return np.arange(len(df), dtype=float) / 50.0
    raw = pd.to_numeric(df["time_ms"], errors="coerce").interpolate(
        limit_direction="both"
    )
    if raw.isna().all():
        return np.arange(len(df), dtype=float) / 50.0
    values = raw.fillna(0.0).to_numpy(dtype=float)
    positive_steps = np.diff(values)
    positive_steps = positive_steps[
        np.isfinite(positive_steps) & (positive_steps > 0)
    ]
    step_ms = float(np.median(positive_steps)) if len(positive_steps) else 20.0
    repaired = np.empty(len(values), dtype=float)
    repaired[0] = values[0]
    for index in range(1, len(values)):
        candidate = values[index]
        repaired[index] = (
            candidate
            if np.isfinite(candidate) and candidate > repaired[index - 1]
            else repaired[index - 1] + step_ms
        )
    return (repaired - repaired[0]) / 1000.0


def _drink_pose_matrix(
    df: pd.DataFrame,
    imu_segments: Dict[str, str],
) -> Tuple[Optional[np.ndarray], Optional[str]]:
    prefixes = (
        _imu_prefix(imu_segments, "forearm", "imu3"),
        _imu_prefix(imu_segments, "hand_wrist", "imu4"),
    )
    columns = [
        f"{prefix}_{axis}"
        for prefix in prefixes
        for axis in ("roll", "pitch", "yaw")
    ]
    missing = [column for column in columns if column not in df.columns]
    if missing:
        return None, f"missing_pose_columns:{','.join(missing)}"
    pose = df.loc[:, columns].apply(pd.to_numeric, errors="coerce")
    if any(pose[column].notna().sum() == 0 for column in columns):
        return None, "pose_columns_have_no_valid_values"
    pose = pose.interpolate(limit_direction="both").ffill().bfill()
    return pose.to_numpy(dtype=float), None


def _drink_pose_center(pose: np.ndarray) -> np.ndarray:
    center = np.median(pose, axis=0)
    center = np.asarray(center, dtype=float)
    center[2] = _circular_center_deg(pose[:, 2])
    center[5] = _circular_center_deg(pose[:, 5])
    return center


def _drink_relative_pose(
    pose: np.ndarray,
    center: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    relative = np.asarray(pose, dtype=float) - np.asarray(center, dtype=float)
    relative[:, 2] = _circular_angle_delta_deg(pose[:, 2], center[2])
    relative[:, 5] = _circular_angle_delta_deg(pose[:, 5], center[5])
    forearm = np.linalg.norm(relative[:, :3], axis=1)
    hand_wrist = np.linalg.norm(relative[:, 3:], axis=1)
    combined = np.sqrt((forearm * forearm + hand_wrist * hand_wrist) / 2.0)
    return forearm, hand_wrist, combined


def _drink_motion_energy(
    df: pd.DataFrame,
    imu_segments: Dict[str, str],
    pose: np.ndarray,
    time_s: np.ndarray,
) -> np.ndarray:
    prefixes = (
        _imu_prefix(imu_segments, "forearm", "imu3"),
        _imu_prefix(imu_segments, "hand_wrist", "imu4"),
    )
    gyro_columns = [
        f"{prefix}_g{axis}" for prefix in prefixes for axis in ("x", "y", "z")
    ]
    if all(column in df.columns for column in gyro_columns):
        gyro = (
            df.loc[:, gyro_columns]
            .apply(pd.to_numeric, errors="coerce")
            .interpolate(limit_direction="both")
            .fillna(0.0)
            .to_numpy(dtype=float)
        )
        forearm = np.linalg.norm(gyro[:, :3], axis=1)
        hand_wrist = np.linalg.norm(gyro[:, 3:], axis=1)
        energy = np.sqrt((forearm * forearm + hand_wrist * hand_wrist) / 2.0)
    else:
        delta = np.diff(pose, axis=0, prepend=pose[[0]])
        delta[:, 2] = _circular_angle_delta_deg(pose[:, 2], np.roll(pose[:, 2], 1))
        delta[:, 5] = _circular_angle_delta_deg(pose[:, 5], np.roll(pose[:, 5], 1))
        delta[0] = 0.0
        dt = np.diff(time_s, prepend=time_s[0])
        positive_dt = dt[dt > 0]
        fallback_dt = float(np.median(positive_dt)) if len(positive_dt) else 0.02
        dt = np.where(dt > 0, dt, fallback_dt)
        forearm = np.linalg.norm(delta[:, :3], axis=1) / dt
        hand_wrist = np.linalg.norm(delta[:, 3:], axis=1) / dt
        energy = np.sqrt((forearm * forearm + hand_wrist * hand_wrist) / 2.0)
    sample_rate = (
        max(1.0, 1.0 / np.median(np.diff(time_s)))
        if len(time_s) > 1
        else 50.0
    )
    return _smooth(energy, max(1, int(round(sample_rate * 0.12))))


def _first_index_at_or_after(time_s: np.ndarray, target_s: float) -> int:
    return min(len(time_s) - 1, int(np.searchsorted(time_s, target_s, side="left")))


def _drink_baseline_window(
    pose: np.ndarray,
    time_s: np.ndarray,
    motion_energy: np.ndarray,
    config: DrinkCycleSegmentationConfig,
) -> Optional[Tuple[int, int]]:
    candidate_end = _first_index_at_or_after(
        time_s, config.initial_baseline_candidate_s
    )
    candidate_pose = pose[: candidate_end + 1]
    candidate_center = _drink_pose_center(candidate_pose)
    _, _, candidate_deviation = _drink_relative_pose(candidate_pose, candidate_center)
    candidate_stable = (
        float(np.percentile(candidate_deviation, 90))
        <= config.baseline_stability_radius_deg
        and float(np.percentile(motion_energy[: candidate_end + 1], 75))
        <= config.maximum_quiet_motion_deg_s
    )
    if candidate_stable:
        return 0, candidate_end

    search_end = _first_index_at_or_after(time_s, config.baseline_search_limit_s)
    for start in range(search_end + 1):
        end = _first_index_at_or_after(
            time_s, time_s[start] + config.stable_baseline_window_s
        )
        if end > search_end or time_s[end] - time_s[start] < (
            config.stable_baseline_window_s - 0.02
        ):
            break
        window_pose = pose[start : end + 1]
        center = _drink_pose_center(window_pose)
        _, _, deviation = _drink_relative_pose(window_pose, center)
        if (
            float(np.percentile(deviation, 90))
            <= config.baseline_stability_radius_deg
            and float(np.percentile(motion_energy[start : end + 1], 75))
            <= config.maximum_quiet_motion_deg_s
        ):
            return start, end
    return None


def segment_drink_complete_cycles(
    df: pd.DataFrame,
    imu_segments: Dict[str, str],
    config: DrinkCycleSegmentationConfig = DRINK_CYCLE_CONFIG,
) -> Tuple[List[ActionSegment], Dict[str, Any]]:
    """Segment complete drink cycles with dynamic post-action rest baselines.

    A cycle starts only after both forearm and wrist leave the current baseline
    during sustained motion. It can finish only after returning at least 35%
    from its peak and then remaining locally quiet for three seconds. The final
    cycle may use the documented 0.5-second end-of-file quiet-tail exception.
    """
    diagnostic: Dict[str, Any] = {
        "method": "drink_complete_cycle_dynamic_rest_v1",
        "fallback_used": False,
        "repetition_count": 0,
        "incomplete_cycle": False,
        "diagnostic": None,
    }
    if len(df) == 0:
        diagnostic["diagnostic"] = "empty_session"
        return [], diagnostic

    time_s = _drink_time_seconds(df)
    pose, pose_error = _drink_pose_matrix(df, imu_segments)
    if pose is None:
        diagnostic["diagnostic"] = pose_error
        return [], diagnostic
    motion_energy = _drink_motion_energy(df, imu_segments, pose, time_s)
    baseline_window = _drink_baseline_window(
        pose, time_s, motion_energy, config
    )
    if baseline_window is None:
        diagnostic["diagnostic"] = "no_stable_initial_baseline"
        return [], diagnostic

    baseline_start, baseline_end = baseline_window
    baseline_center = _drink_pose_center(pose[baseline_start : baseline_end + 1])
    baseline_forearm, baseline_hand, _ = _drink_relative_pose(
        pose[baseline_start : baseline_end + 1], baseline_center
    )
    forearm_threshold = max(
        config.minimum_departure_deg,
        float(np.percentile(baseline_forearm, 90)) * 3.0 + 3.0,
    )
    hand_threshold = max(
        config.minimum_departure_deg,
        float(np.percentile(baseline_hand, 90)) * 3.0 + 3.0,
    )
    baseline_motion = float(
        np.percentile(motion_energy[baseline_start : baseline_end + 1], 75)
    )
    start_motion_threshold = max(
        config.minimum_start_motion_deg_s, baseline_motion * 2.0
    )

    segments: List[ActionSegment] = []
    active = False
    movement_start: Optional[int] = None
    action_start = 0
    peak_deviation = 0.0
    returned_from_peak = False
    quiet_start: Optional[int] = None
    quiet_center: Optional[np.ndarray] = None
    last_confirmation_index: Optional[int] = None

    index = baseline_end + 1
    while index < len(df):
        forearm, hand, combined = _drink_relative_pose(
            pose[index : index + 1], baseline_center
        )
        current_deviation = float(combined[0])
        if not active:
            departed = (
                float(forearm[0]) >= forearm_threshold
                and float(hand[0]) >= hand_threshold
            )
            moving = float(motion_energy[index]) >= start_motion_threshold
            if departed and moving:
                if movement_start is None:
                    movement_start = index
                if time_s[index] - time_s[movement_start] >= config.movement_confirm_s:
                    active = True
                    action_start = _first_index_at_or_after(
                        time_s,
                        max(0.0, time_s[movement_start] - config.segment_pre_padding_s),
                    )
                    _, _, initial_deviation = _drink_relative_pose(
                        pose[movement_start : index + 1], baseline_center
                    )
                    peak_deviation = float(np.max(initial_deviation))
                    movement_start = None
            else:
                movement_start = None
            index += 1
            continue

        peak_deviation = max(peak_deviation, current_deviation)
        returned_now = (
            peak_deviation >= config.minimum_peak_deg
            and current_deviation <= peak_deviation * config.return_peak_ratio
        )
        returned_from_peak = returned_from_peak or returned_now
        quiet_motion = float(motion_energy[index]) <= config.maximum_quiet_motion_deg_s
        if returned_now and quiet_motion:
            if quiet_start is None:
                quiet_start = index
                quiet_center = pose[index].copy()
            assert quiet_center is not None
            _, _, quiet_deviation = _drink_relative_pose(
                pose[index : index + 1], quiet_center
            )
            if float(quiet_deviation[0]) > config.quiet_pose_radius_deg:
                quiet_start = None
                quiet_center = None
            elif time_s[index] - time_s[quiet_start] >= config.quiet_confirm_s:
                segment_end = _first_index_at_or_after(
                    time_s,
                    time_s[quiet_start] + config.segment_tail_padding_s,
                )
                segment_end = min(segment_end, index, len(df) - 1)
                segments.append(
                    ActionSegment(
                        action_index=len(segments) + 1,
                        start_index=action_start,
                        end_index=segment_end,
                        start_time_s=float(time_s[action_start]),
                        end_time_s=float(time_s[segment_end]),
                    )
                )
                baseline_from = _first_index_at_or_after(
                    time_s,
                    max(time_s[quiet_start], time_s[index] - config.next_baseline_window_s),
                )
                baseline_center = _drink_pose_center(pose[baseline_from : index + 1])
                active = False
                peak_deviation = 0.0
                returned_from_peak = False
                quiet_start = None
                quiet_center = None
                last_confirmation_index = index
        else:
            quiet_start = None
            quiet_center = None
        index += 1

    if active:
        tail_quiet_duration = (
            float(time_s[-1] - time_s[quiet_start])
            if quiet_start is not None
            else 0.0
        )
        sample_period_s = (
            float(np.median(np.diff(time_s))) if len(time_s) > 1 else 0.0
        )
        if (
            returned_from_peak
            and quiet_start is not None
            and tail_quiet_duration + sample_period_s
            >= config.session_tail_quiet_s
        ):
            segments.append(
                ActionSegment(
                    action_index=len(segments) + 1,
                    start_index=action_start,
                    end_index=len(df) - 1,
                    start_time_s=float(time_s[action_start]),
                    end_time_s=float(time_s[-1]),
                )
            )
            diagnostic["session_tail_exception_used"] = True
        else:
            diagnostic["incomplete_cycle"] = True
            diagnostic["diagnostic"] = (
                "incomplete_cycle_at_session_end:"
                f"returned={returned_from_peak},tail_quiet_s={tail_quiet_duration:.3f}"
            )

    diagnostic.update(
        {
            "repetition_count": len(segments),
            "initial_baseline": {
                "start_time_s": round(float(time_s[baseline_start]), 3),
                "end_time_s": round(float(time_s[baseline_end]), 3),
            },
            "departure_threshold_deg": {
                "forearm": round(forearm_threshold, 3),
                "hand_wrist": round(hand_threshold, 3),
            },
            "start_motion_threshold_deg_s": round(start_motion_threshold, 3),
            "quiet_motion_threshold_deg_s": config.maximum_quiet_motion_deg_s,
            "return_peak_ratio": config.return_peak_ratio,
            "quiet_confirm_s": config.quiet_confirm_s,
            "last_confirmation_time_s": (
                round(float(time_s[last_confirmation_index]), 3)
                if last_confirmation_index is not None
                else None
            ),
        }
    )
    if not segments and diagnostic["diagnostic"] is None:
        diagnostic["diagnostic"] = "no_complete_cycle_detected"
    return segments, diagnostic
