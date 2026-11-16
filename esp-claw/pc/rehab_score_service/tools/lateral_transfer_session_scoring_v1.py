from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from cloud_interface.raw_log_to_trial import build_trial_from_raw_log, validate_fused_scoring_input
from feature_extractors.lateral_transfer_detectors import detect_lateral_transfer_lf, detect_lateral_transfer_tremor
from feature_extractors.lateral_transfer_profile import (
    DERIVED_ROOT,
    LF_EXTRA_PENALTY,
    MOTION_FAMILY,
    SENSOR_MAPPING_VERSION,
    SESSION_SCORING_VERSION,
    TEMPLATE_PATH,
    TEMPLATE_VERSION,
    TREMOR_PENALTY,
)
from tools.lateral_transfer_segmentation_v1 import CSV_ENCODING, segment_lateral_transfer_file


FROZEN_FIELDS = ["score_version", "valid", "final_score", "subscores", "compensation", "fatigue", "brs", "feedback"]
SUBSCORE_FIELDS = ["speed_score", "ROM_score", "smoothness_score", "coordination_score", "muscle_score"]
WINDOWS_RENAME_RETRY_DELAYS_SECONDS = (0.10, 0.20, 0.40, 0.80, 1.00)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rename_directory_with_retry(source: Path, target: Path) -> None:
    try:
        source.rename(target)
        return
    except PermissionError as exc:
        if getattr(exc, "winerror", None) != 5:
            raise
        last_error = exc
    for delay in WINDOWS_RENAME_RETRY_DELAYS_SECONDS:
        time.sleep(delay)
        try:
            source.rename(target)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) != 5:
                raise
            last_error = exc
    raise last_error


def _safe_std(value: float | None) -> float:
    if value is None or not np.isfinite(value) or abs(value) < 1e-8:
        return 1.0
    return float(value)


def _score_by_z(value: float, mean: float | None, std: float | None, max_score: float, mode: str = "two_sided") -> float:
    if mean is None:
        return max_score * 0.6
    z = (float(value) - float(mean)) / _safe_std(std)
    abs_z = abs(z)
    if mode == "lower_better":
        score = max_score if value <= mean else max_score / (1.0 + 0.3 * abs_z)
    elif mode == "higher_better":
        score = max_score if value >= mean else max_score / (1.0 + 0.3 * abs_z)
    else:
        score = max_score / (1.0 + 0.2 * abs_z)
    return max(0.0, min(max_score, float(score)))


def _reasonable_range_speed_score(value: float, config: dict[str, Any] | None, max_score: float = 15.0) -> float | None:
    if not isinstance(config, dict) or config.get("semantics") != "reasonable_range":
        return None
    try:
        value = float(value)
        lower_good = float(config["lower_good"])
        upper_good = float(config["upper_good"])
        lower_extreme = float(config["lower_extreme"])
        upper_extreme = float(config["upper_extreme"])
        min_score = float(config.get("min_score", 5.0))
    except Exception:
        return None
    if not all(np.isfinite(item) for item in (value, lower_good, upper_good, lower_extreme, upper_extreme, min_score)):
        return None
    if not (lower_extreme < lower_good <= upper_good < upper_extreme):
        return None
    if lower_good <= value <= upper_good:
        return max_score
    if value < lower_good:
        if value <= lower_extreme:
            return max(0.0, min(max_score, min_score))
        score = min_score + (max_score - min_score) * ((value - lower_extreme) / (lower_good - lower_extreme))
        return max(0.0, min(max_score, float(score)))
    if value >= upper_extreme:
        return max(0.0, min(max_score, min_score))
    score = min_score + (max_score - min_score) * ((upper_extreme - value) / (upper_extreme - upper_good))
    return max(0.0, min(max_score, float(score)))


def _speed_score(value: float, profile: dict[str, Any], max_score: float = 15.0) -> float:
    configured = _reasonable_range_speed_score(value, profile.get("speed_scoring"), max_score=max_score)
    if configured is not None:
        return configured
    return _score_by_z(value, profile.get("speed_mean"), profile.get("speed_std"), max_score)


