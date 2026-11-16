"""Reference replay model for rehab_task_decision_input.v4 -> compact v1.

This test-only model mirrors the C adapter contract. It never calls an LLM or
mutates the production formal input; v4 remains authoritative.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from statistics import fmean
from typing import Any


SOURCE_SCHEMA = "rehab_task_decision_input.v4"
COMPACT_SCHEMA = "rehab_task_decision_llm_input.v1"
OUTPUT_SCHEMA = "rehab_task_decision_output.v4"
PREPARATION_SCHEMA = "rehab_task_decision_preparation.v1.1"
EVIDENCE_REDACTION = "[REDACTED_IN_EVIDENCE; SECURE_REPLAY_INJECTS_ORIGINAL_RAW_TEXT]"


INTENTIONALLY_OMITTED_FIELDS = [
    "scene.objects",
    "scene.scan_id (duplicate of decision_binding.source_scan_id)",
    "scene.sha256 (duplicate of decision_binding.source_scene_sha256)",
    "semantic_groups (candidate semantic_group and target raw_class retained)",
    "patient_condition.source/source_bytes/sent_bytes",
    "patient_ability.source/matched_line",
    "recent_performance.records[*].session_id/task_name",
    "item_frequency.records",
    "item_frequency.unknown_history_policy",
    "latest_compensation.mapping_status",
    "scoring_policy.component_rules",
    "scoring_policy.hard_gate_rules",
    "scoring_policy.policy_source",
    "scoring_policy.scoring_authority prose",
    "scoring_policy parameters/formulas/rounding (consumed by P4 preparation and validator)",
    "decision_preparation.candidates[*].advice_score_lookup",
    "candidates[*].hard_gates.allowed_by_ability alias",
    "candidates[*].target_facts.source_frame_id/source_frame_sequence",
    "candidates[*].target_facts.bbox_xyxy",
    "candidates[*].target_facts.object_height_mm/top_height_mm/distance_mm",
    "candidates[*].target_facts.not_border and *_known/status mirrors not needed by the structured rule",
    "duplicate candidate target_facts (replaced by target_reference)",
    "warnings and source image paths when present",
]


class CompactAdapterError(ValueError):
    """Fail-closed source/adapter contract error."""


def canonical_json_bytes(value: Any) -> int:
    return len(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    )


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CompactAdapterError(f"{path}: expected object")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise CompactAdapterError(f"{path}: expected array")
    return value


def _required(obj: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        raise CompactAdapterError(f"{path}.{key}: missing")
    return obj[key]


def _target_projection(facts: Mapping[str, Any]) -> dict[str, Any] | None:
    target_object_id = _required(facts, "target_object_id", "target_facts")
    if target_object_id is None:
        return None
    if not isinstance(target_object_id, str) or not target_object_id:
        raise CompactAdapterError("target_facts.target_object_id: invalid")
    projection = {
        "exists": True,
        "target_object_id": target_object_id,
        "display_group_name": _required(
            facts, "display_group_name", "target_facts"
        ),
        "raw_class": _required(facts, "raw_class", "target_facts"),
        "plane": _required(facts, "plane", "target_facts"),
        "height": {
            "status": _required(facts, "height_status", "target_facts"),
            "support_height_mm": _required(
                facts, "support_height_mm", "target_facts"
            ),
            "confidence": _required(
                facts, "height_confidence", "target_facts"
            ),
        },
        "geometry": {
            "valid": _required(facts, "geometry_valid", "target_facts"),
            "valid_known": _required(
                facts, "geometry_valid_known", "target_facts"
            ),
            "confidence": _required(
                facts, "geometry_confidence", "target_facts"
            ),
            "confidence_status": _required(
                facts, "geometry_confidence_status", "target_facts"
            ),
        },
        "perception": {
            "mode": _required(facts, "perception_mode", "target_facts"),
            "detection_confidence": _required(
                facts, "detection_confidence", "target_facts"
            ),
            "detection_confidence_status": _required(
                facts, "detection_confidence_status", "target_facts"
            ),
            "quality": _required(
                facts, "perception_quality", "target_facts"
            ),
        },
        "visibility": {
            "full_extent_visible": _required(
                facts, "full_extent_visible", "target_facts"
            ),
            "touches_border": _required(
                facts, "touches_border", "target_facts"
            ),
        },
    }
    return projection


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return min(max(value, minimum), maximum)


def _prepared_candidate(
    source: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    ref = _required(candidate, "candidate_ref", "candidate")
    availability = _required(candidate, "availability", "candidate")
    if availability == "blocked":
        return {
            "candidate_ref": ref,
            "availability": "blocked",
            "fixed_components": None,
        }
    if availability != "available":
        raise CompactAdapterError(f"candidates[{ref}].availability: invalid")

    policy = _mapping(
        _required(source, "scoring_policy", "root"), "scoring_policy"
    )
    parameters = _mapping(
        _required(policy, "parameters", "scoring_policy"),
        "scoring_policy.parameters",
    )

    def parameter(name: str) -> float:
        value = _required(parameters, name, "scoring_policy.parameters")
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise CompactAdapterError(f"scoring_policy.parameters.{name}: invalid")
        return float(value)

    recent = _mapping(
        _required(source, "recent_performance", "root"),
        "recent_performance",
    )
    tendency = _required(recent, "tendency_level", "recent_performance")
    if not isinstance(tendency, str) or not tendency.startswith("level_"):
        raise CompactAdapterError("recent_performance.tendency_level: invalid")
    suffix = tendency.removeprefix("level_")
    if not suffix.isdigit() or int(suffix) <= 0:
        raise CompactAdapterError("recent_performance.tendency_level: invalid")
    level = _required(candidate, "level", f"candidates[{ref}]")
    if not isinstance(level, int) or level <= 0:
        raise CompactAdapterError(f"candidates[{ref}].level: invalid")
    delta = level - int(suffix)
    difficulty = parameter("difficulty_max")
    if delta < 0:
        difficulty -= parameter("difficulty_easier_step") * -delta
    elif delta > 0:
        difficulty -= parameter("difficulty_harder_step") * delta
    difficulty = _clamp(difficulty, 0.0, parameter("difficulty_max"))

    latest = _mapping(
        _required(source, "latest_compensation", "root"),
        "latest_compensation",
    )
    severity = _required(latest, "severity", "latest_compensation")
    if not isinstance(severity, (int, float)) or not 0 <= severity <= 1:
        raise CompactAdapterError("latest_compensation.severity: invalid")
    relatedness = 0.0
    status = _required(latest, "status", "latest_compensation")
    if status == "available":
        if latest.get("exact_task_id") == candidate.get("task_id"):
            relatedness = parameter("relatedness_exact_task")
        elif latest.get("action_family") == candidate.get("action_family"):
            relatedness = parameter("relatedness_same_action_family")
        elif latest.get("mapped_group") == candidate.get("semantic_group"):
            relatedness = parameter("relatedness_same_object_group")
    elif status != "none":
        raise CompactAdapterError("latest_compensation.status: invalid")
    compensation = parameter("compensation_max") * (
        1.0 - float(severity) * relatedness
    )

    frequency = _mapping(
        _required(source, "item_frequency", "root"), "item_frequency"
    )
    group = _required(candidate, "semantic_group", f"candidates[{ref}]")
    if group not in ("cup", "ball"):
        raise CompactAdapterError(f"candidates[{ref}].semantic_group: invalid")
    count = _required(frequency, f"{group}_count", "item_frequency")
    if not isinstance(count, int) or count < 0:
        raise CompactAdapterError(f"item_frequency.{group}_count: invalid")
    frequency_score = parameter(
        f"frequency_{count}" if count < 3 else "frequency_3"
    )

    facts = _mapping(
        _required(candidate, "target_facts", f"candidates[{ref}]"),
        f"candidates[{ref}].target_facts",
    )
    quality = facts.get("perception_quality")
    if not isinstance(quality, (int, float)) or not math.isfinite(quality) or not 0 <= quality <= 1:
        detection = facts.get("detection_confidence", 0.0)
        height = facts.get("height_confidence", 0.0)
        detection = _clamp(float(detection) if isinstance(detection, (int, float)) else 0.0, 0.0, 1.0)
        height = _clamp(float(height) if isinstance(height, (int, float)) else 0.0, 0.0, 1.0)
        geometry = facts.get("geometry_confidence")
        if isinstance(geometry, (int, float)) and math.isfinite(geometry):
            quality = (
                parameter("perception_geometry_weight") * _clamp(float(geometry), 0.0, 1.0)
                + parameter("perception_detection_weight") * detection
                + parameter("perception_height_weight") * height
            )
        else:
            quality = (
                parameter("fallback_detection_weight") * detection
                + parameter("fallback_height_weight") * height
            )
        quality = _clamp(float(quality), 0.0, 1.0)
    perception = parameter("perception_min") + parameter("perception_span") * float(quality)
    fixed_total = difficulty + compensation + frequency_score + perception
    return {
        "candidate_ref": ref,
        "availability": "available",
        "fixed_components": {
            "difficulty": difficulty,
            "compensation": compensation,
            "frequency": frequency_score,
            "perception": perception,
            "fixed_total": fixed_total,
        },
    }


def _decision_preparation(source: Mapping[str, Any]) -> dict[str, Any]:
    policy = _mapping(
        _required(source, "scoring_policy", "root"), "scoring_policy"
    )
    if _required(policy, "score_rounding", "scoring_policy") != (
        "round_half_away_to_3_decimals"
    ):
        raise CompactAdapterError("scoring_policy.score_rounding: invalid")
    candidates = _list(_required(source, "candidates", "root"), "candidates")
    return {
        "schema_version": PREPARATION_SCHEMA,
        "source_policy_version": _required(
            policy, "decision_rules_version", "scoring_policy"
        ),
        "fixed_component_authority": "P4",
        "business_authority": "DeepSeek",
        "advice_policy": {
            "range": [16, 25],
            "positive_range": [21, 25],
            "negative_range": [16, 19],
            "neutral": 20,
        },
        "tie_break_candidate_refs": list(
            _list(
                _required(policy, "tie_break_candidate_refs", "scoring_policy"),
                "scoring_policy.tie_break_candidate_refs",
            )
        ),
        "candidates": [
            _prepared_candidate(source, _mapping(candidate, f"candidates[{ref}]"))
            for ref, candidate in enumerate(candidates)
        ],
    }


def prepare_formal_input(formal_input: Mapping[str, Any]) -> dict[str, Any]:
    """Return only the deterministic preparation projection for host replay."""
    source = _mapping(formal_input, "root")
    if _required(source, "schema_version", "root") != SOURCE_SCHEMA:
        raise CompactAdapterError("root.schema_version: expected v4")
    return _decision_preparation(source)


def adapt_formal_input(
    formal_input: Mapping[str, Any], *, redact_patient: bool = False
) -> dict[str, Any]:
    """Return a compact projection without mutating the full formal input."""

    source = _mapping(formal_input, "root")
    if _required(source, "schema_version", "root") != SOURCE_SCHEMA:
        raise CompactAdapterError("root.schema_version: expected v4")

    binding = _mapping(
        _required(source, "decision_binding", "root"), "decision_binding"
    )
    patient_condition = _mapping(
        _required(source, "patient_condition", "root"), "patient_condition"
    )
    patient_ability = _mapping(
        _required(source, "patient_ability", "root"), "patient_ability"
    )
    recent = _mapping(
        _required(source, "recent_performance", "root"),
        "recent_performance",
    )
    frequency = _mapping(
        _required(source, "item_frequency", "root"), "item_frequency"
    )
    compensation = _mapping(
        _required(source, "latest_compensation", "root"),
        "latest_compensation",
    )

    source_candidates = _list(
        _required(source, "candidates", "root"), "candidates"
    )
    if len(source_candidates) != 5:
        raise CompactAdapterError("candidates: expected exactly five")

    targets: list[dict[str, Any]] = []
    target_refs_by_key: dict[str, str] = {}
    candidates: list[dict[str, Any]] = []
    for expected_ref, source_candidate_value in enumerate(source_candidates):
        source_candidate = _mapping(
            source_candidate_value, f"candidates[{expected_ref}]"
        )
        candidate_ref = _required(
            source_candidate, "candidate_ref", f"candidates[{expected_ref}]"
        )
        if candidate_ref != expected_ref:
            raise CompactAdapterError(
                f"candidates[{expected_ref}].candidate_ref: expected {expected_ref}"
            )
        gates = _mapping(
            _required(
                source_candidate, "hard_gates", f"candidates[{expected_ref}]"
            ),
            f"candidates[{expected_ref}].hard_gates",
        )
        facts = _mapping(
            _required(
                source_candidate, "target_facts", f"candidates[{expected_ref}]"
            ),
            f"candidates[{expected_ref}].target_facts",
        )
        projected_target = _target_projection(facts)
        target_reference: str | None = None
        if projected_target is not None:
            dedupe_key = json.dumps(
                projected_target,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            target_reference = target_refs_by_key.get(dedupe_key)
            if target_reference is None:
                target_reference = f"target-{len(targets)}"
                target_refs_by_key[dedupe_key] = target_reference
                targets.append(
                    {"target_ref": target_reference, **projected_target}
                )

        candidate_path = f"candidates[{expected_ref}]"
        candidates.append(
            {
                "candidate_ref": candidate_ref,
                "task_id": _required(source_candidate, "task_id", candidate_path),
                "task_name": _required(
                    source_candidate, "task_name", candidate_path
                ),
                "level": _required(source_candidate, "level", candidate_path),
                "semantic_group": _required(
                    source_candidate, "semantic_group", candidate_path
                ),
                "action_family": _required(
                    source_candidate, "action_family", candidate_path
                ),
                "required_plane": _required(
                    source_candidate, "required_plane", candidate_path
                ),
                "availability": _required(
                    source_candidate, "availability", candidate_path
                ),
                "hard_gate": {
                    "ability_allowed": _required(
                        gates, "ability_allowed", f"{candidate_path}.hard_gates"
                    ),
                    "group_object_count": _required(
                        gates,
                        "group_object_count",
                        f"{candidate_path}.hard_gates",
                    ),
                    "target_exists": _required(
                        gates, "feasible_target", f"{candidate_path}.hard_gates"
                    ),
                    "geometry_height_valid": _required(
                        gates,
                        "geometry_height_valid",
                        f"{candidate_path}.hard_gates",
                    ),
                    "blocked": _required(
                        gates, "blocked", f"{candidate_path}.hard_gates"
                    ),
                    "blocked_reason": _required(
                        gates, "blocked_reason", f"{candidate_path}.hard_gates"
                    ),
                    "score_must_be_zero": _required(
                        gates,
                        "score_must_be_zero",
                        f"{candidate_path}.hard_gates",
                    ),
                },
                "target_reference": target_reference,
            }
        )

    score_records = _list(
        _required(recent, "records", "recent_performance"),
        "recent_performance.records",
    )
    scores = [
        _required(
            _mapping(record, f"recent_performance.records[{index}]"),
            "average_score",
            f"recent_performance.records[{index}]",
        )
        for index, record in enumerate(score_records)
    ]
    raw_text = _required(patient_condition, "raw_text", "patient_condition")
    if not isinstance(raw_text, str) or not raw_text:
        raise CompactAdapterError("patient_condition.raw_text: invalid")
    compact_raw_text = EVIDENCE_REDACTION if redact_patient else raw_text

    return {
        "schema_version": COMPACT_SCHEMA,
        "source_schema_version": SOURCE_SCHEMA,
        "decision_binding": {
            "decision_id": _required(binding, "decision_id", "decision_binding"),
            "generation": _required(binding, "generation", "decision_binding"),
            "scan_id": _required(
                binding, "source_scan_id", "decision_binding"
            ),
            "scene_sha256": _required(
                binding, "source_scene_sha256", "decision_binding"
            ),
        },
        "authority": {
            "machine_facts": SOURCE_SCHEMA,
            "business_scoring": "DeepSeek",
            "deterministic_preparation": PREPARATION_SCHEMA,
            "output_contract": OUTPUT_SCHEMA,
            "validation_input": SOURCE_SCHEMA,
        },
        "patient": {
            "ability": {
                "level": _required(patient_ability, "level", "patient_ability"),
                "parse_status": _required(
                    patient_ability, "parse_status", "patient_ability"
                ),
            },
            "condition": {
                "raw_text": compact_raw_text,
                "status": _required(
                    patient_condition, "status", "patient_condition"
                ),
                "truncated": _required(
                    patient_condition, "truncated", "patient_condition"
                ),
            },
            "medical_advice": {
                "source": "patient.condition.raw_text",
                "interpretation": "candidate_scoped_free_text_semantics",
            },
        },
        "history": {
            "recent_tendency": _required(
                recent, "tendency_level", "recent_performance"
            ),
            "score_history_summary": {
                "status": _required(recent, "status", "recent_performance"),
                "window_limit": _required(
                    recent, "window_limit", "recent_performance"
                ),
                "record_count": _required(
                    recent, "record_count", "recent_performance"
                ),
                "scores": scores,
                "average_score": fmean(scores) if scores else None,
            },
            "frequency_counts": {
                "status": _required(frequency, "status", "item_frequency"),
                "window_limit": _required(
                    frequency, "window_limit", "item_frequency"
                ),
                "mapped_record_count": _required(
                    frequency, "mapped_record_count", "item_frequency"
                ),
                "cup": _required(frequency, "cup_count", "item_frequency"),
                "ball": _required(frequency, "ball_count", "item_frequency"),
            },
        },
        "candidates": candidates,
        "targets": targets,
        "latest_compensation": {
            "status": _required(compensation, "status", "latest_compensation"),
            "latest_task": _required(
                compensation, "task_name", "latest_compensation"
            ),
            "compensation_action_count": _required(
                compensation,
                "compensation_action_count",
                "latest_compensation",
            ),
            "exact_task_id": _required(
                compensation, "exact_task_id", "latest_compensation"
            ),
            "action_family": _required(
                compensation, "action_family", "latest_compensation"
            ),
            "mapped_group": _required(
                compensation, "mapped_group", "latest_compensation"
            ),
            "severity": _required(
                compensation, "severity", "latest_compensation"
            ),
        },
        "decision_preparation": prepare_formal_input(source),
    }
