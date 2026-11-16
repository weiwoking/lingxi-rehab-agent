from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .all_object_geometry import AllObjectGeometryProcessor, load_geometry_profile
from .frame_pipeline import BoundedDepthLookahead, FramePipeline, FramePipelineError
from .scan_aggregation import aggregate_frames, determine_scan_status
from .scan_contracts import ScanContractError, atomic_write_json, canonical_sha256, sha256_file
from .scan_profiles import ProfileBundle, load_profile_bundle
from .scan_provenance import PIPELINE_VERSION, provenance
from .scan_result import build_scan_result
from .pc_scene_ui_adapter import build_and_render_pc_scene_ui
from .stage8b4a_package import build_and_write_stage8b4a
from .stage6_schema import (
    FRAME_RESULT_SCHEMA,
    JOB_REPORT_SCHEMA,
    SCAN_RESULT_SCHEMA,
    validate_frame_result_v1_1,
    validate_job_report_v1_1,
    validate_scan_result_v1_1,
)
from .stage7d_contracts import bind_stage7d_profiles
from .stage7d_outputs import Stage7DProcessor
from .scan_validator import validate_scan
from .validation_receipt import load_receipted_scan
from .pipeline_progress import PipelineProgressReporter
from .frozen_runtime_contracts import ScanJobCancelled, ScanJobRequest
from .frozen_runtime_resources import SharedRuntimeResources
from .tof_degraded import build_tof_decisions


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_compact_degradation_artifacts(
    output_dir: Path, result: dict[str, Any]
) -> None:
    statuses = list(result.get("frame_processing_status") or [])
    atomic_write_json(
        output_dir / "frame_processing_status.json",
        {
            "schema_version": "deltar_frame_processing_status.v1",
            "scan_id": result["scan_id"],
            "frame_count": len(statuses),
            "frames": statuses,
        },
    )
    atomic_write_json(
        output_dir / "tof_frame_diagnostics.json",
        {
            "schema_version": "deltar_tof_frame_diagnostics.v1",
            "scan_id": result["scan_id"],
            "raw_tof_retained": False,
            "frames": [
                {
                    "frame_id": item["frame_id"],
                    "sequence": item["sequence"],
                    "tof_status": item["tof_status"],
                    "degradation_mode": item["degradation_mode"],
                    "reason_codes": item["reason_codes"],
                    "diagnostics": item["tof_diagnostics"],
                }
                for item in statuses
            ],
        },
    )
    geometry_path = output_dir / "stage8b_all_object_geometry.v1.json"
    if geometry_path.is_file():
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
        sources: dict[str, int] = {}
        failures: dict[str, int] = {}
        for frame in geometry.get("frames", []):
            for item in frame.get("objects", []):
                if item.get("numeric_available") is True:
                    source = str((item.get("diagnostics") or {}).get("support_source") or "unknown")
                    sources[source] = sources.get(source, 0) + 1
                else:
                    reason = str(item.get("failure_reason") or "unknown")
                    failures[reason] = failures.get(reason, 0) + 1
        atomic_write_json(
            output_dir / "support_authority_summary.json",
            {
                "schema_version": "deltar_support_authority_summary.v1",
                "scan_id": result["scan_id"],
                "support_is_shape_immutable": True,
                "support_source_counts": dict(sorted(sources.items())),
                "geometry_failure_counts": dict(sorted(failures.items())),
            },
        )


def _raise_if_cancelled(cancel_path: Path | None) -> None:
    if cancel_path is not None and cancel_path.is_file():
        raise ScanJobCancelled("ABORTED_BY_REAL_TRAFFIC")


def _append_phase(
    output_dir: Path,
    phase: str,
    phase_start: str,
    started: float,
    *,
    details: dict[str, Any] | None = None,
    scan_id: str | None = None,
) -> None:
    payload = {
        "scan_id": scan_id or output_dir.name,
        "phase": phase,
        "phase_start": phase_start,
        "phase_end": utc_now(),
        "elapsed_seconds": time.perf_counter() - started,
        "process_id": os.getpid(),
        "command": "deltar_p4_pipeline.cli scan-run",
        "exit_code": None,
    }
    if details:
        payload["details"] = details
    path = output_dir / "logs" / "pipeline_phases.jsonl"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _job_id(scan: dict[str, Any], bundle: ProfileBundle) -> str:
    digest = canonical_sha256({"scan_id": scan["scan_id"], "created_at": scan["created_at"], "profiles": bundle.hashes})
    return f"job_{digest[:20]}"


