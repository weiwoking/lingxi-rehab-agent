from pathlib import Path

from deltar_p4_pipeline.core_class_contract import CORE_NOW, classify_class, core_contract
from deltar_p4_pipeline.stage5_2b_validation import core_performance


ROOT = Path(__file__).resolve().parents[1]


def test_core_class_categories():
    assert classify_class("cup") == "core_now"
    assert classify_class("remote") == "supported_but_not_core"
    assert classify_class("towel") == "future_custom_class"
    assert classify_class("airplane") == "not_needed"


def test_person_role_is_context_not_manipulable():
    value = core_contract()
    person = next(item for item in value["core_now"] if item["class_name"] == "person")
    assert "not a manipulable" in person["role"]
    assert value["training_performed"] is False


def test_core_set_is_frozen():
    assert CORE_NOW == ("person", "chair", "dining table", "cup", "bottle", "book", "tv")


def test_sample_sufficiency_marks_one_to_four_as_insufficient():
    value = core_performance(ROOT)
    bottle = next(item for item in value["classes"] if item["class_name"] == "bottle")
    cup = next(item for item in value["classes"] if item["class_name"] == "cup")
    assert bottle["count"] == 5 and bottle["sample_sufficiency"]["stable_conclusion_allowed"] is True
    assert cup["count"] == 1 and cup["sample_sufficiency"]["label"] == "insufficient_1_to_4"
