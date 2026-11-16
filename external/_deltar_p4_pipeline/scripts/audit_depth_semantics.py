from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs_stage4" / "depth_semantics_audit.json"


def build() -> dict:
    evidence = [
        {
            "id": "E1_nyu_hdf5_source",
            "source": "_upstream/deltar_route_probe/src/dataloader/nyu.py:85-90,109-129",
            "finding": "HDF5 depth is loaded without geometric conversion and is both dense supervision and the source used to synthesize zone histograms.",
            "strength": "direct_code_evidence",
        },
        {
            "id": "E2_hist_mu",
            "source": "_upstream/deltar_route_probe/src/utils/dataloader.py:83-129",
            "finding": "NYU synthetic hist_data mu is the weighted mean of bins made directly from the dense depth values; no ray/Z conversion exists.",
            "strength": "direct_code_evidence",
        },
        {
            "id": "E3_supervision",
            "source": "_upstream/deltar_route_probe/train.py:100-117; evaluate.py:51-100",
            "finding": "Prediction is supervised and evaluated directly against the dataset depth tensor.",
            "strength": "direct_code_evidence",
        },
        {
            "id": "E4_paper_tof",
            "source": "Li et al., DELTAR, ECCV 2022, arXiv:2209.13362, paper p.5 section 3.1",
            "url": "https://arxiv.org/abs/2209.13362",
            "finding": "The paper explicitly describes conventional ToF output as distance along the ray from the optical center; L5 produces zone depth distributions from time-of-flight ranges.",
            "strength": "primary_paper_explicit_for_tof_input",
        },
        {
            "id": "E5_paper_target",
            "source": "Li et al., DELTAR, paper pp.5 and 10",
            "finding": "RealSense depth is the real-data ground truth and NYU depth maps generate synthetic L5 distributions, but the paper does not explicitly label the network output axial Z versus radial range.",
            "strength": "primary_paper_boundary",
        },
        {
            "id": "E6_calib",
            "source": "_upstream/Calib_DELTAR_route_probe/optimize_plane.py:95-107,190-193",
            "finding": "The calibration path calls generate_pcd(use_tof_as_z=True): it divides raw zone depth by ray.z to obtain ray length and then reconstructs a point whose z equals the raw value. It therefore treats the calibration input as axial Z, not radial range.",
            "strength": "direct_code_evidence_conflicting_with_paper_sensor_description",
        },
        {
            "id": "E7_demo_generator_gap",
            "source": "_deltar_contract/specs/UNRESOLVED_ITEMS.md:7",
            "finding": "The final official demo HDF5 generator mapping raw ULD fields to hist_data was not published; equality to raw distance/sigma divided by 1000 remains unresolved.",
            "strength": "explicit_known_gap",
        },
    ]
    payload = {
        "schema_version": "deltar_stage4_depth_semantics_audit.v1",
        "status": "complete",
        "depth_semantics_status": "PLAUSIBLE_BUT_UNPROVEN",
        "depth_semantics": "axial_z",
        "engine_supported_semantics": ["axial_z", "radial_range"],
        "silent_default_allowed": False,
        "answers": {
            "hdf5_depth_origin": "NYU path: dataset HDF5 depth; ZJU/demo path: RealSense-derived dense depth in released HDF5. Final generator is unavailable.",
            "training_and_evaluation_definition": "Numerically identical to the loaded dataset depth map; geometric axial/radial label is not asserted in code.",
            "official_code_treats_output_as_camera_z": "No explicit backprojection or Camera-Z declaration exists. Code only treats it as dense metric depth.",
            "calib_radial_to_camera_z_conversion": "No active radial-to-Z conversion. The active use_tof_as_z=True path performs the opposite construction: raw is Z and ray length is raw/ray.z.",
            "paper_or_public_code_explicit": "Explicit for ToF input as along-ray range; not explicit for final dense output. Calibration code supplies contradictory practical evidence.",
            "hist_data_mu": "NYU synthetic path: mean of the source depth-map histogram. Released real/demo path: fitted zone mean in meters, but raw-field generation remains unresolved.",
            "output_and_tof_same_semantics": "Not proven and must not be assumed. The evidence supports potentially different input and output conventions.",
        },
        "decision_rationale": "Axial Z is a strong engineering inference from dense supervision conventions and Calib_DELTAR's active path, but the paper's explicit along-ray ToF description plus the missing HDF5 generator prevents PROVEN status.",
        "evidence": evidence,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
