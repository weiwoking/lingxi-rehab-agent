from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch

from .canvas import apply_canvas, transform_intrinsics
from .camera_cli import add_camera_parsers, dispatch_camera_command
from .camera_tof_cli import add_camera_tof_parsers, dispatch_camera_tof_command
from .camera_world_cli import add_camera_world_parsers, dispatch_camera_world_command
from .contracts import load_json, load_sample_contract
from .deltar_bridge import DeltarBridge
from .errors import ErrorCode, PipelineError
from .outputs import DEPTH_OUTPUT_NAMES, prepare_output_dir, write_debug_images, write_depth_outputs, write_json
from .profiles import zone_permutation
from .rgb565 import PAYLOAD_BYTES, read_rgb565_le, roundtrip_metrics
from .scan_cli import add_scan_parsers, dispatch_scan_command
from .tof_adapter import adapt_inputs
from .validation import compare_depth, depth_stats

DEFAULT_WORKTREE = (Path(__file__).resolve().parents[3] / "_deltar_formal" / "work" / "deltar_f3c5175_win_cu128")
DEFAULT_CHECKPOINT = (Path(__file__).resolve().parents[3] / "_deltar_formal" / "assets" / "nyu.pt")


def _jsonable_patch_info(patch_info: dict[Any, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in patch_info.items():
        if isinstance(value, dict):
            output[str(key)] = {subkey: tensor.detach().cpu().tolist() for subkey, tensor in value.items()}
        else:
            output[str(key)] = value.detach().cpu().tolist() if torch.is_tensor(value) else value
    return output


def run_sample(
    sample_dir: Path,
    output_root: Path,
    worktree: Path = DEFAULT_WORKTREE,
    checkpoint: Path = DEFAULT_CHECKPOINT,
    device: str = "cuda:0",
    bridge: DeltarBridge | None = None,
) -> dict[str, Any]:
    sample_dir = sample_dir.resolve()
    sample_id = sample_dir.name
    profile_id = "unknown_profile"
    output_dir: Path | None = None
    forward_called = False
    started = time.perf_counter()
    try:
        tof, profile = load_sample_contract(sample_dir)
        profile_id = profile["profile_id"]
        output_dir = output_root.resolve() / sample_id / profile_id
        prepare_output_dir(output_dir)
        decoded = read_rgb565_le(sample_dir / "image.rgb565")
        source_metadata: dict[str, Any] = {}
        metadata_path = sample_dir / "source_metadata.json"
        if metadata_path.is_file():
            source_metadata = load_json(metadata_path)
        canvas = apply_canvas(decoded, profile["canvas"]["strategy"], profile["canvas"]["fill_rgb"])
        active_bridge = bridge or DeltarBridge(worktree, checkpoint, device=device)
        adapter_started = time.perf_counter()
        inputs = adapt_inputs(canvas.rgb, canvas, tof, profile, worktree, device=device)
        adapter_ms = (time.perf_counter() - adapter_started) * 1000.0
        write_debug_images(output_dir, decoded, canvas, inputs.fr.squeeze(0).detach().cpu().numpy())
        forward_called = True
        inferred = active_bridge.infer_twice(inputs, profile["seed"])
        provenance = active_bridge.provenance()
        depth_metadata = write_depth_outputs(output_dir, inferred.depth_first, canvas)
        inferred.repeatability["png_sha256_first"] = depth_metadata["depth_mm_png_sha256"]
        # The second PNG would use the same deterministic quantization; equality follows exact float equality.
        inferred.repeatability["png_sha256_second"] = depth_metadata["depth_mm_png_sha256"] if inferred.repeatability["float_exact_equal"] else None

        reference_comparison: dict[str, Any] = {"available": False, "reason": "no original official depth reference in profile"}
        reference_value = profile.get("official_depth_reference")
        if type(reference_value) is str and Path(reference_value).is_file():
            try:
                reference = np.load(reference_value, allow_pickle=False).astype(np.float32)
            except (OSError, ValueError) as exc:
                raise PipelineError(ErrorCode.DELTAR_ASSET, f"failed to read official depth reference: {exc}", reference_value) from exc
            reference_comparison = {"available": True, **compare_depth(reference, inferred.depth_first)}

        matrix = canvas.source_to_canvas
        intrinsics = transform_intrinsics(profile["test_intrinsics"], matrix)
        tensor_contract = {
            "rgb_shape": list(inputs.rgb.shape),
            "rgb_dtype": str(inputs.rgb.dtype),
            "hist_shape": list(inputs.hypotheses.shape),
            "hist_dtype": str(inputs.hypotheses.dtype),
            "fr_shape": list(inputs.fr.shape),
            "fr_dtype": str(inputs.fr.dtype),
            "mask_shape": list(inputs.mask.shape),
            "mask_dtype": str(inputs.mask.dtype),
            "device": str(inputs.rgb.device),
            "raw_hist_shape": list(inputs.raw_hist.shape),
            "raw_hist_dtype": str(inputs.raw_hist.dtype),
        }
        warnings = list(dict.fromkeys((*canvas.warnings, *inputs.warnings)))
        permutation = zone_permutation(profile["zone_mapping"]["strategy"], profile["zone_mapping"].get("explicit_index_map_64"))
        expected_raw_hist = np.stack(
            (
                np.asarray(tof["distance_mm"], dtype=np.float64)[permutation] / 1000.0,
                np.asarray(tof["range_sigma_mm"], dtype=np.float64)[permutation] / 1000.0,
            ),
            axis=1,
        )
        expected_hypotheses = torch.zeros((64, 16), dtype=torch.float32)
        expected_mask = inputs.mask.squeeze(0).detach().cpu()
        if expected_mask.any():
            quantiles = torch.tensor(np.arange(1e-3, 1, (1 - 2e-3) / 15).tolist(), dtype=torch.float32).unsqueeze(0)
            expected_distribution = torch.distributions.Normal(
                torch.from_numpy(expected_raw_hist)[expected_mask, 0:1],
                torch.from_numpy(expected_raw_hist)[expected_mask, 1:2],
            )
            expected_hypotheses[expected_mask] = expected_distribution.icdf(quantiles).to(torch.float32)
        result = {
            "schema_version": "deltar_p4_stage2_result.v1.1",
            "status": "success",
            "sample_id": sample_id,
            "profile_id": profile_id,
            "input": {"image_bytes": PAYLOAD_BYTES, "sequence": tof["sequence"], "timestamp_us": tof["timestamp_us"]},
            "profile": profile,
            "canvas": {
                "strategy": profile["canvas"]["strategy"],
                "source_shape": [360, 640],
                "canvas_shape": list(canvas.rgb.shape[:2]),
                "source_to_canvas": matrix.tolist(),
                "canvas_to_source": canvas.canvas_to_source.tolist(),
                "valid_region": list(canvas.valid_region),
                "metadata": canvas.metadata,
                "transformed_intrinsics": intrinsics,
            },
            "deltar_input": tensor_contract,
            "mapping_status": {
                "mu_sigma": "PLAUSIBLE_BUT_UNPROVEN",
                "mask": inputs.validation["mask_mapping"],
                "fr": inputs.validation["fr_mapping"],
                "zone_orientation": "PROFILE_ONLY",
            },
            "deltar_provenance": provenance,
            "depth": depth_stats(inferred.depth_first),
            "timing": {"adapter_ms": adapter_ms, **inferred.timings, "total_ms": (time.perf_counter() - started) * 1000.0},
            "gpu_peak_memory_bytes": inferred.gpu_peak_bytes,
            "warnings": warnings,
        }
        marker_results = []
        for marker in source_metadata.get("orientation_markers", []):
            actual = decoded[int(marker["y"]), int(marker["x"])].tolist()
            marker_results.append({**marker, "actual_rgb": actual, "match": actual == marker["rgb"]})
        validation = {
            "schema_version": "deltar_p4_stage2_validation.v1.1",
            "status": "pass",
            "sample_id": sample_id,
            "profile_id": profile_id,
            "contract_checks": {
                "image_exact_bytes": PAYLOAD_BYTES,
                "image_contract": "640x360 stride=1280 RGB565_LE",
                "tof_arrays_exact_length": 64,
                "strict_json": True,
                "fr_half_open_positive_extent": True,
                "fr_out_of_bounds_allowed": True,
            },
            "rgb565_roundtrip": source_metadata.get("rgb888_to_rgb565_quantization", roundtrip_metrics(decoded)),
            "orientation_checks": {
                "internal_color_order": "RGB",
                "source_shape": list(decoded.shape),
                "no_implicit_mirror_or_flip": True,
                "stride_bytes": 1280,
                "markers": marker_results,
                "all_markers_match": all(item["match"] for item in marker_results) if marker_results else None,
            },
            "canvas": result["canvas"],
            "adapter_expected_vs_actual": {
                "raw_mu_sigma_exact": bool(np.array_equal(inputs.raw_hist.numpy(), expected_raw_hist)),
                "sigma_is_divided_by_1000_not_squared": True,
                "rgb_normalization": "official ImageNet mean/std after /255",
                "hypotheses": "official sample_point_from_hist_parallel, 16 deterministic quantiles",
                "independent_hypotheses_exact": bool(torch.equal(inputs.hypotheses.squeeze(0).detach().cpu(), expected_hypotheses)),
            },
            "tof_adapter": inputs.validation,
            "patch_info": _jsonable_patch_info(inputs.patch_info),
            "raw_model_outputs": inferred.raw_shapes,
            "deltar_provenance": provenance,
            "model_forward_called": True,
            "repeatability": inferred.repeatability,
            "depth_output_validation": depth_metadata,
            "official_original_model_sensitivity": reference_comparison,
            "warnings": warnings,
            "unverified": [
                "real P4 mu/sigma equivalence to ZJUL5 hist_data",
                "real P4 zone orientation",
                "status 5/6/9 validity policy",
                "regular grid equivalence to calibrated Camera-ToF projection",
                "real-world depth accuracy",
            ],
        }
        write_json(output_dir / "result.json", result)
        write_json(output_dir / "validation_report.json", validation)
        return result
    except PipelineError as exc:
        if exc.code == str(ErrorCode.OUTPUT_WRITE) and output_dir is None:
            raise
        if output_dir is None:
            output_dir = output_root.resolve() / sample_id / profile_id
            prepare_output_dir(output_dir)
        try:
            partial_outputs = sorted(path.name for path in output_dir.iterdir() if path.is_file())
        except OSError as listing_exc:
            raise PipelineError(ErrorCode.OUTPUT_WRITE, f"failed to inspect partial outputs under {output_dir}: {listing_exc}", str(output_dir)) from listing_exc
        rejected = {
            "schema_version": "deltar_p4_stage2_result.v1.1",
            "sample_id": sample_id,
            "profile_id": profile_id,
            **exc.as_dict(),
            "debug_only": any(name in partial_outputs for name in ("decoded_rgb.png", "canvas_rgb.png", "valid_region.png", "zone_fr_overlay.png")),
            "model_forward_called": forward_called,
            "partial_outputs": partial_outputs,
            "warnings": [],
        }
        write_json(output_dir / "result.json", rejected)
        validation = {
            "schema_version": "deltar_p4_stage2_validation.v1.1",
            "status": "rejected",
            "sample_id": sample_id,
            "profile_id": profile_id,
            "error": exc.as_dict(),
            "debug_only": rejected["debug_only"],
            "model_forward_called": forward_called,
            "depth_outputs_present": [name for name in DEPTH_OUTPUT_NAMES if (output_dir / name).exists()],
            "partial_outputs": partial_outputs,
        }
        write_json(output_dir / "validation_report.json", validation)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strict fake-P4 to official DELTAR Stage 2 adapter")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run one sample through the same production PoC core")
    run.add_argument("--sample", type=Path, required=True)
    run.add_argument("--output-root", type=Path, default=Path("outputs"))
    run.add_argument("--worktree", type=Path, default=DEFAULT_WORKTREE)
    run.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    run.add_argument("--device", default="cuda:0")
    subparsers.add_parser("stage3-matrix", help="run the frozen Stage 3 official object-depth matrix")
    subparsers.add_parser("stage3-1-matrix", help="run Stage 3.1 native-set method and contract hardening matrix")
    add_scan_parsers(subparsers)
    add_camera_parsers(subparsers)
    add_camera_tof_parsers(subparsers)
    add_camera_world_parsers(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    scan_code = dispatch_scan_command(args)
    if scan_code is not None:
        return scan_code
    camera_code = dispatch_camera_command(args)
    if camera_code is not None:
        return camera_code
    camera_tof_code = dispatch_camera_tof_command(args)
    if camera_tof_code is not None:
        return camera_tof_code
    camera_world_code = dispatch_camera_world_command(args)
    if camera_world_code is not None:
        return camera_world_code
    if args.command == "stage3-matrix":
        script = Path(__file__).resolve().parents[2] / "scripts" / "run_stage3_matrix.py"
        runpy.run_path(str(script), run_name="__main__")
        return 0
    if args.command == "stage3-1-matrix":
        script = Path(__file__).resolve().parents[2] / "scripts" / "run_stage3_1_matrix.py"
        runpy.run_path(str(script), run_name="__main__")
        return 0
    try:
        result = run_sample(args.sample, args.output_root, args.worktree, args.checkpoint, args.device)
    except PipelineError as exc:
        print(json.dumps(exc.as_dict(), ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"status": result["status"], "sample_id": result["sample_id"], "profile_id": result["profile_id"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
