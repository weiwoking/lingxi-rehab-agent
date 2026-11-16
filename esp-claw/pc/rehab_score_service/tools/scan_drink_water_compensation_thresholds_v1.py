from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SERVICE_ROOT = Path(__file__).resolve().parents[1]
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from scoring_assets import DERIVED_ROOT as ASSET_DERIVED_ROOT


SCAN_VERSION = "drink_water_compensation_threshold_scan_v1"
DEFAULT_INPUT_DIR = (
    ASSET_DERIVED_ROOT
    / "drink_water_v1"
    / "compensation_feature_diagnosis_v1_1"
)
DEFAULT_OUTPUT_DIR = (
    ASSET_DERIVED_ROOT
    / "drink_water_v1"
    / "compensation_threshold_scan_v1"
)

PRIMARY_FEATURES = ("TrunkCompensationRatio", "TrunkROM")
COMPARISON_FEATURES = (
    "imu2_roll_range",
    "imu2_yaw_range",
    "imu2_gyro_norm_p95",
    "imu2_pitch_positive_excursion",
    "imu2_positive_excursion_duration_ms",
    "imu2_pitch_absolute_excursion",
)
SCAN_FEATURES = PRIMARY_FEATURES + COMPARISON_FEATURES
FORBIDDEN_RULE_FEATURES = {
    "final_score",
    "speed_score",
    "ROM_score",
    "smoothness_score",
    "coordination_score",
    "muscle_score",
}

EXPECTED_TOTAL_REPS = 40
EXPECTED_NORMAL_REPS = 33
EXPECTED_COMPENSATION_REPS = 7

RULE_FIELDS = (
    "rule_id",
    "rule_expression",
    "feature_names",
    "thresholds",
    "TP",
    "FN",
    "FP",
    "TN",
    "recall",
    "precision",
    "specificity",
    "accuracy",
    "f1",
    "balanced_accuracy",
    "normal_false_positive_count",
    "compensation_false_negative_count",
    "pure_normal_false_positive_count",
    "mixed_normal_false_positive_count",
    "pure_compensation_true_positive_count",
    "mixed_compensation_true_positive_count",
    "false_positive_rep_ids",
    "false_negative_rep_ids",
)


@dataclass(frozen=True)
class RepRecord:
    rep_id: str
    source_file: str
    source_category: str
    rep_index: int
    is_compensation: bool
    features: dict[str, float | None]

    @property
    def is_mixed(self) -> bool:
        return "mixed" in self.source_category or "正常-代偿-正常" in self.source_category

    @property
    def is_pure_normal(self) -> bool:
        return not self.is_mixed and not self.is_compensation

    @property
    def is_pure_compensation(self) -> bool:
        return not self.is_mixed and self.is_compensation


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "y"}:
        return True
    if text in {"false", "0", "no", "n", ""}:
        return False
    raise ValueError(f"invalid_bool:{value}")


def parse_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = float(text)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def source_category_from_row(row: dict[str, Any]) -> str:
    category = str(row.get("source_category") or "").strip()
    if category:
        return category
    label = str(row.get("ground_truth_rep_label") or "").strip()
    if "normal_compensation_normal" in label:
        return "mixed"
    if "compensation" in label:
        return "compensation"
    return "normal"


def load_rep_records(input_dir: Path) -> list[RepRecord]:
    csv_path = input_dir / "01_all_40_rep_feature_diagnosis.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"missing_input_csv:{csv_path}")

    records: list[RepRecord] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("empty_feature_diagnosis_csv")
        forbidden = sorted(FORBIDDEN_RULE_FEATURES.intersection(reader.fieldnames))
        if forbidden:
            # Presence is expected in the diagnostic table, but these fields are
            # intentionally excluded from rule generation below.
            pass
        for row_index, row in enumerate(reader, start=1):
            source_file = str(row.get("source_file") or f"source_{row_index}").strip()
            rep_index = int(float(str(row.get("rep_index") or row_index)))
            rep_id = str(row.get("rep_id") or "").strip()
            if not rep_id:
                rep_id = f"{Path(source_file).stem}::rep_{rep_index:02d}"
            features = {feature: parse_float(row.get(feature)) for feature in SCAN_FEATURES}
            records.append(
                RepRecord(
                    rep_id=rep_id,
                    source_file=source_file,
                    source_category=source_category_from_row(row),
                    rep_index=rep_index,
                    is_compensation=parse_bool(row.get("ground_truth_has_compensation")),
                    features=features,
                )
            )
    validate_rep_contract(records)
    return records


