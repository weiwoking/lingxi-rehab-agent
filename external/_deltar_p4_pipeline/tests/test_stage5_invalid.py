import pytest

from deltar_p4_pipeline.errors import PipelineError
from deltar_p4_pipeline.stage5_contracts import validate_tof_payload


def test_bad_tof_length_rejected():
    with pytest.raises(PipelineError):validate_tof_payload({"schema_version":"rehab_tof_raw.v1","distance_mm":[],"range_sigma_mm":[],"target_status":[],"nb_target_detected":[]})
