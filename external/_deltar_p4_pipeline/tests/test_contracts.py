import copy

import pytest

from deltar_p4_pipeline.contracts import validate_profile, validate_tof
from deltar_p4_pipeline.errors import PipelineError
from tests.helpers import valid_profile, valid_tof


def test_valid_contracts() -> None:
    assert validate_tof(valid_tof())["distance_mm"][0] == 2000.0
    assert validate_profile(valid_profile())["canvas"]["strategy"] == "letterbox_640x480"


@pytest.mark.parametrize("field", ["sequence", "timestamp_us", "distance_mm", "range_sigma_mm", "target_status", "nb_target_detected"])
def test_missing_tof_fields(field: str) -> None:
    value = valid_tof()
    del value[field]
    with pytest.raises(PipelineError) as caught:
        validate_tof(value)
    assert caught.value.code == "E_FIELD_MISSING"


def test_bool_is_not_accepted_as_integer() -> None:
    value = valid_tof()
    value["sequence"] = True
    with pytest.raises(PipelineError) as caught:
        validate_tof(value)
    assert caught.value.code == "E_TYPE"


def test_sigma_is_standard_deviation_and_not_squared() -> None:
    checked = validate_tof(valid_tof())
    assert checked["range_sigma_mm"] == [40.0] * 64


def test_out_of_bounds_fr_is_allowed() -> None:
    fr = [[-10, -20, 500, 700] for _ in range(64)]
    checked = validate_profile(valid_profile(fr={"strategy": "explicit_model_canvas", "values": fr}))
    assert checked["fr"]["values"][0] == [-10.0, -20.0, 500.0, 700.0]