def validate_rep_contract(records: list[RepRecord]) -> None:
    total = len(records)
    compensation_count = sum(1 for record in records if record.is_compensation)
    normal_count = total - compensation_count
    if total != EXPECTED_TOTAL_REPS:
        raise ValueError(f"unexpected_rep_count:{total}")
    if normal_count != EXPECTED_NORMAL_REPS or compensation_count != EXPECTED_COMPENSATION_REPS:
        raise ValueError(
            f"unexpected_ground_truth_counts:normal={normal_count},compensation={compensation_count}"
        )


def threshold_candidates(values: Iterable[float | None]) -> list[float]:
    finite_values = sorted({value for value in values if value is not None and math.isfinite(value)})
    if not finite_values:
        return []
    if len(finite_values) == 1:
        value = finite_values[0]
        delta = max(abs(value) * 0.01, 1e-6)
        return [value - delta, value + delta]
    mids = [(left + right) / 2.0 for left, right in zip(finite_values, finite_values[1:])]
    min_gap = min(abs(right - left) for left, right in zip(finite_values, finite_values[1:]) if right != left)
    edge_delta = max(min_gap / 2.0, 1e-9)
    return [finite_values[0] - edge_delta, *mids, finite_values[-1] + edge_delta]


def safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def evaluate_rule(
    records: list[RepRecord],
    rule_id: str,
    expression: str,
    feature_names: list[str],
    thresholds: dict[str, float],
    predicate: Callable[[RepRecord], bool],
) -> dict[str, Any]:
    tp = fn = fp = tn = 0
    false_positive_ids: list[str] = []
    false_negative_ids: list[str] = []
    pure_normal_fp = mixed_normal_fp = pure_comp_tp = mixed_comp_tp = 0

    for record in records:
        predicted = predicate(record)
        actual = record.is_compensation
        if predicted and actual:
            tp += 1
            if record.is_mixed:
                mixed_comp_tp += 1
            else:
                pure_comp_tp += 1
        elif predicted and not actual:
            fp += 1
            false_positive_ids.append(record.rep_id)
            if record.is_mixed:
                mixed_normal_fp += 1
            else:
                pure_normal_fp += 1
        elif not predicted and actual:
            fn += 1
            false_negative_ids.append(record.rep_id)
        else:
            tn += 1

    recall = safe_div(tp, tp + fn)
    precision = safe_div(tp, tp + fp)
    specificity = safe_div(tn, tn + fp)
    accuracy = safe_div(tp + tn, len(records))
    f1 = safe_div(2.0 * precision * recall, precision + recall)
    balanced_accuracy = (recall + specificity) / 2.0
    return {
        "rule_id": rule_id,
        "rule_expression": expression,
        "feature_names": feature_names,
        "thresholds": thresholds,
        "TP": tp,
        "FN": fn,
        "FP": fp,
        "TN": tn,
        "recall": recall,
        "precision": precision,
        "specificity": specificity,
        "accuracy": accuracy,
        "f1": f1,
        "balanced_accuracy": balanced_accuracy,
        "normal_false_positive_count": fp,
        "compensation_false_negative_count": fn,
        "pure_normal_false_positive_count": pure_normal_fp,
        "mixed_normal_false_positive_count": mixed_normal_fp,
        "pure_compensation_true_positive_count": pure_comp_tp,
        "mixed_compensation_true_positive_count": mixed_comp_tp,
        "false_positive_rep_ids": false_positive_ids,
        "false_negative_rep_ids": false_negative_ids,
    }


def single_feature_scan(records: list[RepRecord]) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for feature in SCAN_FEATURES:
        for index, threshold in enumerate(
            threshold_candidates(record.features.get(feature) for record in records),
            start=1,
        ):
            rule_id = f"single_{feature}_{index:03d}"
            rules.append(
                evaluate_rule(
                    records,
                    rule_id,
                    f"{feature} >= {threshold:.12g}",
                    [feature],
                    {feature: threshold},
                    lambda record, f=feature, t=threshold: (
                        record.features.get(f) is not None and record.features[f] >= t
                    ),
                )
            )
    return rules