def _template_score_profile(template_path: Path) -> dict[str, Any]:
    payload = json.loads(template_path.read_text(encoding="utf-8"))
    if payload.get("template_version") != TEMPLATE_VERSION:
        raise ValueError("lateral_template_version_mismatch")
    if payload.get("motion_family") != MOTION_FAMILY:
        raise ValueError("lateral_template_motion_family_mismatch")
    profile = payload.get("score_profile")
    if not isinstance(profile, dict):
        raise ValueError("lateral_template_missing_score_profile")
    return profile


def _muscle_score(trial: dict[str, Any], profile: dict[str, Any]) -> float:
    scores = []
    for muscle in ("Extensor_digitorum", "Biceps_brachii", "Triceps_brachii", "Extensor_carpi_ulnaris"):
        for feature, max_score in (("RMS", 3.0), ("MAV", 3.0), ("WL_mean", 2.0)):
            key = f"{muscle}_{feature}"
            scores.append(_score_by_z(float(trial.get(key, 0.0)), profile.get(f"{key}_mean"), profile.get(f"{key}_std"), max_score))
    return max(0.0, min(30.0, sum(scores) / 32.0 * 30.0))


def score_lateral_rep(rep_csv: Path, template_path: Path, session_id: str, rep_index: int) -> dict[str, Any]:
    profile = _template_score_profile(template_path)
    trial = build_trial_from_raw_log(rep_csv, patient_id="lateral_transfer_offline", session_id=session_id, task_number=24, trial_id=rep_index)
    speed_score = _speed_score(float(trial["WristMeanSpeed_mm_s"]), profile, 15.0)
    rom_score = _score_by_z(float(trial["UpperArmROM_mm"]), profile.get("ROM_mean"), profile.get("ROM_std"), 20.0, mode="higher_better")
    peaks_score = _score_by_z(float(trial["NumSpeedPeaks"]), profile.get("num_speed_peaks_mean"), profile.get("num_speed_peaks_std"), 12.0)
    pause_score = _score_by_z(float(trial["PauseCount"]), profile.get("pause_count_mean"), profile.get("pause_count_std"), 8.0, mode="lower_better")
    smoothness_score = max(0.0, min(20.0, peaks_score + pause_score))
    coordination_score = (
        _score_by_z(float(trial.get("ForearmRotationRange_deg") or 0.0), profile.get("forearm_rotation_mean"), profile.get("forearm_rotation_std"), 10.0)
        + _score_by_z(float(trial.get("ForearmMeanAngularSpeed_deg_s") or 0.0), profile.get("forearm_angular_speed_mean"), profile.get("forearm_angular_speed_std"), 5.0)
    )
    muscle_score = _muscle_score(trial, profile)
    base_score = speed_score + rom_score + smoothness_score + coordination_score + muscle_score
    from cloud_interface.raw_log_to_trial import read_fused_csv

    rep_df = read_fused_csv(rep_csv)
    lf = detect_lateral_transfer_lf(rep_df)
    tremor = detect_lateral_transfer_tremor(rep_df)
    types = []
    if lf.get("detected"):
        types.append("LF")
    if tremor.get("detected"):
        types.append("TREMOR")
    penalty = (LF_EXTRA_PENALTY if "LF" in types else 0.0) + (TREMOR_PENALTY if "TREMOR" in types else 0.0)
    feedback = []
    if "LF" in types:
        feedback.append("检测到躯干前倾代偿")
    if "TREMOR" in types:
        feedback.append("检测到手部抖动代偿")
    return {
        "score_version": SESSION_SCORING_VERSION,
        "valid": True,
        "final_score": round(max(0.0, min(100.0, base_score - penalty)), 2),
        "subscores": {
            "speed_score": round(speed_score, 2),
            "ROM_score": round(rom_score, 2),
            "smoothness_score": round(smoothness_score, 2),
            "coordination_score": round(coordination_score, 2),
            "muscle_score": round(muscle_score, 2),
        },
        "compensation": {"detected": bool(types), "types": types},
        "fatigue": {"state": "正常", "need_rest": False, "penalty": 0},
        "brs": None,
        "feedback": feedback,
        "_private_details": {
            "base_score": round(base_score, 6),
            "penalty": penalty,
            "lf": lf,
            "tremor": tremor,
            "motion_family": MOTION_FAMILY,
            "sensor_mapping_version": SENSOR_MAPPING_VERSION,
        },
    }


