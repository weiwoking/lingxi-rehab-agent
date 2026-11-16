from __future__ import annotations

import argparse
import json
from pathlib import Path

from deltar_p4_pipeline.yolo_worker_client import run_yolo_worker

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "workers" / "yolo_worker.py"
PYTHON = Path(r"C:\ec\_deltar_yolo_cuda\venv\Scripts\python.exe")
DETECT = Path(r"C:\ec\esp-claw\pc\vision_interpreter\models\yolo11n.pt")
SEGMENT = ROOT / "models" / "yolo11n-seg.pt"
DETECT_SHA256 = "0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1"
SEGMENT_SHA256 = "55ed65c56c91713d23e8402371c6c49a6fd84f257f7dce452e8d70e41dcbe152"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise SystemExit(f"refusing to overwrite {args.output_dir}")
    args.output_dir.mkdir(parents=True)

    results = {}
    for task, model, expected_hash in (
        ("detect", DETECT, DETECT_SHA256),
        ("segment", SEGMENT, SEGMENT_SHA256),
    ):
        output = run_yolo_worker(
            PYTHON,
            WORKER,
            model,
            task,
            [args.source],
            args.output_dir / task,
            expected_sha256=expected_hash,
            device="cuda:0",
            confidence=0.25,
            imgsz=640,
            batch_size=1,
            half=False,
            require_cuda=True,
            expected_gpu_name="RTX 5070 Ti",
            required_class_ids=(73,),
            timeout_s=600,
        )[0]
        results[task] = {
            "source": output["document"]["image"]["path"],
            "output_count": 1,
            "instance_count": len(output["document"]["instances"]),
            "model_sha256": output["document"]["model"]["sha256"],
            "book_class_name": output["document"]["model"]["class_names"]["73"],
            "metadata": output["worker_metadata"],
            "timing": output["worker_timing"],
            "actual_sub_batches": output["actual_sub_batches"],
            "worker_invocation_wall_ms": output["worker_invocation_wall_ms"],
            "parent_reload_ms": output["parent_reload_ms"],
        }
    summary = {
        "schema_version": "stage8b1_gpu_smoke.v1",
        "source": str(args.source.resolve()),
        "detect": results["detect"],
        "segment": results["segment"],
        "no_cpu_fallback": all(
            item["metadata"]["device"] == "cuda:0"
            and item["metadata"]["actual_model_device"] == "cuda:0"
            and item["metadata"]["cuda_available"] is True
            for item in results.values()
        ),
    }
    (args.output_dir / "stage8b1_gpu_smoke.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