def ratio_rom_scan(records: list[RepRecord], operator: str) -> list[dict[str, Any]]:
    ratio_thresholds = threshold_candidates(record.features.get("TrunkCompensationRatio") for record in records)
    rom_thresholds = threshold_candidates(record.features.get("TrunkROM") for record in records)
    rules: list[dict[str, Any]] = []
    for ratio_index, ratio_threshold in enumerate(ratio_thresholds, start=1):
        for rom_index, rom_threshold in enumerate(rom_thresholds, start=1):
            rule_id = f"ratio_rom_{operator.lower()}_{ratio_index:03d}_{rom_index:03d}"
            if operator == "AND":
                predicate = lambda record, rt=ratio_threshold, mt=rom_threshold: (
                    record.features.get("TrunkCompensationRatio") is not None
                    and record.features.get("TrunkROM") is not None
                    and record.features["TrunkCompensationRatio"] >= rt
                    and record.features["TrunkROM"] >= mt
                )
            elif operator == "OR":
                predicate = lambda record, rt=ratio_threshold, mt=rom_threshold: (
                    (
                        record.features.get("TrunkCompensationRatio") is not None
                        and record.features["TrunkCompensationRatio"] >= rt
                    )
                    or (
                        record.features.get("TrunkROM") is not None
                        and record.features["TrunkROM"] >= mt
                    )
                )
            else:
                raise ValueError(f"unsupported_operator:{operator}")
            rules.append(
                evaluate_rule(
                    records,
                    rule_id,
                    (
                        f"TrunkCompensationRatio >= {ratio_threshold:.12g} "
                        f"{operator} TrunkROM >= {rom_threshold:.12g}"
                    ),
                    ["TrunkCompensationRatio", "TrunkROM"],
                    {
                        "TrunkCompensationRatio": ratio_threshold,
                        "TrunkROM": rom_threshold,
                    },
                    predicate,
                )
            )
    return rules


def simplicity_rank(rule: dict[str, Any]) -> int:
    return len(rule["feature_names"])


def preferred_feature_rank(rule: dict[str, Any]) -> int:
    features = set(rule["feature_names"])
    if features == {"TrunkCompensationRatio"}:
        return 0
    if features == {"TrunkROM"}:
        return 1
    if features == {"TrunkCompensationRatio", "TrunkROM"}:
        return 2
    return 3


def safety_sort_key(rule: dict[str, Any]) -> tuple[Any, ...]:
    return (
        rule["normal_false_positive_count"],
        -rule["recall"],
        -rule["specificity"],
        simplicity_rank(rule),
        preferred_feature_rank(rule),
        rule["rule_id"],
    )


def balanced_sort_key(rule: dict[str, Any]) -> tuple[Any, ...]:
    return (
        -rule["f1"],
        -rule["balanced_accuracy"],
        rule["normal_false_positive_count"],
        rule["compensation_false_negative_count"],
        simplicity_rank(rule),
        preferred_feature_rank(rule),
        rule["rule_id"],
    )


def demo_sort_key(rule: dict[str, Any]) -> tuple[Any, ...]:
    mixed_missing = 2 - rule["mixed_compensation_true_positive_count"]
    return (
        mixed_missing,
        -rule["recall"],
        rule["normal_false_positive_count"],
        -rule["f1"],
        simplicity_rank(rule),
        preferred_feature_rank(rule),
        rule["rule_id"],
    )


def select_candidates(rules: list[dict[str, Any]]) -> dict[str, Any]:
    safety = min(rules, key=safety_sort_key) if rules else None
    balanced = min(rules, key=balanced_sort_key) if rules else None
    demo_pool = [rule for rule in rules if rule["mixed_compensation_true_positive_count"] == 2]
    demo = min(demo_pool or rules, key=demo_sort_key) if rules else None
    recommended_pool = [rule for rule in rules if rule["recall"] == 1.0 and rule["FP"] <= 1]
    recommended = min(recommended_pool, key=safety_sort_key) if recommended_pool else None
    return {
        "safety_candidate": safety,
        "balanced_candidate": balanced,
        "demo_candidate": demo,
        "recommended_candidate": recommended,
        "manual_decision_required": True,
    }


def source_session_id(record: RepRecord) -> str:
    return record.source_file


def generate_rules_for_records(records: list[RepRecord]) -> list[dict[str, Any]]:
    return [
        *single_feature_scan(records),
        *ratio_rom_scan(records, "AND"),
        *ratio_rom_scan(records, "OR"),
    ]


