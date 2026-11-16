from __future__ import annotations

import json
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from .all_object_geometry import AllObjectGeometryProcessor
from .canvas import apply_canvas, map_depth_to_source
from .deltar_bridge import DeltarBridge
from .formal_geometry_authority import load_formal_geometry_authority
from .frozen_runtime_contracts import (
    DEFAULT_DELTAR_CHECKPOINT,
    DEFAULT_DELTAR_WORKTREE,
)
from .rgb565 import read_rgb565_le
from .scan_contracts import ScanContractError, atomic_write_json, sha256_file
from .scan_profiles import ProfileBundle
from .scan_validator import ValidatedFrame
from .stage6_formal import build_logical_groups, formal_result_to_observation
from .stage6_schema import normalize_frame_diagnostics, validate_frame_result_v1_1
from .stage7d_outputs import Stage7DProcessor
from .tof_adapter import OfficialUtilsPerJobCache, adapt_inputs
from .tof_degraded import formal_valid_count, public_tof_decision
from .yolo_worker_client import run_yolo_pair

DEFAULT_WORKTREE = DEFAULT_DELTAR_WORKTREE
DEFAULT_CHECKPOINT = DEFAULT_DELTAR_CHECKPOINT
DEFAULT_YOLO_PYTHON = Path(r"C:\ec\_deltar_yolo_cuda\venv\Scripts\python.exe")
CORE_CLASSES = ("person", "chair", "dining table", "cup", "bottle", "book", "tv")
EXPECTED_GPU_NAME = "RTX 5070 Ti"
BOOK_CLASS_ID = 73


def _save_source_png(path: Path, rgb: np.ndarray) -> None:
    Image.fromarray(rgb, mode="RGB").save(path)


def _write_source_pngs(
    items: list[tuple[Path, np.ndarray]], workers: int = 1
) -> None:
    if workers < 1 or workers > 4:
        raise ValueError("source PNG workers must be between 1 and 4")
    if workers == 1:
        for path, rgb in items:
            _save_source_png(path, rgb)
        return
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="phase6-source-png"
    ) as executor:
        futures = [executor.submit(_save_source_png, path, rgb) for path, rgb in items]
        try:
            for future in futures:
                future.result()
        except BaseException:
            for future in futures:
                future.cancel()
            raise


@dataclass(frozen=True)
class PreparedSource:
    rgb: np.ndarray
    path: Path
    decode_rgb565_ms: float


@dataclass(frozen=True)
class CachedYolo:
    detect_document: dict[str, Any]
    segment_document: dict[str, Any]
    masks: dict[str, np.ndarray]


@dataclass(frozen=True)
class DepthLookaheadToken:
    scan_id: str
    frame_id: str
    attempt: int
    runtime_generation: int


@dataclass(frozen=True)
class PrefetchedDepth:
    token: DepthLookaheadToken
    depths: dict[str, np.ndarray]
    repeatability: dict[str, Any]
    timings: dict[str, Any]
    total_ms: float
    stage7d_record: dict[str, Any] | None = None


class BoundedDepthLookahead:
    def __init__(
        self,
        pipeline: "FramePipeline",
        frames: list[ValidatedFrame],
        *,
        scan_id: str,
        attempt: int,
        runtime_generation: int,
    ) -> None:
        self._pipeline = pipeline
        self._frames = list(frames)
        self._scan_id = scan_id
        self._attempt = attempt
        self._runtime_generation = runtime_generation
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="phase6-deltar-lookahead"
        )
        self._index = 0
        self._future: Future[PrefetchedDepth] | None = None
        self._closed = False
        if self._frames:
            self._future = self._submit(self._frames[0])

    def _token(self, frame: ValidatedFrame) -> DepthLookaheadToken:
        return DepthLookaheadToken(
            scan_id=self._scan_id,
            frame_id=str(frame.frame["frame_id"]),
            attempt=self._attempt,
            runtime_generation=self._runtime_generation,
        )

    def _submit(self, frame: ValidatedFrame) -> Future[PrefetchedDepth]:
        return self._executor.submit(
            self._pipeline.prepare_depth_lookahead, frame, self._token(frame)
        )

    def take(
        self, frame: ValidatedFrame
    ) -> tuple[PrefetchedDepth, DepthLookaheadToken]:
        if self._closed or self._future is None:
            raise FramePipelineError(
                "model_inference",
                "bounded DELTAR lookahead has no pending frame",
                code="E_DELTAR_LOOKAHEAD_STATE",
            )
        expected_frame = self._frames[self._index]
        expected_token = self._token(expected_frame)
        if frame is not expected_frame:
            raise FramePipelineError(
                "model_inference",
                "bounded DELTAR lookahead frame order mismatch",
                code="E_DELTAR_LOOKAHEAD_TOKEN",
            )
        current = self._future
        self._index += 1
        self._future = (
            self._submit(self._frames[self._index])
            if self._index < len(self._frames)
            else None
        )
        try:
            return current.result(), expected_token
        except FramePipelineError:
            raise
        except Exception as exc:
            raise FramePipelineError.from_cause(
                "model_inference",
                f"bounded DELTAR lookahead failed: {type(exc).__name__}: {exc}",
                exc,
            ) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._future is not None:
            self._future.cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
        self._future = None

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass


