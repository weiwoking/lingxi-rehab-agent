from pathlib import Path

from deltar_p4_pipeline.stage5_2b_validation import distance_analysis, optimization_decision, stage6_contract


ROOT = Path(__file__).resolve().parents[1]


def test_distance_analysis_is_exhaustive_and_has_all_bands():
    value = distance_analysis(ROOT)
    assert len(value["bands"]) == 11
    assert sum(item["object_count"] for item in value["bands"]) == 768
    assert all("sample_sufficiency" in item for item in value["bands"])


def test_optimization_decision_uses_frozen_gates():
    value = optimization_decision(ROOT)
    assert value["algorithm_optimization_needed"] is False
    assert all(value["checks"].values())


def test_stage6_entry_contract_is_authorized_and_scoped():
    value = stage6_contract()
    assert value["authorized"] is True
    assert value["primary_chain"] == "new_unmodified_DELTAR"
    assert "PC batch processing" in value["stage6_scope_only"]
    assert "P4" in value["stage6_excludes"]