def evaluate_rule_on_records(rule: dict[str, Any], records: list[RepRecord]) -> dict[str, Any]:
    thresholds = rule["thresholds"]
    expression = rule["rule_expression"]
    feature_names = rule["feature_names"]
    if len(feature_names) == 1:
        feature = feature_names[0]
        threshold = thresholds[feature]
        predicate = lambda record, f=feature, t=threshold: (
            record.features.get(f) is not None and record.features[f] >= t
        )
    elif " AND " in expression:
        predicate = lambda record: (
            record.features.get("TrunkCompensationRatio") is not None
            and record.features.get("TrunkROM") is not None
            and record.features["TrunkCompensationRatio"] >= thresholds["TrunkCompensationRatio"]
            and record.features["TrunkROM"] >= thresholds["TrunkROM"]
        )
    elif " OR " in expression:
        predicate = lambda record: (
            (
                record.features.get("TrunkCompensationRatio") is not None
                and record.features["TrunkCompensationRatio"] >= thresholds["TrunkCompensationRatio"]
            )
            or (
                record.features.get("TrunkROM") is not None
                and record.features["TrunkROM"] >= thresholds["TrunkROM"]
            )
        )
    else:
        raise ValueError(f"cannot_rebuild_rule_predicate:{expression}")
    return evaluate_rule(records, rule["rule_id"], expression, feature_names, thresholds, predicate)


def leave_one_source_session_out(records: list[RepRecord]) -> dict[str, Any]:
    source_ids = sorted({source_session_id(record) for record in records})
    fold_results: list[dict[str, Any]] = []
    total_fp = 0
    total_fn = 0
    recalls: list[float] = []
    specificities: list[float] = []

    for source_id in source_ids:
        train = [record for record in records if source_session_id(record) != source_id]
        heldout = [record for record in records if source_session_id(record) == source_id]
        selected = select_candidates(generate_rules_for_records(train))["balanced_candidate"]
        if selected is None:
            continue
        result = evaluate_rule_on_records(selected, heldout)
        recalls.append(result["recall"])
        specificities.append(result["specificity"])
        total_fp += result["FP"]
        total_fn += result["FN"]
        fold_results.append(
            {
                "heldout_source_session": source_id,
                "selected_rule_id": selected["rule_id"],
                "selected_rule_expression": selected["rule_expression"],
                "TP": result["TP"],
                "FN": result["FN"],
                "FP": result["FP"],
                "TN": result["TN"],
                "recall": result["recall"],
                "specificity": result["specificity"],
            }
        )

    return {
        "fold_count": len(fold_results),
        "mean_recall": sum(recalls) / len(recalls) if recalls else 0.0,
        "mean_specificity": sum(specificities) / len(specificities) if specificities else 0.0,
        "total_false_positives": total_fp,
        "total_false_negatives": total_fn,
        "unstable_fold_count": sum(1 for fold in fold_results if fold["FN"] or fold["FP"]),
        "exploratory_only": True,
        "current_dataset_only": True,
        "not_clinically_validated": True,
        "folds": fold_results,
    }


def serialize_thresholds(thresholds: dict[str, float]) -> str:
    return json.dumps(thresholds, ensure_ascii=False, sort_keys=True)


def serialize_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def write_rules_csv(path: Path, rules: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(RULE_FIELDS))
        writer.writeheader()
        for rule in rules:
            row = dict(rule)
            row["feature_names"] = serialize_list(rule["feature_names"])
            row["thresholds"] = serialize_thresholds(rule["thresholds"])
            row["false_positive_rep_ids"] = serialize_list(rule["false_positive_rep_ids"])
            row["false_negative_rep_ids"] = serialize_list(rule["false_negative_rep_ids"])
            writer.writerow(row)


def write_error_cases(path: Path, rules: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["candidate_type", "case_type", "rep_id"])
        writer.writeheader()
        for name in ("safety_candidate", "balanced_candidate", "demo_candidate", "recommended_candidate"):
            rule = rules.get(name)
            if not rule:
                continue
            for rep_id in rule["false_positive_rep_ids"]:
                writer.writerow({"candidate_type": name, "case_type": "false_positive", "rep_id": rep_id})
            for rep_id in rule["false_negative_rep_ids"]:
                writer.writerow({"candidate_type": name, "case_type": "false_negative", "rep_id": rep_id})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_sha256sums(output_dir: Path) -> None:
    sha_path = output_dir / "SHA256SUMS.txt"
    lines = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path != sha_path:
            lines.append(f"{sha256_file(path)}  {path.relative_to(output_dir).as_posix()}")
    sha_path.write_text("\n".join(lines) + "\n", encoding="ascii")


