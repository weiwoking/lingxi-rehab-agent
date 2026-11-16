import copy

import pytest

from app.config import _normalize_person_follow, get_config


def test_current_person_follow_configuration_is_frozen():
    config = get_config()["person_follow"]
    assert config["enabled"] is True
    assert config["logical_model_slot"] == "person_follow"
    assert config["target_class"] == "person"
    assert config["confidence_threshold"] == pytest.approx(0.35)
    assert config["image_size"] == 640
    assert config["device"] == "cpu"
    assert config["half"] is False
    assert config["max_image_bytes"] == 131072
    assert config["warmup"] is True


def test_old_configuration_without_person_follow_remains_readable_and_disabled():
    config = {"model": {"path": "models/yolo11n.pt"}}
    _normalize_person_follow(config)
    assert config["person_follow"]["enabled"] is False
    assert config["person_follow"]["target_class"] == "person"


@pytest.mark.parametrize(
    "override",
    [
        {"half": True, "device": "cpu"},
        {"confidence_threshold": 1.1},
        {"target_class": "bad class"},
        {"max_image_bytes": 0},
        {"warmup": "yes"},
    ],
)
def test_invalid_person_follow_configuration_blocks_readiness(override):
    config = {
        "model": {"path": "models/yolo11n.pt"},
        "person_follow": {"enabled": True, **copy.deepcopy(override)},
    }
    with pytest.raises(ValueError):
        _normalize_person_follow(config)