def public_score(score: dict[str, Any]) -> dict[str, Any]:
    return {key: score[key] for key in FROZEN_FIELDS}


def orchestrate_session(source_session: Path, output_root: Path, template_path: Path, session_id: str | None, overwrite: bool) -> dict[str, Any]:
    if not template_path.exists():
        raise FileNotFoundError(f"lateral_transfer_template_missing:{template_path}")
    if session_id is None:
        session_id = source_session.parent.name if source_session.name == "raw_session.csv" else source_session.stem
    output_root.mkdir(parents=True, exist_ok=True)
    session_dir = output_root / session_id
    if session_dir.exists() and not overwrite:
        raise FileExistsError(f"session_output_exists:{session_dir}")
    tmp_dir = output_root / f"{session_id}.tmp_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    rollback_dir = output_root / f"{session_id}.rollback_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    try:
        reps, diagnostic, df = segment_lateral_transfer_file(source_session)
        valid_reps = [rep for rep in reps if rep.get("valid") is True]
        (tmp_dir / "repetitions").mkdir(parents=True)
        (tmp_dir / "per_rep_scores").mkdir(parents=True)
        score_rows = []
        for out_index, rep in enumerate(valid_reps, start=1):
            rep_name = f"rep_{out_index:02d}"
            rep_csv = tmp_dir / "repetitions" / f"{rep_name}.csv"
            rep_df = df.iloc[int(rep["start_index"]) : int(rep["end_index"]) + 1].copy()
            validate_fused_scoring_input(rep_df, source_label=f"{source_session}#{rep_name}")
            rep_df.to_csv(rep_csv, index=False, encoding=CSV_ENCODING)
            score = score_lateral_rep(rep_csv, template_path, session_id, out_index)
            details = score.pop("_private_details")
            write_json(tmp_dir / "per_rep_scores" / f"{rep_name}_score.json", public_score(score))
            write_json(tmp_dir / "per_rep_scores" / f"{rep_name}_score_metadata.json", {"rep": rep, "details": details})
            score_rows.append({"rep_index": out_index, "final_score": score["final_score"], "types": score["compensation"]["types"]})
        summary = {
            "session_version": SESSION_SCORING_VERSION,
            "motion_family": MOTION_FAMILY,
            "session_id": session_id,
            "source_session": str(source_session),
            "source_sha256": sha256_file(source_session),
            "template_path": str(template_path),
            "template_sha256": sha256_file(template_path),
            "segmentation": diagnostic,
            "rep_count": len(valid_reps),
            "average_score": round(float(np.mean([row["final_score"] for row in score_rows])), 2) if score_rows else None,
            "repetitions": score_rows,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        write_json(tmp_dir / "session_manifest.json", {"session_id": session_id, "source_sha256": summary["source_sha256"], "motion_family": MOTION_FAMILY})
        write_json(tmp_dir / "session_summary.json", summary)
        (tmp_dir / "session_score_report.md").write_text(f"# Lateral Transfer Session Report\n\nsession_id: {session_id}\nrep_count: {len(valid_reps)}\n", encoding="utf-8")
        if session_dir.exists():
            rename_directory_with_retry(session_dir, rollback_dir)
            try:
                rename_directory_with_retry(tmp_dir, session_dir)
            except Exception:
                if not session_dir.exists() and rollback_dir.exists():
                    rename_directory_with_retry(rollback_dir, session_dir)
                raise
            shutil.rmtree(rollback_dir)
        else:
            rename_directory_with_retry(tmp_dir, session_dir)
        return summary
    except Exception:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline lateral_transfer session scoring.")
    parser.add_argument("--source-session", required=True, type=Path)
    parser.add_argument("--template", type=Path, default=TEMPLATE_PATH)
    parser.add_argument("--output-root", type=Path, default=DERIVED_ROOT / "session_scoring_v1")
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    summary = orchestrate_session(args.source_session, args.output_root, args.template, args.session_id, args.overwrite)
    print(json.dumps({"session_id": summary["session_id"], "rep_count": summary["rep_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
