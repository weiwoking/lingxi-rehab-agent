#!/usr/bin/env python3
"""Privacy-safe recursive replay for deterministic decision preparation."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))

from llm_compact_reference import adapt_formal_input  # noqa: E402


TASK_REF_BY_ID = {
    "OT_CUP_TRANSFER_L1": 0,
    "OT_CUP_DRINK_L2": 1,
    "OT_CUP_DRINK_L3": 2,
    "OT_BALL_TRANSFER_L1": 3,
    "OT_BALL_TRANSFER_L2": 4,
}
SCHEMA_PATH = (
    TEST_DIR
    / "fixtures/llm_compact/rehab_task_decision_llm_input.v1.schema.json"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def binding_id(document: dict[str, Any]) -> str | None:
    binding = document.get("decision_binding")
    if isinstance(binding, dict):
        value = binding.get("decision_id")
        return value if isinstance(value, str) else None
    value = document.get("decision_id")
    return value if isinstance(value, str) else None


def round_score(value: float) -> float:
    return math.floor(value * 1000.0 + 0.5) / 1000.0


def canonical_bytes(document: dict[str, Any]) -> int:
    return len(
        json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    )


def narration_marker_counts(content: Any) -> dict[str, int]:
    text = content if isinstance(content, str) else ""
    markers = {
        "formula": r"formula|公式",
        "lookup": r"lookup|查表|映射表",
        "rounding": r"round(?:ing)?|四舍五入|小数",
        "component_recalculation": (
            r"difficulty.*(?:compensation|frequency|perception)|"
            r"(?:难度|补偿|频率|感知).*(?:相加|求和|计算)"
        ),
    }
    return {
        name: len(re.findall(pattern, text, flags=re.IGNORECASE | re.DOTALL))
        for name, pattern in markers.items()
    }


def verify_preparation(compact: dict[str, Any]) -> dict[str, Any]:
    candidates = compact["candidates"]
    targets = {target["target_ref"]: target for target in compact["targets"]}
    prepared = compact["decision_preparation"]["candidates"]
    assert compact["decision_preparation"]["advice_policy"] == {
        "range": [16, 25],
        "positive_range": [21, 25],
        "negative_range": [16, 19],
        "neutral": 20,
    }
    assert compact["decision_preparation"]["tie_break_candidate_refs"] == [
        0, 1, 2, 3, 4
    ]
    assert "scoring_policy" not in compact
    refs = [candidate["candidate_ref"] for candidate in candidates]
    assert refs == [0, 1, 2, 3, 4]
    assert [entry["candidate_ref"] for entry in prepared] == refs
    blocked_refs: list[int] = []
    available_refs: list[int] = []
    for candidate, entry in zip(candidates, prepared, strict=True):
        ref = candidate["candidate_ref"]
        assert candidate["availability"] == entry["availability"]
        if candidate["availability"] == "blocked":
            blocked_refs.append(ref)
            assert entry["fixed_components"] is None
            assert "advice_score_lookup" not in entry
            continue
        available_refs.append(ref)
        assert candidate["target_reference"] in targets
        fixed = entry["fixed_components"]
        assert fixed is not None
        expected_total = sum(
            fixed[name]
            for name in ("difficulty", "compensation", "frequency", "perception")
        )
        assert math.isclose(fixed["fixed_total"], expected_total, abs_tol=1e-9)
        assert "advice_score_lookup" not in entry
    return {
        "candidate_refs": refs,
        "available_refs": available_refs,
        "blocked_refs": blocked_refs,
        "target_count": len(targets),
        "target_links_valid": True,
        "score_semantics_valid": True,
        "lookup_absent": True,
    }


def verify_selected_output(
    compact: dict[str, Any], selected: dict[str, Any]
) -> dict[str, Any]:
    def numeric(value: Any) -> float:
        if isinstance(value, dict):
            value = value.get("value")
        assert isinstance(value, (int, float)) and math.isfinite(value)
        return float(value)

    prepared = compact["decision_preparation"]["candidates"]
    output_candidates = selected.get("candidates")
    assert isinstance(output_candidates, list) and len(output_candidates) == 5
    refs = [candidate.get("candidate_ref") for candidate in output_candidates]
    assert refs == [0, 1, 2, 3, 4]
    available_refs: list[int] = []
    scores: dict[int, float] = {}
    for entry, output in zip(prepared, output_candidates, strict=True):
        ref = entry["candidate_ref"]
        availability = output.get("availability")
        if entry["availability"] == "blocked":
            assert availability == "blocked"
            assert output.get("score_breakdown") is None
            assert output.get("score") == 0
            continue
        if availability == "blocked":
            assert output.get("score_breakdown") is None
            assert output.get("score") == 0
            continue
        assert availability == "available"
        breakdown = output.get("score_breakdown")
        assert isinstance(breakdown, dict)
        fixed = entry["fixed_components"]
        assert fixed is not None
        for name in ("difficulty", "compensation", "frequency", "perception"):
            assert math.isclose(
                numeric(breakdown[name]), float(fixed[name]), abs_tol=5e-4
            )
        advice_value = numeric(breakdown.get("advice"))
        assert advice_value.is_integer() and 16 <= advice_value <= 25
        advice = int(advice_value)
        expected_raw = float(fixed["fixed_total"]) + advice
        expected_score = round_score(min(max(expected_raw, 0.0), 100.0))
        assert math.isclose(
            numeric(breakdown["final_raw"]), expected_raw, abs_tol=5e-4
        )
        assert math.isclose(float(output["score"]), expected_score, abs_tol=5e-4)
        available_refs.append(ref)
        scores[ref] = expected_score
    winner_ref = selected.get("winner_ref")
    assert winner_ref in available_refs
    return {
        "winner_ref": winner_ref,
        "winner_available": True,
        "candidate_refs_preserved": True,
        "score_semantics_valid": True,
        "available_refs": available_refs,
        "scores": scores,
    }


def replay(input_root: Path) -> dict[str, Any]:
    schema = load(SCHEMA_PATH)
    validator = Draft202012Validator(schema)
    formal_paths = sorted(input_root.rglob("latest_formal_decision_input*.json"))
    compact_paths = sorted(input_root.rglob("latest_decision_llm_input*.json"))
    reasoning_paths = sorted(input_root.rglob("latest_decision_reasoning*.json"))
    selected_paths = sorted(input_root.rglob("selected_task_details*.json"))
    reasonings: dict[str, list[dict[str, Any]]] = {}
    reasoning_inventory: list[dict[str, Any]] = []
    for path in reasoning_paths:
        document = load(path)
        decision_id = binding_id(document)
        winner_ref = TASK_REF_BY_ID.get(document.get("task_id"))
        entry = {
            "file": str(path.relative_to(input_root)),
            "sha256": digest(path),
            "schema_version": document.get("schema_version"),
            "decision_id": decision_id,
            "winner_ref": winner_ref,
            "winner_task_id": document.get("task_id"),
            "latency_sec": document.get("latency_sec"),
            "reasoning_tokens": document.get("reasoning_tokens"),
            "reasoning_bytes": document.get("reasoning_bytes"),
            "narration_marker_counts": narration_marker_counts(
                document.get("reasoning_content")
            ),
        }
        reasoning_inventory.append(entry)
        if decision_id is not None:
            reasonings.setdefault(decision_id, []).append(entry)

    compact_inventory: list[dict[str, Any]] = []
    compacts_by_decision: dict[str, list[dict[str, Any]]] = {}
    for path in compact_paths:
        document = load(path)
        validator.validate(document)
        entry = {
            "file": str(path.relative_to(input_root)),
            "sha256": digest(path),
            "schema_version": document.get("schema_version"),
            "decision_id": binding_id(document),
            "canonical_bytes": canonical_bytes(document),
            "candidate_refs": [
                candidate.get("candidate_ref")
                for candidate in document.get("candidates", [])
            ],
            "blocked_refs": [
                candidate.get("candidate_ref")
                for candidate in document.get("candidates", [])
                if candidate.get("availability") == "blocked"
            ],
            "has_decision_preparation": "decision_preparation" in document,
            "preparation_schema": (
                document.get("decision_preparation", {}).get("schema_version")
                if isinstance(document.get("decision_preparation"), dict)
                else None
            ),
            "schema_valid": True,
        }
        compact_inventory.append(entry)
        decision_id = entry["decision_id"]
        if isinstance(decision_id, str):
            compacts_by_decision.setdefault(decision_id, []).append(entry)

    selected_by_decision: dict[str, list[dict[str, Any]]] = {}
    selected_inventory: list[dict[str, Any]] = []
    for path in selected_paths:
        document = load(path)
        decision_id = binding_id(document)
        entry = {
            "file": str(path.relative_to(input_root)),
            "sha256": digest(path),
            "schema_version": document.get("schema_version"),
            "decision_id": decision_id,
            "winner_ref": document.get("winner_ref"),
            "candidate_refs": [
                candidate.get("candidate_ref")
                for candidate in document.get("candidates", [])
                if isinstance(candidate, dict)
            ],
        }
        selected_inventory.append(entry)
        if isinstance(decision_id, str):
            selected_by_decision.setdefault(decision_id, []).append(
                {"metadata": entry, "document": document}
            )

    formal_replays: list[dict[str, Any]] = []
    for path in formal_paths:
        formal = load(path)
        compact = adapt_formal_input(formal)
        validator.validate(compact)
        semantics = verify_preparation(compact)
        decision_id = binding_id(formal)
        matched = reasonings.get(decision_id or "", [])
        matched_compacts = compacts_by_decision.get(decision_id or "", [])
        payload_comparisons = []
        generated_bytes = canonical_bytes(compact)
        for old in matched_compacts:
            if old["preparation_schema"] != "rehab_task_decision_preparation.v1":
                continue
            old_bytes = int(old["canonical_bytes"])
            assert generated_bytes < old_bytes
            payload_comparisons.append({
                "before_file": old["file"],
                "before_preparation_schema": old["preparation_schema"],
                "before_bytes": old_bytes,
                "after_preparation_schema":
                    compact["decision_preparation"]["schema_version"],
                "after_bytes": generated_bytes,
                "reduction_bytes": old_bytes - generated_bytes,
                "reduction_percent": round(
                    (old_bytes - generated_bytes) * 100.0 / old_bytes, 2
                ),
            })
        winner_checks = []
        for reasoning in matched:
            winner_ref = reasoning["winner_ref"]
            winner_available = winner_ref in semantics["available_refs"]
            assert winner_ref is not None and winner_available
            winner_checks.append({
                "winner_ref": winner_ref,
                "winner_task_id": reasoning["winner_task_id"],
                "winner_available": winner_available,
            })
        selected_checks = []
        for selected_entry in selected_by_decision.get(decision_id or "", []):
            check = verify_selected_output(compact, selected_entry["document"])
            selected_checks.append({
                "file": selected_entry["metadata"]["file"],
                **check,
            })
        formal_replays.append({
            "file": str(path.relative_to(input_root)),
            "sha256": digest(path),
            "schema_version": formal.get("schema_version"),
            "decision_id": decision_id,
            "generated_compact_schema": compact["schema_version"],
            "generated_preparation_schema":
                compact["decision_preparation"]["schema_version"],
            "generated_compact_bytes": generated_bytes,
            **semantics,
            "matched_winners": winner_checks,
            "matched_selected_outputs": selected_checks,
            "payload_comparisons": payload_comparisons,
            "schema_valid": True,
        })

    assert formal_replays, "no formal decision inputs found"
    return {
        "schema_version": "rehab_decision_preparation_replay.v1.1",
        "input_root": str(input_root),
        "privacy": "No patient raw text or reasoning_content is emitted; only counts and hashes are retained.",
        "formal_replay_count": len(formal_replays),
        "existing_compact_count": len(compact_inventory),
        "reasoning_artifact_count": len(reasoning_inventory),
        "selected_artifact_count": len(selected_inventory),
        "formal_replays": formal_replays,
        "existing_compact_validation": compact_inventory,
        "reasoning_inventory": reasoning_inventory,
        "selected_inventory": selected_inventory,
        "overall": "PASS",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay(args.input_root.resolve())
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
