from __future__ import annotations

from collections import Counter
from typing import Any, Callable

import numpy as np

from .official_object_benchmark import percentile_summary


def error_record(predicted: float | None, truth: float | None) -> dict[str, float | None]:
    if predicted is None or truth is None:
        return {"absolute_error_m": None, "relative_error": None, "signed_error_m": None}
    signed = float(predicted - truth)
    return {"absolute_error_m": abs(signed), "relative_error": abs(signed) / max(float(truth), 1e-6), "signed_error_m": signed}


def summarize(records: list[dict[str, Any]], prefix: str) -> dict[str, Any]:
    valid = [row for row in records if row.get(f"{prefix}_absolute_error_m") is not None]
    absolute = [float(row[f"{prefix}_absolute_error_m"]) for row in valid]
    relative = [float(row[f"{prefix}_relative_error"]) for row in valid]
    total = len(records)
    return {
        "object_count": total,
        "valid_count": len(valid),
        "rejection_count": total - len(valid),
        "rejection_rate": (total - len(valid)) / max(total, 1),
        "false_valid_count": 0,
        "absolute_error_m": percentile_summary(absolute),
        "relative_error": percentile_summary(relative),
        "within_5_percent_count": sum(value <= 0.05 for value in relative),
        "within_5_percent_rate": sum(value <= 0.05 for value in relative) / max(len(valid), 1),
        "within_10_percent_count": sum(value <= 0.10 for value in relative),
        "within_10_percent_rate": sum(value <= 0.10 for value in relative) / max(len(valid), 1),
        "within_25_percent_count": sum(value <= 0.25 for value in relative),
        "within_25_percent_rate": sum(value <= 0.25 for value in relative) / max(len(valid), 1),
        "within_50_percent_count": sum(value <= 0.50 for value in relative),
        "within_50_percent_rate": sum(value <= 0.50 for value in relative) / max(len(valid), 1),
        "rejection_reasons": dict(Counter(row.get(f"{prefix}_rejection_reason") or "valid" for row in records)),
    }


def grouped(records: list[dict[str, Any]], key: str, prefix: str) -> dict[str, Any]:
    values = sorted({str(row.get(key)) for row in records})
    return {value: summarize([row for row in records if str(row.get(key)) == value], prefix) for value in values}


def numeric_grouped(records: list[dict[str, Any]], name: str, selector: Callable[[dict[str, Any]], str], prefix: str) -> dict[str, Any]:
    tagged = [{**row, name: selector(row)} for row in records]
    return grouped(tagged, name, prefix)
