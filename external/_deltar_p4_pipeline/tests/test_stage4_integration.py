import json
from pathlib import Path

from scripts.build_stage4_inputs import build
from scripts.run_stage4_official_matrix import run as run_official
from scripts.run_stage4_sensitivity import run as run_sensitivity

ROOT = Path(__file__).resolve().parents[1]


def test_integration_reads_real_stage31_outputs_not_mock():
    manifest = build()
    first = manifest["object_groups"][0]
    assert (ROOT / first["provenance"]["stage3_1_result"]).is_file()
    assert (ROOT / first["source_depth_refs"]["direct_640x360"]).is_file()


def test_official_internal_consistency_matrix():
    result = run_official()
    assert result["object_count"] == 61
    assert result["success_count"] + result["rejected_count"] == 61
    assert result["official_absolute_geometry_not_evaluated"]


def test_sensitivity_all_required_families():
    result = run_sensitivity()
    assert result["case_count"] == 40
    assert set(result["summary"]) == {"depth", "intrinsics", "camera_ground"}
    assert result["camera_tof"]["status"] == "upstream_alignment_sensitivity_not_fully_evaluated"


def test_depth_semantics_audit_freezes_nonproven_status():
    audit = json.loads((ROOT / "outputs_stage4" / "depth_semantics_audit.json").read_text())
    assert audit["depth_semantics"] == "axial_z"
    assert audit["depth_semantics_status"] == "PLAUSIBLE_BUT_UNPROVEN"
    assert audit["engine_supported_semantics"] == ["axial_z", "radial_range"]