def _phase6_attempt_token(output_dir: Path) -> int:
    for part in reversed(output_dir.parts):
        match = re.fullmatch(r"generation_\d+_attempt_(\d+)", part)
        if match is not None:
            return int(match.group(1))
    return 1


def _job_report(job_id: str, scan_id: str, status: str, started_at: str, *, result_status: str | None,
                errors: list[dict[str, Any]], warnings: list[str], frame_count: int,
                processed_frames: list[str], rejected_frames: list[dict[str, Any]], bundle: ProfileBundle,
                exit_code: int, result_written: bool, total_ms: float) -> dict[str, Any]:
    model_hashes = {item["task"]: item["sha256"] for item in bundle.model_manifest["models"]}
    report = {
        "schema_version": JOB_REPORT_SCHEMA,
        "job_id": job_id,
        "scan_id": scan_id,
        "status": status,
        "exit_code": exit_code,
        "started_at": started_at,
        "finished_at": utc_now(),
        "result_status": result_status,
        "input": {"frame_count": frame_count},
        "output": {
            "result_schema": SCAN_RESULT_SCHEMA,
            "frame_result_schema": FRAME_RESULT_SCHEMA,
            "result_written": result_written,
        },
        "processed_frames": processed_frames,
        "rejected_frames": rejected_frames,
        "warnings": sorted(set(warnings)),
        "errors": errors,
        "provenance": {"pipeline_version": PIPELINE_VERSION, "model_hashes": model_hashes, "profile_hashes": bundle.hashes},
        "processing": {"total_ms": total_ms},
        "resume_supported": False,
        "persistent_queue": False,
    }
    validate_job_report_v1_1(report)
    return report