class FramePipelineError(Exception):
    def __init__(
        self,
        phase: str,
        message: str,
        *,
        code: str = "E_FRAME_PROCESSING",
        error_type: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.phase = phase
        self.message = message
        self.code = code
        self.error_type = error_type
        self.details = details or {}

    @classmethod
    def from_cause(
        cls, phase: str, message: str, cause: Exception
    ) -> "FramePipelineError":
        code = str(getattr(cause, "code", "E_FRAME_PROCESSING"))
        cause_details = getattr(cause, "details", None)
        details: dict[str, Any] = {}
        if isinstance(cause_details, dict):
            for key, value in cause_details.items():
                if isinstance(value, list) and len(value) > 32:
                    details[key] = value[:32]
                    details[f"{key}_total"] = len(value)
                else:
                    details[key] = value
        details.setdefault("error_type", type(cause).__name__)
        return cls(
            phase,
            message,
            code=code,
            error_type=type(cause).__name__,
            details=details,
        )


@dataclass
class FramePipeline:
    bundle: ProfileBundle
    output_root: Path
    device: str = "cuda:0"
    worktree: Path = DEFAULT_WORKTREE
    checkpoint: Path = DEFAULT_CHECKPOINT
    yolo_python: Path = DEFAULT_YOLO_PYTHON
    stage7d_processor: Stage7DProcessor | None = None
    all_object_geometry_processor: AllObjectGeometryProcessor | None = None
    progress_reporter: Any = None
    shared_bridge: DeltarBridge | None = None
    stage7d_lookahead_enabled: bool = False
    source_png_workers: int = 1
    stage6_required_region_only: bool = False
    tof_decisions: dict[str, dict[str, Any]] | None = None

    def __post_init__(self) -> None:
        if self.source_png_workers < 1 or self.source_png_workers > 4:
            raise ValueError("source PNG workers must be between 1 and 4")
        self._bridge: DeltarBridge | None = self.shared_bridge
        self.formal_authority = load_formal_geometry_authority()
        self._models = {item["task"]: item for item in self.bundle.model_manifest["models"]}
        self._prepared_sources: dict[str, PreparedSource] = {}
        self._cached_yolo: dict[str, CachedYolo] = {}
        self._official_utils_cache = OfficialUtilsPerJobCache()
        self._thread_state = threading.local()
        if set(self._models) != {"depth", "detect", "segment"}:
            raise ScanContractError("E_PROFILE_BUNDLE", "manifest must contain depth/detect/segment exactly once", "models")
        self._progress_frame: dict[str, Any] = {}
        if self.tof_decisions is None:
            self.tof_decisions = {}

    def _decision_for(self, frame: ValidatedFrame) -> dict[str, Any]:
        frame_id = str(frame.frame["frame_id"])
        decision = self.tof_decisions.get(frame_id) if self.tof_decisions else None
        if decision is not None:
            return decision
        count = formal_valid_count(frame.tof)
        return {
            "tof": frame.tof if count else None,
            "tof_status": "TOF_NORMAL" if count >= 8 else "TOF_DEGRADED" if count else "TOF_UNAVAILABLE",
            "degradation_mode": "NORMAL" if count >= 8 else "PARTIAL_ZONE_TOF" if count else "RGB_ONLY_GEOMETRY_UNAVAILABLE",
            "source": "current" if count else "none",
            "formal_valid_count": count,
            "confidence": float(np.sqrt(count / 64.0)) if count else 0.0,
            "reason_codes": [] if count >= 8 else ["sparse_formal_valid_zones"] if count else ["zero_formal_valid_tof", "no_safe_tof"],
            "borrowed_from_frame_id": None,
            "borrowed_age_ms": None,
            "pose_gate": None,
        }

    def _rgb_only_result(
        self,
        frame: ValidatedFrame,
        prepared: PreparedSource,
        cached: CachedYolo,
        decision: dict[str, Any],
        started: float,
    ) -> dict[str, Any]:
        frame_id = str(frame.frame["frame_id"])
        detections = cached.detect_document["instances"]
        segments = cached.segment_document["instances"]
        model_hashes = {task: self._models[task]["sha256"] for task in ("depth", "detect", "segment")}
        unavailable_depth = {
            "direct_640x360": np.zeros((360, 640), dtype=np.float32),
            "letterbox_640x480": np.zeros((360, 640), dtype=np.float32),
        }
        groups, public_groups, region_audit = build_logical_groups(
            frame_id, detections, segments, cached.masks, unavailable_depth,
            model_hashes, "segment_masks.npz",
            {name: "unavailable" for name in unavailable_depth},
            float(self.bundle.algorithm["detect_segment_iou_threshold"]),
            required_region_only=self.stage6_required_region_only,
        )
        objects: list[dict[str, Any]] = []
        for group in groups:
            selected_source = "segmentation" if group["segmentation_primary"].get("available") else "detection_fallback"
            formal_result = {
                "status": "rejected", "selected_source": selected_source,
                "selected_geometry": None, "canvas_selection_status": "both_rejected",
                "selected_canvas": None, "selection_is_provisional": False,
                "single_canvas_only": False, "automatic_geometry_valid": False,
                "absolute_geometry_valid": False, "fallback_used": False,
                "fallback_reason": "no_safe_tof", "critical_geometry_rejections": ["no_safe_tof"],
                "attempts": [{"source": "rgb_detection_only", "success": False,
                    "canvas_selection_status": "both_rejected", "selected_canvas": None,
                    "single_canvas_only": False, "automatic_geometry_valid": False,
                    "critical_geometry_rejections": ["no_safe_tof"]}],
                "warnings": ["W_TOF_UNAVAILABLE", "W_GEOMETRY_UNAVAILABLE"],
                "errors": [], "rejection_reason": "no_safe_tof",
            }
            objects.append(formal_result_to_observation(
                group, formal_result, float(self.bundle.algorithm["border_tolerance_px"])
            ))
        if self.all_object_geometry_processor is not None:
            self.all_object_geometry_processor.add_failed_frame_objects(
                frame_id, int(frame.frame["sequence"]), public_groups,
                "no_safe_tof", prepared.path,
            )
        valid_tof: list[float] = []
        result = {
            "schema_version": "deltar_scan_frame_result.v1.1",
            "status": "success",
            "frame_id": frame_id,
            "sequence": frame.frame["sequence"],
            "timestamps": {"camera_timestamp_us": frame.frame["camera_timestamp_us"], "tof_timestamp_us": frame.frame["tof_timestamp_us"], "pair_delta_us": frame.frame["pair_delta_us"]},
            "decoded_image": {"width": 640, "height": 360, "pixel_format": "RGB565_LE", "payload_bytes": 460800, "payload_sha256": sha256_file(frame.image_path)},
            "tof_summary": {"valid_zone_count": 0, "median_distance_mm": None},
            "models": {task: {"task": task, "sha256": self._models[task]["sha256"]} for task in ("depth", "detect", "segment")},
            "profiles": self.bundle.public_provenance(),
            "deltar_repeatability": {},
            "detections": {"detect_count": len(detections), "segment_count": len(segments),
                "matched_count": sum(group["match"]["status"] == "matched" for group in public_groups),
                "detect_instances": detections, "segment_instances": segments},
            "logical_object_groups": public_groups,
            "region_validation": region_audit,
            "objects": objects,
            "warnings": ["W_TOF_UNAVAILABLE", "W_DELTAR_UNAVAILABLE", "W_GEOMETRY_UNAVAILABLE"],
            "rejections": [item["frame_object_id"] for item in objects],
            "errors": [],
            "gt_used_for_inference": False,
            "processing_ms": {"decode_rgb565_ms": prepared.decode_rgb565_ms,
                "deltar_total_ms": 0.0, "yolo_ms": 0.0,
                "frame_total_ms": (time.perf_counter() - started) * 1000.0},
            "processing_status": {
                "capture_status": "CAPTURE_OK", "tof_status": "TOF_UNAVAILABLE",
                "deltar_status": "DELTAR_UNAVAILABLE", "detection_status": "DETECTION_OK",
                "geometry_status": "GEOMETRY_UNAVAILABLE",
                "degradation_mode": "RGB_ONLY_GEOMETRY_UNAVAILABLE",
                "reason_codes": list(decision["reason_codes"]),
                "tof_diagnostics": public_tof_decision(decision),
            },
        }
        validate_frame_result_v1_1(result)
        return result

    def _progress(self, subphase: str, status: str, *, started: float | None = None,
                  message: str = "", metrics: dict[str, Any] | None = None) -> None:
        if bool(getattr(self._thread_state, "suppress_progress", False)):
            return
        reporter = self.progress_reporter
        if reporter is None:
            return
        try:
            reporter.emit(
                "frame_processing", subphase, status,
                frame_id=self._progress_frame.get("frame_id"),
                frame_index=self._progress_frame.get("frame_index"),
                frame_total=self._progress_frame.get("frame_total"),
                current=self._progress_frame.get("frame_index"),
                total=self._progress_frame.get("frame_total"),
                elapsed_ms=0.0 if started is None else (time.perf_counter() - started) * 1000.0,
                message=message, metrics=metrics,
            )
        except Exception:
            pass

    def _pipeline_event(self, phase: str, subphase: str, status: str, *,
                        started: float | None = None, message: str = "",
                        metrics: dict[str, Any] | None = None) -> None:
        if self.progress_reporter is None:
            return
        try:
            self.progress_reporter.emit(
                phase, subphase, status,
                elapsed_ms=0.0 if started is None else (time.perf_counter() - started) * 1000.0,
                message=message, metrics=metrics,
            )
        except Exception:
            pass

    @property
    def bridge(self) -> DeltarBridge:
        if self._bridge is None:
            self._bridge = DeltarBridge(self.worktree, self.checkpoint, device=self.device)
        return self._bridge

    def _adapter_profile(self, canvas_strategy: str) -> dict[str, Any]:
        base = self.bundle.algorithm["deltar_adapter"]
        result = {
            "schema_version": "deltar_p4_profile.v1",
            "profile_id": f"stage6_{canvas_strategy}_v1",
            "sample_kind": "stage6_scan_frame",
            "image": {"width": 640, "height": 360, "stride": 1280, "pixel_format": "RGB565_LE"},
            "canvas": {"strategy": canvas_strategy, "fill_rgb": [0, 0, 0]},
            "histogram": {"mu_strategy": "distance_mm_to_m", "sigma_strategy": "range_sigma_mm_to_m"},
            "seed": int(base["seed"]),
            "formal_geometry_authority": self.formal_authority.provenance(),
        }
        return result

    def _run_depth(self, rgb: np.ndarray, tof: dict[str, Any]) -> tuple[dict[str, np.ndarray], dict[str, Any], dict[str, Any]]:
        depths: dict[str, np.ndarray] = {}
        repeatability: dict[str, Any] = {}
        timings: dict[str, Any] = {}
        for strategy in ("direct_640x360", "letterbox_640x480"):
            started = time.perf_counter()
            subphase = "deltar_direct" if strategy.startswith("direct") else "deltar_letterbox"
            label = "DELTAR直接深度推理" if strategy.startswith("direct") else "DELTAR letterbox深度推理"
            self._progress(subphase, "started", message=f"正在处理{label}")
            profile = self._adapter_profile(strategy)
            canvas = apply_canvas(rgb, strategy, [0, 0, 0])
            inputs = adapt_inputs(
                canvas.rgb,
                canvas,
                tof,
                profile,
                self.worktree,
                self.formal_authority,
                device=self.device,
                official_utils_cache=self._official_utils_cache,
            )
            inferred = self.bridge.infer_twice(inputs, profile["seed"])
            source, valid = map_depth_to_source(inferred.depth_first, canvas)
            source = source.copy()
            source[~valid] = 0.0
            depths[strategy] = source
            repeatability[strategy] = {
                **inferred.repeatability,
                "formal_d_fusion": inferred.fusion_audits,
                "model_timing_ms": inferred.timings,
                "gpu_peak_memory_mb": inferred.gpu_peak_bytes / (1024.0 * 1024.0),
            }
            timings[f"deltar_{'direct' if strategy.startswith('direct') else 'letterbox'}_ms"] = (time.perf_counter() - started) * 1000.0
            self._progress(subphase, "completed", started=started, message=f"{label}完成")
        return depths, repeatability, timings

    def prepare_depth_lookahead(
        self, frame: ValidatedFrame, token: DepthLookaheadToken
    ) -> PrefetchedDepth:
        frame_id = str(frame.frame["frame_id"])
        if token.frame_id != frame_id:
            raise FramePipelineError(
                "model_inference",
                "bounded DELTAR lookahead submit token mismatch",
                code="E_DELTAR_LOOKAHEAD_TOKEN",
            )
        self._thread_state.suppress_progress = True
        try:
            prepared = self._prepared_sources[frame_id]
            started = time.perf_counter()
            decision = self._decision_for(frame)
            if decision["tof"] is None:
                raise FramePipelineError(
                    "model_inference", "DELTAR lookahead skipped: ToF unavailable",
                    code="E_TOF_UNAVAILABLE",
                )
            depths, repeatability, timings = self._run_depth(prepared.rgb, decision["tof"])
            total_ms = (time.perf_counter() - started) * 1000.0
            for depth in depths.values():
                depth.flags.writeable = False
            stage7d_record = None
            if self.stage7d_lookahead_enabled:
                if self.stage7d_processor is None:
                    raise FramePipelineError(
                        "frame_processing",
                        "Stage 7D lookahead requires a Stage 7D processor",
                        code="E_PHASE6_STAGE7D_LOOKAHEAD_SCOPE",
                    )
                stage7d_record = self.stage7d_processor.process_frame(
                    frame_id,
                    prepared.rgb,
                    depths["direct_640x360"],
                    source_rgb_path=prepared.path,
                )
            return PrefetchedDepth(
                token=token,
                depths=depths,
                repeatability=repeatability,
                timings=timings,
                total_ms=total_ms,
                stage7d_record=stage7d_record,
            )
        except FramePipelineError:
            raise
        except Exception as exc:
            raise FramePipelineError.from_cause(
                "model_inference",
                f"DELTAR failed for {frame_id}: {type(exc).__name__}: {exc}",
                exc,
            ) from exc
        finally:
            self._thread_state.suppress_progress = False

    def prepare_scan_yolo(self, scan_id: str, frames: list[ValidatedFrame]) -> dict[str, Any]:
        if self.device != "cuda:0":
            raise FramePipelineError(
                "model_inference",
                f"Stage 8B-1 requires cuda:0; received {self.device!r}",
            )
        frame_ids = [str(frame.frame["frame_id"]) for frame in frames]
        sequences = [int(frame.frame["sequence"]) for frame in frames]
        if len(frame_ids) != len(set(frame_ids)):
            raise FramePipelineError("model_inference", "duplicate frame ids in scan")
        if sequences != sorted(sequences):
            raise FramePipelineError("model_inference", "validated frame order is not stable")
        if len(frames) == 11 and frame_ids != [
            f"frame_{index:06d}" for index in range(11)
        ]:
            raise FramePipelineError(
                "model_inference",
                "11-frame Stage 8B-1 order must be frame_000000..frame_000010",
            )

        source_paths: list[Path] = []
        source_pngs: list[tuple[Path, np.ndarray]] = []
        prepared: dict[str, PreparedSource] = {}
        decode_all_started = time.perf_counter()
        self._pipeline_event("pipeline_setup", "source_decode", "started", message="正在解码扫描源图像")
        try:
            for frame in frames:
                frame_id = str(frame.frame["frame_id"])
                decode_started = time.perf_counter()
                rgb = read_rgb565_le(frame.image_path)
                decode_ms = (time.perf_counter() - decode_started) * 1000.0
                source_dir = self.output_root / "logs" / frame_id
                source_dir.mkdir(parents=True, exist_ok=True)
                source_path = source_dir / "source_rgb.png"
                source_pngs.append((source_path, rgb))
                prepared[frame_id] = PreparedSource(rgb, source_path, decode_ms)
                source_paths.append(source_path)
            _write_source_pngs(source_pngs, self.source_png_workers)
        except Exception as exc:
            raise FramePipelineError(
                "model_inference",
                f"source RGB preparation failed: {type(exc).__name__}: {exc}",
            ) from exc
        self._pipeline_event(
            "pipeline_setup", "source_decode", "completed", started=decode_all_started,
            message="扫描源图像解码完成", metrics={"frame_count": len(frames)},
        )

        worker = Path(__file__).resolve().parents[2] / "workers" / "yolo_runtime_worker.py"
        batch_root = self.output_root / "stage8b1_yolo_batch_outputs"
        confidence = float(self.bundle.algorithm["yolo_confidence"])
        try:
            detect_started = time.perf_counter()
            self._pipeline_event("yolo", "detect", "started", message="正在执行YOLO检测批次")
            segment_started = detect_started
            self._pipeline_event("yolo", "segment", "started", message="正在执行YOLO分割批次")
            detect_outputs, segment_outputs, pair_timing = run_yolo_pair(
                self.yolo_python,
                worker,
                Path(self._models["detect"]["path"]),
                Path(self._models["segment"]["path"]),
                source_paths,
                batch_root,
                detect_sha256=self._models["detect"]["sha256"],
                segment_sha256=self._models["segment"]["sha256"],
                scan_id=scan_id,
                device="cuda:0",
                confidence=confidence,
                timeout_s=600,
                imgsz=640,
                batch_size=len(frames),
                half=False,
                require_cuda=True,
                expected_gpu_name=EXPECTED_GPU_NAME,
                required_class_ids=(BOOK_CLASS_ID,),
            )
            self._pipeline_event(
                "yolo", "detect", "completed", started=detect_started,
                message="YOLO检测批次完成", metrics={"frame_count": len(frames)},
            )
            self._pipeline_event(
                "yolo", "segment", "completed", started=segment_started,
                message="YOLO分割批次完成", metrics={"frame_count": len(frames)},
            )
        except Exception as exc:
            raise FramePipelineError(
                "model_inference",
                f"scan-level YOLO batch failed: {type(exc).__name__}: {exc}",
            ) from exc

        if len(detect_outputs) != len(frames) or len(segment_outputs) != len(frames):
            raise FramePipelineError(
                "model_inference", "scan-level YOLO output count mismatch"
            )
        cached: dict[str, CachedYolo] = {}
        for index, frame_id in enumerate(frame_ids):
            detect_document = detect_outputs[index]["document"]
            segment_document = segment_outputs[index]["document"]
            expected_path = str(source_paths[index].resolve()).casefold()
            actual_paths = (
                str(Path(detect_document["image"]["path"]).resolve()).casefold(),
                str(Path(segment_document["image"]["path"]).resolve()).casefold(),
            )
            if actual_paths != (expected_path, expected_path):
                raise FramePipelineError(
                    "model_inference",
                    f"cached YOLO path mismatch for {frame_id}",
                )
            cached[frame_id] = CachedYolo(
                detect_document,
                segment_document,
                segment_outputs[index]["masks"],
            )
        if list(cached) != frame_ids:
            raise FramePipelineError(
                "model_inference", "cached YOLO frame order mismatch"
            )

        detect_first = detect_outputs[0]
        segment_first = segment_outputs[0]
        detect_total_wall_ms = float(
            detect_first["worker_timing"]["predict_and_postprocess_ms"]
            + detect_first["worker_timing"]["serialize_ms"]
        )
        segment_total_wall_ms = float(
            segment_first["worker_timing"]["predict_and_postprocess_ms"]
            + segment_first["worker_timing"]["serialize_ms"]
        )
        combined_yolo_wall_ms = float(
            pair_timing["invocation_wall_ms"]
            + detect_first["parent_reload_ms"]
            + segment_first["parent_reload_ms"]
        )
        timing = {
            "schema_version": "stage8b1_yolo_batch_timing.v1",
            "scan_id": scan_id,
            "detect": detect_first["worker_timing"],
            "segment": segment_first["worker_timing"],
            "parent_reload_ms": {
                "detect": detect_first["parent_reload_ms"],
                "segment": segment_first["parent_reload_ms"],
            },
            "detect_total_wall_ms": detect_total_wall_ms,
            "segment_total_wall_ms": segment_total_wall_ms,
            "combined_yolo_wall_ms": combined_yolo_wall_ms,
            "lifecycle": pair_timing,
            "gpu_peak_memory_mb": max(
                float(detect_first["worker_timing"]["gpu_peak_memory_mb"]),
                float(segment_first["worker_timing"]["gpu_peak_memory_mb"]),
            ),
        }
        manifest = {
            "schema_version": "stage8b1_yolo_batch_manifest.v1",
            "scan_id": scan_id,
            "ordered_frame_ids": frame_ids,
            "ordered_source_paths": [str(path.resolve()) for path in source_paths],
            "detect_model_path": str(Path(self._models["detect"]["path"]).resolve()),
            "detect_model_sha256": self._models["detect"]["sha256"],
            "segment_model_path": str(Path(self._models["segment"]["path"]).resolve()),
            "segment_model_sha256": self._models["segment"]["sha256"],
            "worker_python": str(self.yolo_python.resolve()),
            "runtime": detect_first["worker_metadata"],
            "device": "cuda:0",
            "precision": "FP32",
            "half": False,
            "imgsz": 640,
            "requested_batch_size": len(frames),
            "actual_sub_batches": {
                "detect": detect_first["actual_sub_batches"],
                "segment": segment_first["actual_sub_batches"],
            },
            "worker_invocations": {"detect": 1, "segment": 1},
            "worker_process_invocations": 1,
            "runtime_mode": pair_timing["mode"],
            "runtime_identity": pair_timing["runtime"],
            "model_load_counts": {
                "detect": detect_first["worker_metadata"]["model_load_count"],
                "segment": segment_first["worker_metadata"]["model_load_count"],
            },
            "detect_output_by_frame": {
                frame_id: str(detect_outputs[index]["json_path"])
                for index, frame_id in enumerate(frame_ids)
            },
            "segment_output_by_frame": {
                frame_id: {
                    "json": str(segment_outputs[index]["json_path"]),
                    "masks": str(segment_outputs[index]["npz_path"]),
                }
                for index, frame_id in enumerate(frame_ids)
            },
            "book_class_id": BOOK_CLASS_ID,
            "book_class_name": detect_first["document"]["model"]["class_names"][
                str(BOOK_CLASS_ID)
            ],
            "segment_all_frames": True,
            "detect_gates_segment": False,
        }
        atomic_write_json(
            self.output_root / "stage8b1_yolo_batch_manifest.json", manifest
        )
        atomic_write_json(
            self.output_root / "stage8b1_yolo_batch_timing.json", timing
        )
        self._prepared_sources = prepared
        self._cached_yolo = cached
        return {"manifest": manifest, "timing": timing}

    def process(
        self,
        frame: ValidatedFrame,
        *,
        frame_index: int | None = None,
        frame_total: int | None = None,
        prefetched_depth: PrefetchedDepth | None = None,
        expected_lookahead_token: DepthLookaheadToken | None = None,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        frame_id = frame.frame["frame_id"]
        self._progress_frame = {
            "frame_id": frame_id,
            "frame_index": frame_index if frame_index is not None else int(frame.frame["sequence"]) + 1,
            "frame_total": frame_total,
        }
        timing: dict[str, float] = {}
        input_started = time.perf_counter()
        self._progress("input_prepare", "started", message="正在准备当前帧输入")
        try:
            prepared = self._prepared_sources[frame_id]
            cached = self._cached_yolo[frame_id]
            rgb = prepared.rgb
            source_dir = prepared.path.parent
            timing["decode_rgb565_ms"] = prepared.decode_rgb565_ms
            self._progress("input_prepare", "completed", started=input_started, message="当前帧输入准备完成")
            tof_decision = self._decision_for(frame)
            if tof_decision["tof"] is None:
                return self._rgb_only_result(frame, prepared, cached, tof_decision, started)
            selected_tof = tof_decision["tof"]
            if prefetched_depth is None:
                if expected_lookahead_token is not None:
                    raise FramePipelineError(
                        "model_inference",
                        "bounded DELTAR lookahead result is missing",
                        code="E_DELTAR_LOOKAHEAD_TOKEN",
                    )
                depth_started = time.perf_counter()
                depths, repeatability, depth_timing = self._run_depth(rgb, selected_tof)
                timing.update(depth_timing)
                timing["deltar_total_ms"] = (
                    time.perf_counter() - depth_started
                ) * 1000.0
            else:
                if (
                    expected_lookahead_token is None
                    or prefetched_depth.token != expected_lookahead_token
                    or expected_lookahead_token.frame_id != frame_id
                ):
                    raise FramePipelineError(
                        "model_inference",
                        "bounded DELTAR lookahead merge token mismatch",
                        code="E_DELTAR_LOOKAHEAD_TOKEN",
                    )
                if any(
                    depth.flags.writeable
                    for depth in prefetched_depth.depths.values()
                ):
                    raise FramePipelineError(
                        "model_inference",
                        "bounded DELTAR lookahead depth is not immutable",
                        code="E_DELTAR_LOOKAHEAD_MUTABLE",
                    )
                depths = prefetched_depth.depths
                repeatability = prefetched_depth.repeatability
                timing.update(prefetched_depth.timings)
                timing["deltar_total_ms"] = prefetched_depth.total_ms
        except Exception as exc:
            raise FramePipelineError.from_cause(
                "model_inference",
                f"DELTAR failed for {frame_id}: {type(exc).__name__}: {exc}",
                exc,
            ) from exc
        stage7d_record: dict[str, Any] | None = None
        if self.stage7d_processor is not None:
            support_started = time.perf_counter()
            self._progress("support_plane", "started", message="正在计算支撑面")
            try:
                if prefetched_depth is not None and self.stage7d_lookahead_enabled:
                    if prefetched_depth.stage7d_record is None:
                        raise FramePipelineError(
                            "frame_processing",
                            "bounded Stage 7D lookahead result is missing",
                            code="E_PHASE6_STAGE7D_LOOKAHEAD_STATE",
                        )
                    stage7d_record = prefetched_depth.stage7d_record
                else:
                    stage7d_record = self.stage7d_processor.process_frame(
                        frame_id,
                        rgb,
                        depths["direct_640x360"],
                        source_rgb_path=prepared.path,
                    )
                stage7d_timing = dict(stage7d_record["_stage7d_timing"])
                timing.update(stage7d_timing)
                self._progress(
                    "support_plane",
                    "completed",
                    started=support_started,
                    message="支撑面计算完成",
                    metrics={
                        **stage7d_timing,
                        "deferred_or_disabled_diagnostics": (
                            "enabled"
                            if stage7d_timing["stage7d_diagnostics_enabled"]
                            else "disabled"
                        ),
                    },
                )
            except Exception as exc:
                raise FramePipelineError(
                    "frame_processing",
                    f"Stage 7D failed for {frame_id}: {type(exc).__name__}: {exc}",
                ) from exc
        try:
            geometry_phase_started = time.perf_counter()
            self._progress("frame_object_geometry", "started", message="正在计算对象几何")
            cache_started = time.perf_counter()
            detect_doc = cached.detect_document
            segment_doc = cached.segment_document
            masks = cached.masks
            timing["yolo_ms"] = 0.0
            timing["yolo_cache_lookup_ms"] = (
                time.perf_counter() - cache_started
            ) * 1000.0
            detections = detect_doc["instances"]
            segments = segment_doc["instances"]
            depth_refs = {
                canvas_name: f"{canvas_name}.npy" for canvas_name in depths
            }
            model_hashes = {task: self._models[task]["sha256"] for task in ("depth", "detect", "segment")}
            groups, public_groups, region_audit = build_logical_groups(
                frame_id, detections, segments, masks, depths, model_hashes, "segment_masks.npz", depth_refs,
                float(self.bundle.algorithm["detect_segment_iou_threshold"]),
                required_region_only=self.stage6_required_region_only,
            )
            matched_count = sum(group["match"]["status"] == "matched" for group in public_groups)
            if self.all_object_geometry_processor is not None:
                if stage7d_record is None:
                    raise ValueError(
                        "all-object geometry requires the same-frame Stage 7D result"
                    )
                geometry_started = time.perf_counter()
                self._progress("all_object_accumulate", "started", message="正在累积全对象几何")
                selected_depth = stage7d_record["_formal_geometry_selected"]
                geometry_frame = self.all_object_geometry_processor.process_frame(
                    frame_id,
                    int(frame.frame["sequence"]),
                    public_groups,
                    masks,
                    selected_depth["camera_axial_z_mm"],
                    selected_depth["world_points_mm"],
                    list(selected_depth.get("support_planes", [])),
                    prepared.path,
                )
                authority_by_id = {
                    item["logical_object_id"]: item for item in geometry_frame["objects"]
                }
                objects = []
                for group in groups:
                    authority_item = authority_by_id[group["logical_object_id"]]
                    geometry_success = authority_item.get("numeric_available") is True
                    geometry = authority_item.get("geometry") or {}
                    point_count = int(
                        authority_item.get("diagnostics", {}).get("point_count", 0)
                    )
                    formal_result = {
                        "status": "success" if geometry_success else "rejected",
                        "selected_source": authority_item.get("selected_source"),
                        "selected_geometry": (
                            {
                                "distance_m": float(geometry["distance_mm"]) / 1000.0,
                                "object_height_m": float(geometry["object_height_mm"]) / 1000.0,
                                "bottom_height_m": float(geometry["support_height_mm"]) / 1000.0,
                                "point_count": point_count,
                                "quality_inputs": {
                                    "bottom_support": point_count,
                                    "mask_support": point_count,
                                    "depth_iqr": float(
                                        authority_item.get("diagnostics", {}).get(
                                            "depth_iqr_mm", 0.0
                                        )
                                    )
                                    / 1000.0,
                                },
                            }
                            if geometry_success
                            else None
                        ),
                        "canvas_selection_status": (
                            "agreed" if geometry_success else "both_rejected"
                        ),
                        "selected_canvas": "direct_640x360" if geometry_success else None,
                        "selection_is_provisional": False,
                        "single_canvas_only": False,
                        "automatic_geometry_valid": geometry_success,
                        "absolute_geometry_valid": geometry_success,
                        "fallback_used": False,
                        "fallback_reason": None,
                        "critical_geometry_rejections": (
                            []
                            if geometry_success
                            else [str(authority_item.get("failure_reason") or "v2_failure")]
                        ),
                        "attempts": [
                            {
                                "source": "all_object_geometry_v2",
                                "success": geometry_success,
                                "canvas_selection_status": (
                                    "agreed" if geometry_success else "both_rejected"
                                ),
                                "selected_canvas": (
                                    "direct_640x360" if geometry_success else None
                                ),
                                "single_canvas_only": False,
                                "automatic_geometry_valid": geometry_success,
                                "critical_geometry_rejections": (
                                    []
                                    if geometry_success
                                    else [
                                        str(
                                            authority_item.get("failure_reason")
                                            or "v2_failure"
                                        )
                                    ]
                                ),
                            }
                        ],
                        "warnings": [],
                        "errors": [],
                        "rejection_reason": (
                            None
                            if geometry_success
                            else str(authority_item.get("failure_reason") or "v2_failure")
                        ),
                    }
                    item = formal_result_to_observation(
                        group,
                        formal_result,
                        float(self.bundle.algorithm["border_tolerance_px"]),
                    )
                    formal_provenance = {
                        **item["formal_geometry"]["provenance"],
                        "stage4_function": "all_object_geometry.measure_logical_object_v2",
                    }
                    item["formal_geometry"]["provenance"] = formal_provenance
                    item["provenance"]["stage4_function"] = (
                        "all_object_geometry.measure_logical_object_v2"
                    )
                    if not geometry_success:
                        objects.append(item)
                        continue
                    distance_m = float(geometry["distance_mm"]) / 1000.0
                    height_m = float(geometry["object_height_mm"]) / 1000.0
                    support_m = float(geometry["support_height_mm"]) / 1000.0
                    item["distance_m"] = distance_m
                    item["distance_semantics"] = "camera_axial_z_median"
                    item["geometry_point_count"] = point_count
                    item["formal_geometry"].update(
                        {
                            "status": "success",
                            "canvas_selection_status": "agreed",
                            "selected_canvas": "direct_640x360",
                            "selection_is_provisional": False,
                            "single_canvas_only": False,
                            "automatic_geometry_valid": True,
                            "absolute_geometry_valid": True,
                            "fallback_used": False,
                            "fallback_reason": None,
                            "critical_geometry_rejections": [],
                            "selected_geometry": {
                                "distance_m": distance_m,
                                "object_height_m": height_m,
                                "bottom_height_m": support_m,
                                "point_count": item["geometry_point_count"],
                                "mask_support": item["geometry_point_count"],
                            },
                        }
                    )
                    for name, value in (
                        ("visible_height", height_m),
                        ("physical_height", height_m),
                        ("bottom_height", support_m),
                    ):
                        item[name] = {
                            "value_m": value,
                            "available": True,
                            "absolute_valid": True,
                        }
                    item["visibility"].update(
                        {
                            "physical_height_valid": True,
                            "bottom_height_valid": True,
                            "physical_height_m": height_m,
                            "bottom_height_m": support_m,
                        }
                    )
                    item["warnings"] = [
                        warning
                        for warning in item["warnings"]
                        if warning != "W_UNMEASURED_CALIBRATION"
                    ]
                    objects.append(item)
                timing["all_object_geometry_ms"] = (
                    time.perf_counter() - geometry_started
                ) * 1000.0
                self._progress(
                    "all_object_accumulate", "completed", started=geometry_started,
                    message="当前帧全对象几何累积完成",
                )
            else:
                raise ValueError("formal all-object Geometry v2 processor is required")
            self._progress(
                "frame_object_geometry", "completed", started=geometry_phase_started,
                message="对象几何计算完成", metrics={"object_count": len(objects)},
            )
        except Exception as exc:
            raise FramePipelineError("frame_processing", f"YOLO/Stage 3.1/4.1 failed for {frame_id}: {type(exc).__name__}: {exc}") from exc
        valid_tof = [float(value) for value, status, count in zip(selected_tof["distance_mm"], selected_tof["target_status"], selected_tof["nb_target_detected"]) if count > 0 and status in {5, 6, 9, 10}]
        timing["frame_total_ms"] = (time.perf_counter() - started) * 1000.0
        result = {
            "schema_version": "deltar_scan_frame_result.v1.1",
            "status": "success",
            "frame_id": frame_id,
            "sequence": frame.frame["sequence"],
            "timestamps": {"camera_timestamp_us": frame.frame["camera_timestamp_us"], "tof_timestamp_us": frame.frame["tof_timestamp_us"], "pair_delta_us": frame.frame["pair_delta_us"]},
            "decoded_image": {"width": 640, "height": 360, "pixel_format": "RGB565_LE", "payload_bytes": 460800, "payload_sha256": sha256_file(frame.image_path)},
            "tof_summary": {"valid_zone_count": len(valid_tof), "median_distance_mm": float(np.median(valid_tof)) if valid_tof else None},
            "models": {task: {"task": task, "sha256": self._models[task]["sha256"]} for task in ("depth", "detect", "segment")},
            "profiles": self.bundle.public_provenance(),
            "deltar_repeatability": repeatability,
            "detections": {"detect_count": len(detections), "segment_count": len(segments), "matched_count": matched_count,
                           "detect_instances": detections, "segment_instances": segments},
            "logical_object_groups": public_groups,
            "region_validation": region_audit,
            "objects": objects,
            "warnings": sorted(set(warning for item in objects for warning in item["warnings"])),
            "rejections": [item["frame_object_id"] for item in objects if item["rejection_reason"]],
            "errors": [],
            "gt_used_for_inference": False,
            "processing_ms": timing,
            "processing_status": {
                "capture_status": "CAPTURE_OK",
                "tof_status": str(tof_decision["tof_status"]),
                "deltar_status": "DELTAR_OK",
                "detection_status": "DETECTION_OK",
                "geometry_status": (
                    "GEOMETRY_UNAVAILABLE"
                    if geometry_frame["objects"] and not any(
                        item.get("numeric_available") is True
                        for item in geometry_frame["objects"]
                    )
                    else "GEOMETRY_DEGRADED"
                    if tof_decision["tof_status"] != "TOF_NORMAL" or any(
                        item.get("geometry_status") != "ok"
                        for item in geometry_frame["objects"]
                    )
                    else "GEOMETRY_OK"
                ),
                "degradation_mode": str(tof_decision["degradation_mode"]),
                "reason_codes": list(tof_decision["reason_codes"]),
                "tof_diagnostics": public_tof_decision(tof_decision),
            },
        }
        diagnostic_audit = normalize_frame_diagnostics(result)
        if diagnostic_audit:
            with (self.output_root / "logs" / "frame_diagnostics.jsonl").open(
                "a", encoding="utf-8", newline="\n"
            ) as handle:
                handle.write(json.dumps({
                    "frame_id": frame_id,
                    "sequence": frame.frame["sequence"],
                    "diagnostics": diagnostic_audit,
                }, ensure_ascii=False, sort_keys=True) + "\n")
        validate_frame_result_v1_1(result)
        return result