def output_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_summary(
    records: list[RepRecord],
    candidates: dict[str, Any],
    loso: dict[str, Any],
    input_dir: Path,
) -> dict[str, Any]:
    recommended = candidates["recommended_candidate"]
    return {
        "scan_version": SCAN_VERSION,
        "generated_at": utc_now(),
        "input_dir": str(input_dir),
        "scanned_features": list(SCAN_FEATURES),
        "scan_rules": [
            "feature >= threshold",
            "TrunkCompensationRatio >= ratio_threshold AND TrunkROM >= rom_threshold",
            "TrunkCompensationRatio >= ratio_threshold OR TrunkROM >= rom_threshold",
        ],
        "total_rep_count": len(records),
        "ground_truth_normal_count": sum(1 for record in records if not record.is_compensation),
        "ground_truth_compensation_count": sum(1 for record in records if record.is_compensation),
        "input_detector_hit_count": read_input_detector_hit_count(input_dir),
        "production_detector_modified": False,
        "threshold_modified": False,
        "formal_baseline_modified": False,
        "recommended_candidate": recommended,
        "manual_decision_required": True,
        "exploratory_only": True,
        "current_dataset_only": True,
        "not_clinically_validated": True,
        "leave_one_source_session_out": {
            key: value for key, value in loso.items() if key != "folds"
        },
    }


def read_input_detector_hit_count(input_dir: Path) -> int:
    summary_path = input_dir / "12_diagnosis_summary.json"
    if not summary_path.exists():
        return 0
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0
    value = payload.get("detector_hit_count", 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def write_report(path: Path, summary: dict[str, Any], candidates: dict[str, Any]) -> None:
    lines = [
        "# 喝水前倾代偿阈值扫描 V1",
        "",
        f"- 扫描版本：`{summary['scan_version']}`",
        f"- 输入目录：`{summary['input_dir']}`",
        f"- 总 rep：{summary['total_rep_count']}",
        f"- normal / compensation：{summary['ground_truth_normal_count']} / {summary['ground_truth_compensation_count']}",
        "- 生产 detector：未修改",
        "- 正式 baseline：未修改",
        "- 阈值：未写入生产代码",
        "- 结论性质：exploratory_only / current_dataset_only / not_clinically_validated",
        "",
        "## 候选规则",
    ]
    for key in ("safety_candidate", "balanced_candidate", "demo_candidate", "recommended_candidate"):
        rule = candidates.get(key)
        if rule is None:
            lines.append(f"- {key}: null")
            continue
        lines.append(
            f"- {key}: `{rule['rule_expression']}` TP={rule['TP']} FN={rule['FN']} "
            f"FP={rule['FP']} TN={rule['TN']} F1={rule['f1']:.4f}"
        )
    lines.extend(
        [
            "",
            "## 人工决策",
            "",
            "`manual_decision_required` 固定为 true。即使存在 Recall=1.0 且 FP<=1 的候选规则，也不得自动接入生产 detector。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_scan(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    records = load_rep_records(input_dir)
    single_rules = single_feature_scan(records)
    and_rules = ratio_rom_scan(records, "AND")
    or_rules = ratio_rom_scan(records, "OR")
    all_rules = [*single_rules, *and_rules, *or_rules]
    ranked = sorted(all_rules, key=balanced_sort_key)
    candidates = select_candidates(all_rules)
    loso = leave_one_source_session_out(records)
    summary = build_summary(records, candidates, loso, input_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    write_rules_csv(output_dir / "01_single_feature_scan.csv", single_rules)
    write_rules_csv(output_dir / "02_ratio_rom_and_scan.csv", and_rules)
    write_rules_csv(output_dir / "03_ratio_rom_or_scan.csv", or_rules)
    output_json(output_dir / "04_ranked_candidates.json", {"scan_version": SCAN_VERSION, "rules": ranked})
    output_json(output_dir / "05_selected_candidates.json", {"scan_version": SCAN_VERSION, **candidates})
    write_error_cases(output_dir / "06_error_case_reps.csv", candidates)
    output_json(output_dir / "07_leave_one_source_session_out.json", loso)
    output_json(output_dir / "08_threshold_scan_summary.json", summary)
    write_report(output_dir / "09_threshold_scan_report.md", summary, candidates)
    write_sha256sums(output_dir)
    return summary


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan drink-water trunk compensation threshold candidates.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = run_scan(args.input_dir, args.output_dir)
    print(
        json.dumps(
            {
                "scan_version": summary["scan_version"],
                "output_dir": str(args.output_dir),
                "recommended_candidate": summary["recommended_candidate"],
                "manual_decision_required": summary["manual_decision_required"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