def _rejected_frame_result(frame: Any, bundle: ProfileBundle, exc: FramePipelineError, elapsed_ms: float) -> dict[str, Any]:
    valid_tof = [
        float(value) for value, status, count in zip(
            frame.tof["distance_mm"], frame.tof["target_status"], frame.tof["nb_target_detected"]
        ) if count > 0 and status in {5, 6, 9}
    ]
    valid_tof.sort()
    median = None if not valid_tof else valid_tof[len(valid_tof) // 2] if len(valid_tof) % 2 else (valid_tof[len(valid_tof) // 2 - 1] + valid_tof[len(valid_tof) // 2]) / 2.0
    models = {item["task"]: {"task": item["task"], "sha256": item["sha256"]} for item in bundle.model_manifest["models"]}
    result = {
        "schema_version": FRAME_RESULT_SCHEMA,
        "status": "rejected",
        "frame_id": frame.frame["frame_id"],
        "sequence": frame.frame["sequence"],
        "timestamps": {key: frame.frame[key] for key in ("camera_timestamp_us", "tof_timestamp_us", "pair_delta_us")},
        "decoded_image": {"width": 640, "height": 360, "pixel_format": "RGB565_LE", "payload_bytes": 460800, "payload_sha256": sha256_file(frame.image_path)},
        "tof_summary": {"valid_zone_count": len(valid_tof), "median_distance_mm": median},
        "models": models,
        "profiles": bundle.public_provenance(),
        "deltar_repeatability": {},
        "detections": {"detect_count": 0, "segment_count": 0, "matched_count": 0, "detect_instances": [], "segment_instances": []},
        "logical_object_groups": [],
        "region_validation": [],
        "objects": [],
        "warnings": ["W_FRAME_REJECTED"],
        "rejections": [frame.frame["frame_id"]],
        "errors": [{
            "code": exc.code,
            "phase": exc.phase,
            "message": exc.message,
            "details": {**exc.details, "error_type": exc.error_type},
        }],
        "gt_used_for_inference": False,
        "processing_ms": {"frame_total_ms": elapsed_ms},
    }
    validate_frame_result_v1_1(result)
    return result


def run_scan(scan_dir: Path, output_dir: Path, profile_dir: Path, *, device: str = "cuda:0", resume: bool = False,
             overwrite: bool = False, stage7d_horizontal_planes: bool = False,
             stage7d_diagnostics: bool = False,
             stage7d_profile: Path | None = None,
             generate_full_visualization: bool = True,
             validation_receipt_required: bool = False,
             shared_runtime_resources: SharedRuntimeResources | None = None,
             cancel_path: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    if resume:
        raise ScanContractError("E_SCAN_SCHEMA", "--resume is forbidden in Stage 6", "resume")
    if overwrite:
        raise ScanContractError("E_SCAN_OUTPUT_EXISTS", "overwrite is intentionally unsupported in Stage 6", "overwrite")
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise ScanContractError("E_SCAN_OUTPUT_EXISTS", "output directory already exists", "output_dir")
    started_wall = utc_now()
    started = time.perf_counter()
    input_phase_started_at = utc_now()
    input_phase_started = time.perf_counter()
    bundle = (
        shared_runtime_resources.bundle
        if shared_runtime_resources is not None
        else load_profile_bundle(profile_dir, validate_model_hashes=True)
    )
    if shared_runtime_resources is not None:
        if bundle.root != Path(profile_dir).resolve():
            raise ScanContractError("E_PROFILE_BUNDLE", "runtime profile bundle does not match job request", "profile_bundle")
        if shared_runtime_resources.bridge.device != device:
            raise ScanContractError("E_PROFILE_BUNDLE", "runtime device does not match job request", "device")
    stage7d_context = None
    if stage7d_horizontal_planes:
        if stage7d_profile is None:
            raise ScanContractError(
                "E_STAGE7D_PROFILE",
                "formal Geometry requires the horizontal-plane profile",
                "stage7d",
            )
        stage7d_context = bind_stage7d_profiles(stage7d_profile)
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
        (output_dir / "frame_results").mkdir()
        (output_dir / "logs").mkdir()
    except OSError as exc:
        raise ScanContractError("E_SCAN_OUTPUT_WRITE", f"cannot create output directory: {exc}", "output_dir") from exc
    reporter = PipelineProgressReporter.for_output(
        output_dir,
        scan_dir.name if shared_runtime_resources is not None else output_dir.name,
    )
    reporter.emit(
        "pipeline", "terminal", "started", elapsed_ms=(time.perf_counter() - started) * 1000.0,
        message="扫描Pipeline开始", checkpoint=True,
    )
    reporter.emit(
        "pipeline_setup", "asset_validation", "started", message="正在校验扫描资产",
    )
    limits = bundle.algorithm["limits"]
    try:
        if validation_receipt_required:
            validated = load_receipted_scan(scan_dir)
        else:
            validated = validate_scan(
                scan_dir, bundle, max_frames=int(limits["max_frames_per_scan"]),
                max_scan_bytes=int(limits["max_scan_bytes"]), allow_frame_rejections=True,
            )
        if not validated.frames:
            raise ScanContractError(
                "E_SCAN_NO_VALID_FRAMES", "all frames violate the input contract", "frames",
                list(validated.rejected_frames),
            )
    except ScanContractError as exc:
        reporter.emit(
            "pipeline_setup", "asset_validation", "failed",
            elapsed_ms=(time.perf_counter() - input_phase_started) * 1000.0,
            message="扫描资产校验失败",
            error={"code": exc.code, "type": type(exc).__name__, "message": exc.message}, checkpoint=True,
        )
        reporter.emit(
            "pipeline", "terminal", "failed", elapsed_ms=(time.perf_counter() - started) * 1000.0,
            message="扫描Pipeline失败",
            error={"code": exc.code, "type": type(exc).__name__, "message": exc.message}, checkpoint=True,
        )
        reporter.close()
        raise
    reporter.emit(
        "pipeline_setup", "asset_validation", "completed",
        elapsed_ms=(time.perf_counter() - input_phase_started) * 1000.0,
        message="扫描资产校验完成",
        metrics={"validated_frames": len(validated.frames), "rejected_frames": len(validated.rejected_frames),
                 "frame_total": int(validated.scan["frame_count"])},
        checkpoint=True,
    )
    _append_phase(
        output_dir,
        "input_preparation",
        input_phase_started_at,
        input_phase_started,
        details={"validated_frame_count": len(validated.frames)},
        scan_id=validated.scan["scan_id"],
    )
    source_png_parallel_requested = (
        os.environ.get("DELTAR_PHASE6_SOURCE_PNG_PARALLEL") == "1"
    )
    if source_png_parallel_requested and not (
        os.environ.get("DELTAR_PHASE6_LAB") == "1"
        and os.environ.get("DELTAR_PHASE6_GPU_LOOKAHEAD") == "1"
        and os.environ.get("DELTAR_PHASE6_STAGE7D_LOOKAHEAD") == "1"
        and stage7d_context is not None
        and int(validated.scan["frame_count"]) == 11
    ):
        raise ScanContractError(
            "E_PHASE6_SOURCE_PNG_PARALLEL_SCOPE",
            "parallel source PNG writes require the Phase 6 formal 11-frame Stage 7D lookahead path",
            "DELTAR_PHASE6_SOURCE_PNG_PARALLEL",
        )
    stage6_required_region_requested = (
        os.environ.get("DELTAR_PHASE6_STAGE6_REQUIRED_REGION") == "1"
    )
    if stage6_required_region_requested and not source_png_parallel_requested:
        raise ScanContractError(
            "E_PHASE6_STAGE6_REQUIRED_REGION_SCOPE",
            "Stage 6 required-region evaluation requires the retained Phase 6 source PNG path",
            "DELTAR_PHASE6_STAGE6_REQUIRED_REGION",
        )
    stage7d_ransac_batch_requested = (
        os.environ.get("DELTAR_PHASE6_STAGE7D_RANSAC_BATCH") == "1"
    )
    if stage7d_ransac_batch_requested and not stage6_required_region_requested:
        raise ScanContractError(
            "E_PHASE6_STAGE7D_RANSAC_BATCH_SCOPE",
            "Stage 7D RANSAC batching requires the retained Phase 6 required-region path",
            "DELTAR_PHASE6_STAGE7D_RANSAC_BATCH",
        )
    stage7d_png_pair_parallel_requested = (
        os.environ.get("DELTAR_PHASE6_STAGE7D_PNG_PAIR_PARALLEL") == "1"
    )
    if stage7d_png_pair_parallel_requested and not stage6_required_region_requested:
        raise ScanContractError(
            "E_PHASE6_STAGE7D_PNG_PAIR_PARALLEL_SCOPE",
            "Stage 7D visual PNG pair parallelism requires the retained E22 path",
            "DELTAR_PHASE6_STAGE7D_PNG_PAIR_PARALLEL",
        )
    job_id = _job_id(validated.scan, bundle)
    frame_results: list[dict[str, Any]] = []
    rejected_frames = list(validated.rejected_frames)
    frame_failures: list[FramePipelineError] = []
    warnings: list[str] = ["W_UNMEASURED_CALIBRATION"] if bundle.calibration["calibration_status"] != "measured" else []
    stage7d_processor = (
        None
        if stage7d_context is None
        else Stage7DProcessor(
            stage7d_context,
            output_dir,
            validated.scan["scan_id"],
            diagnostics_enabled=stage7d_diagnostics,
            ransac_batch_bound=(32 if stage7d_ransac_batch_requested else 1),
            visual_png_workers=(2 if stage7d_png_pair_parallel_requested else 1),
        )
    )
    all_object_geometry_processor = None
    if int(validated.scan["frame_count"]) == 11 and stage7d_context is not None:
        all_object_geometry_processor = AllObjectGeometryProcessor(
            output_dir,
            str(validated.scan["scan_id"]),
            11,
            load_geometry_profile(
                Path(profile_dir).resolve() / "formal_geometry_region_profile.json"
            ),
        )
    tof_decisions = build_tof_decisions(validated.frames)
    pipeline = FramePipeline(
        bundle,
        output_dir,
        device=device,
        stage7d_processor=stage7d_processor,
        all_object_geometry_processor=all_object_geometry_processor,
        progress_reporter=reporter,
        shared_bridge=(shared_runtime_resources.bridge if shared_runtime_resources is not None else None),
        stage7d_lookahead_enabled=(
            os.environ.get("DELTAR_PHASE6_STAGE7D_LOOKAHEAD") == "1"
        ),
        source_png_workers=(4 if source_png_parallel_requested else 1),
        stage6_required_region_only=stage6_required_region_requested,
        tof_decisions=tof_decisions,
    )
    yolo_phase_started_at = utc_now()
    yolo_phase_started = time.perf_counter()
    _raise_if_cancelled(cancel_path)
    try:
        pipeline.prepare_scan_yolo(validated.scan["scan_id"], validated.frames)
    except FramePipelineError as exc:
        reporter.emit(
            "yolo", "detect", "failed", elapsed_ms=(time.perf_counter() - yolo_phase_started) * 1000.0,
            message="YOLO批处理失败", error={"code": exc.code, "type": exc.error_type, "message": exc.message},
            checkpoint=True,
        )
        reporter.emit(
            "pipeline", "terminal", "failed", elapsed_ms=(time.perf_counter() - started) * 1000.0,
            message="扫描Pipeline失败", error={"code": exc.code, "type": exc.error_type, "message": exc.message},
            checkpoint=True,
        )
        reporter.close()
        failure = {
            "schema_version": "stage8b1_yolo_batch_failure.v1",
            "scan_id": validated.scan["scan_id"],
            "phase": exc.phase,
            "message": exc.message,
            "device": device,
            "input_preserved": True,
            "result_written": False,
        }
        atomic_write_json(output_dir / "stage8b1_yolo_batch_failure.json", failure)
        report = _job_report(
            job_id,
            validated.scan["scan_id"],
            "failed",
            started_wall,
            result_status="failed",
            errors=[
                {
                    "code": "E_YOLO_BATCH",
                    "phase": exc.phase,
                    "message": exc.message,
                }
            ],
            warnings=warnings,
            frame_count=validated.scan["frame_count"],
            processed_frames=[],
            rejected_frames=rejected_frames,
            bundle=bundle,
            exit_code=4,
            result_written=False,
            total_ms=(time.perf_counter() - started) * 1000.0,
        )
        atomic_write_json(output_dir / "job_report.json", report)
        raise
    _append_phase(
        output_dir,
        "yolo_detect_segment",
        yolo_phase_started_at,
        yolo_phase_started,
        details={"worker_invocations": {"detect": 1, "segment": 1}},
        scan_id=validated.scan["scan_id"],
    )
    _raise_if_cancelled(cancel_path)
    shared_phase_started_at = utc_now()
    shared_phase_started = time.perf_counter()
    frame_total = int(validated.scan["frame_count"])
    completed_frames = 0
    successful_frames = 0
    failed_frames = 0
    lookahead_requested = (
        os.environ.get("DELTAR_PHASE6_GPU_LOOKAHEAD") == "1"
        and all(decision["tof"] is not None for decision in tof_decisions.values())
    )
    lookahead_lab_enabled = os.environ.get("DELTAR_PHASE6_LAB") == "1"
    stage7d_lookahead_requested = (
        os.environ.get("DELTAR_PHASE6_STAGE7D_LOOKAHEAD") == "1"
        and lookahead_requested
    )
    if lookahead_requested and not lookahead_lab_enabled:
        raise ScanContractError(
            "E_PHASE6_LAB_ONLY",
            "bounded DELTAR lookahead requires the Phase 6 lab guard",
            "DELTAR_PHASE6_LAB",
        )
    if lookahead_requested and (stage7d_context is None or frame_total != 11):
        raise ScanContractError(
            "E_PHASE6_LOOKAHEAD_SCOPE",
            "bounded DELTAR lookahead is restricted to the formal 11-frame Stage 7D path",
            "DELTAR_PHASE6_GPU_LOOKAHEAD",
        )
    if stage7d_lookahead_requested and not lookahead_lab_enabled:
        raise ScanContractError(
            "E_PHASE6_STAGE7D_LOOKAHEAD_DEPENDENCY",
            "Stage 7D lookahead requires both Phase 6 lab and DELTAR lookahead guards",
            "DELTAR_PHASE6_STAGE7D_LOOKAHEAD",
        )
    lookahead = (
        None
        if not lookahead_requested
        else BoundedDepthLookahead(
            pipeline,
            validated.frames,
            scan_id=str(validated.scan["scan_id"]),
            attempt=_phase6_attempt_token(output_dir),
            runtime_generation=(
                shared_runtime_resources.generation
                if shared_runtime_resources is not None
                else 0
            ),
        )
    )
    try:
        for frame in validated.frames:
            _raise_if_cancelled(cancel_path)
            frame_started = time.perf_counter()
            frame_index = int(frame.frame["sequence"]) + 1
            reporter.emit(
                "frame_processing", "frame", "started", frame_id=frame.frame["frame_id"],
                frame_index=frame_index, frame_total=frame_total, current=frame_index, total=frame_total,
                message=f"正在处理第{frame_index}/{frame_total}帧",
                metrics={"completed_frames": completed_frames, "successful_frames": successful_frames,
                         "rejected_frames": len(rejected_frames), "failed_frames": failed_frames},
            )
            try:
                prefetched_depth = None
                expected_lookahead_token = None
                if lookahead is not None:
                    prefetched_depth, expected_lookahead_token = lookahead.take(frame)
                result = pipeline.process(
                    frame,
                    frame_index=frame_index,
                    frame_total=frame_total,
                    prefetched_depth=prefetched_depth,
                    expected_lookahead_token=expected_lookahead_token,
                )
                frame_results.append(result)
                warnings.extend(result["warnings"])
                write_started = time.perf_counter()
                reporter.emit(
                    "frame_processing", "frame_result_write", "started", frame_id=frame.frame["frame_id"],
                    frame_index=frame_index, frame_total=frame_total, current=frame_index, total=frame_total,
                    message="正在写入原子帧结果",
                )
                atomic_write_json(output_dir / "frame_results" / f"{frame.frame['frame_id']}.json", result)
                reporter.emit(
                    "frame_processing", "frame_result_write", "completed", frame_id=frame.frame["frame_id"],
                    frame_index=frame_index, frame_total=frame_total, current=frame_index, total=frame_total,
                    elapsed_ms=(time.perf_counter() - write_started) * 1000.0, message="原子帧结果写入完成",
                )
                completed_frames += 1
                if result.get("status") == "success":
                    successful_frames += 1
                else:
                    failed_frames += 1
                reporter.emit(
                    "frame_processing", "frame", "completed", frame_id=frame.frame["frame_id"],
                    frame_index=frame_index, frame_total=frame_total, current=completed_frames, total=frame_total,
                    elapsed_ms=(time.perf_counter() - frame_started) * 1000.0,
                    message=f"第{frame_index}/{frame_total}帧处理完成",
                    metrics={"completed_frames": completed_frames, "successful_frames": successful_frames,
                             "rejected_frames": len(rejected_frames), "failed_frames": failed_frames,
                             "result_status": result.get("status")}, checkpoint=True,
                )
            except FramePipelineError as exc:
                frame_failures.append(exc)
                rejected_result = _rejected_frame_result(frame, bundle, exc, (time.perf_counter() - frame_started) * 1000.0)
                rejected_frames.append({
                    "frame_id": frame.frame["frame_id"],
                    "sequence": frame.frame["sequence"],
                    "error": rejected_result["errors"][0],
                })
                atomic_write_json(output_dir / "frame_results" / f"{frame.frame['frame_id']}.json", rejected_result)
                completed_frames += 1
                failed_frames += 1
                reporter.emit(
                    "frame_processing", "frame", "failed", frame_id=frame.frame["frame_id"],
                    frame_index=frame_index, frame_total=frame_total, current=completed_frames, total=frame_total,
                    elapsed_ms=(time.perf_counter() - frame_started) * 1000.0,
                    message=f"第{frame_index}/{frame_total}帧已写入拒绝结果",
                    metrics={"completed_frames": completed_frames, "successful_frames": successful_frames,
                             "rejected_frames": len(rejected_frames), "failed_frames": failed_frames},
                    error={"code": exc.code, "type": exc.error_type, "message": exc.message}, checkpoint=True,
                )
    finally:
        if lookahead is not None:
            lookahead.close()
    _raise_if_cancelled(cancel_path)
    if frame_failures and not frame_results:
        if stage7d_processor is not None and stage7d_processor.frame_records:
            stage7d_processor.write_summary()
        phase = "model_inference" if all(item.phase == "model_inference" for item in frame_failures) else "frame_processing"
        failure_exit = 4 if all(item.phase == "model_inference" for item in frame_failures) else 5
        report = _job_report(job_id, validated.scan["scan_id"], "failed", started_wall, result_status="failed",
                             errors=[item["error"] for item in rejected_frames],
                             warnings=warnings, frame_count=validated.scan["frame_count"], processed_frames=[],
                             rejected_frames=rejected_frames, bundle=bundle, exit_code=failure_exit,
                             result_written=False, total_ms=(time.perf_counter() - started) * 1000.0)
        atomic_write_json(output_dir / "job_report.json", report)
        representative = frame_failures[0]
        reporter.emit(
            "pipeline", "terminal", "failed", elapsed_ms=(time.perf_counter() - started) * 1000.0,
            message="全部有效帧处理失败",
            error={"code": representative.code, "type": representative.error_type, "message": representative.message},
            checkpoint=True,
        )
        reporter.close()
        raise FramePipelineError(
            phase,
            representative.message,
            code=representative.code,
            error_type=representative.error_type,
            details={
                **representative.details,
                "failed_frame_count": len(frame_failures),
                "total_valid_frame_count": len(validated.frames),
            },
        ) from representative
    if stage7d_processor is not None:
        stage7d_processor.write_summary()
    package_result = None
    if all_object_geometry_processor is not None:
        _raise_if_cancelled(cancel_path)
        dual_gate_started_at = utc_now()
        dual_gate_started = time.perf_counter()
        reporter.emit("all_object_geometry", "geometry_document_write", "started", message="正在写入全对象几何")
        geometry_write_started = time.perf_counter()
        all_object_geometry_processor.write()
        reporter.emit(
            "all_object_geometry", "geometry_document_write", "completed",
            elapsed_ms=(time.perf_counter() - geometry_write_started) * 1000.0,
            message="全对象几何写入完成", checkpoint=True,
        )
        repository_root = Path(__file__).resolve().parents[2]
        reporter.emit("selection", "gate1", "started", message="正在执行Gate1")
        reporter.emit("selection", "gate2", "started", message="正在执行Gate2")
        reporter.emit("selection", "selected_write", "started", message="正在生成选择结果")
        reporter.emit("package", "package_write", "started", message="正在构建Package")
        package_started = time.perf_counter()
        package_result = build_and_write_stage8b4a(
            output_dir, repository_root=repository_root
        )
        reporter.emit("selection", "gate1", "completed", message="Gate1完成", checkpoint=True)
        reporter.emit("selection", "gate2", "completed", message="Gate2完成", checkpoint=True)
        reporter.emit(
            "selection", "selected_write", "completed", message="选择结果写入完成",
            metrics={"selected_count": len(package_result["selected_document"]["items"])}, checkpoint=True,
        )
        reporter.emit(
            "package", "package_write", "completed", elapsed_ms=(time.perf_counter() - package_started) * 1000.0,
            message="Package构建完成", metrics={"package_bytes": package_result["package_bytes"]}, checkpoint=True,
        )
        _append_phase(
            output_dir,
            "stage8b_dual_gate_package",
            dual_gate_started_at,
            dual_gate_started,
            details={
                "gate_object_count": len(package_result["gate_document"]["objects"]),
                "selected_object_count": len(package_result["selected_document"]["items"]),
                "package_bytes": package_result["package_bytes"],
            },
            scan_id=validated.scan["scan_id"],
        )
        if generate_full_visualization:
            visualization_started_at = utc_now()
            visualization_started = time.perf_counter()
            reporter.emit("visualization", "render", "started", message="正在生成可视化")
            manifest = build_and_render_pc_scene_ui(output_dir)
            reporter.emit(
                "visualization", "render", "completed",
                elapsed_ms=(time.perf_counter() - visualization_started) * 1000.0,
                message="可视化生成完成", checkpoint=True,
            )
            _append_phase(
                output_dir,
                "stage8b3_full_visualization",
                visualization_started_at,
                visualization_started,
                details={
                    "html_path": str(
                        (output_dir / manifest["html"]["path"]).resolve()
                    )
                },
                scan_id=validated.scan["scan_id"],
            )
    _append_phase(
        output_dir,
        "deltar_formal_geometry_v2",
        shared_phase_started_at,
        shared_phase_started,
        details={
            "frame_count": len(frame_results),
            "deltar_seconds": sum(
                float(frame["processing_ms"].get("deltar_total_ms", 0.0))
                for frame in frame_results
            )
            / 1000.0,
        },
        scan_id=validated.scan["scan_id"],
    )
    aggregation_started = time.perf_counter()
    _raise_if_cancelled(cancel_path)
    objects, aggregation_warnings, aggregation = aggregate_frames(frame_results, bundle)
    aggregation_ms = (time.perf_counter() - aggregation_started) * 1000.0
    warnings.extend(aggregation_warnings)
    if rejected_frames or frame_failures:
        warnings.append("W_FRAME_REJECTED")
    status = determine_scan_status(frame_results, objects, bundle.calibration["calibration_status"])
    if rejected_frames and status == "success":
        status = "partial_success"
    root = Path(__file__).resolve().parents[2]
    prov = provenance(root, bundle)
    processing = {
        "frame_processing_ms": {frame["frame_id"]: frame["processing_ms"] for frame in frame_results},
        "aggregation_ms": aggregation_ms,
        "total_ms": (time.perf_counter() - started) * 1000.0,
        "started_at": started_wall,
        "finished_at": utc_now(),
        "frames_processed_in_sequence": True,
        "dense_depth_retained": False,
    }
    result = build_scan_result(scan=validated.scan, job_id=job_id, frame_results=frame_results,
                               rejected_frames=rejected_frames, objects=objects, warnings=warnings, errors=[], status=status,
                               aggregation=aggregation, provenance=prov, processing=processing, bundle=bundle)
    validate_scan_result_v1_1(result)
    atomic_write_json(output_dir / "result.json", result)
    _write_compact_degradation_artifacts(output_dir, result)
    job_status = "success" if status == "success" else "partial_success" if status == "partial_success" else "rejected"
    report = _job_report(job_id, validated.scan["scan_id"], job_status, started_wall, result_status=status,
                         errors=[], warnings=warnings, frame_count=validated.scan["frame_count"],
                         processed_frames=[frame["frame_id"] for frame in frame_results], rejected_frames=rejected_frames,
                         bundle=bundle, exit_code=0, result_written=True, total_ms=(time.perf_counter() - started) * 1000.0)
    atomic_write_json(output_dir / "job_report.json", report)
    try:
        (output_dir / "logs" / "scan.log").write_text(
            f"job_id={job_id}\nscan_id={validated.scan['scan_id']}\nstatus={status}\nvalid_frames={len(frame_results)}\nrejected_frames={len(rejected_frames)}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        raise ScanContractError("E_SCAN_OUTPUT_WRITE", f"cannot write scan log: {exc}", "logs/scan.log") from exc
    reporter.emit(
        "pipeline", "terminal", "completed", elapsed_ms=(time.perf_counter() - started) * 1000.0,
        message="扫描Pipeline完成",
        metrics={"completed_frames": completed_frames, "successful_frames": successful_frames,
                 "rejected_frames": len(rejected_frames), "failed_frames": failed_frames,
                 "reporter_serialize_ms": reporter.serialize_ms,
                 "reporter_flush_ms": reporter.flush_ms, "reporter_fsync_ms": reporter.fsync_ms},
        checkpoint=True,
    )
    reporter.close()
    return result, report


def run_scan_job(
    request: ScanJobRequest,
    shared_runtime_resources: SharedRuntimeResources,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run one fully isolated scan job against immutable shared runtime resources."""
    return run_scan(
        request.scan_dir,
        request.output_dir,
        request.profile_dir,
        device=request.device,
        resume=request.resume,
        overwrite=request.overwrite,
        stage7d_horizontal_planes=request.stage7d_horizontal_planes,
        stage7d_diagnostics=request.stage7d_diagnostics,
        stage7d_profile=request.stage7d_profile,
        generate_full_visualization=request.generate_full_visualization,
        validation_receipt_required=request.validation_receipt_required,
        shared_runtime_resources=shared_runtime_resources,
        cancel_path=request.cancel_path,
    )
