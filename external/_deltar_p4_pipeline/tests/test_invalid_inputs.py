import json
from pathlib import Path

import pytest

from deltar_p4_pipeline.contracts import load_json, validate_profile, validate_tof
from deltar_p4_pipeline.errors import PipelineError
from tests.helpers import valid_profile, valid_tof


@pytest.mark.parametrize("field", ["distance_mm", "range_sigma_mm", "target_status", "nb_target_detected"])
def test_non_64_arrays_rejected(field: str) -> None:
    value = valid_tof()
    value[field] = value[field][:-1]
    with pytest.raises(PipelineError) as caught:
        validate_tof(value)
    assert caught.value.code == "E_ARRAY_LENGTH"


def test_negative_sigma_rejected() -> None:
    value = valid_tof()
    value["range_sigma_mm"][3] = -1
    with pytest.raises(PipelineError) as caught:
        validate_tof(value)
    assert caught.value.code == "E_NEGATIVE_SIGMA"


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_json_rejected(tmp_path: Path, literal: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"x": ' + literal + "}", encoding="utf-8")
    with pytest.raises(PipelineError) as caught:
        load_json(path)
    assert caught.value.code == "E_JSON_PARSE"


def test_string_number_rejected() -> None:
    value = valid_tof()
    value["distance_mm"][0] = "2000"
    with pytest.raises(PipelineError) as caught:
        validate_tof(value)
    assert caught.value.code == "E_TYPE"


def test_status_range_rejected() -> None:
    value = valid_tof()
    value["target_status"][0] = 256
    with pytest.raises(PipelineError) as caught:
        validate_tof(value)
    assert caught.value.code == "E_STATUS_RANGE"


def test_explicit_mask_requires_bool64() -> None:
    profile = valid_profile(mask={"strategy": "explicit_bool64", "values": [1] * 64})
    with pytest.raises(PipelineError) as caught:
        validate_profile(profile)
    assert caught.value.code == "E_TYPE"


@pytest.mark.parametrize("row", [[0, 0, 0, 1], [0, 2, 1, 2], [1, 0, 0, 1]])
def test_empty_or_reversed_fr_rejected(row: list[int]) -> None:
    profile = valid_profile(fr={"strategy": "explicit_model_canvas", "values": [row] * 64})
    with pytest.raises(PipelineError) as caught:
        validate_profile(profile)
    assert caught.value.code == "E_FR_INVALID"


def test_unsupported_profile_rejected() -> None:
    profile = valid_profile(canvas={"strategy": "guess", "fill_rgb": [0, 0, 0]})
    with pytest.raises(PipelineError) as caught:
        validate_profile(profile)
    assert caught.value.code == "E_PROFILE_UNSUPPORTED"
