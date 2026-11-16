"""Submit the retained G001 scan through the production HTTP/worker boundary."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import BinaryIO

import requests


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser()
    value.add_argument(
        "--scan-dir",
        type=Path,
        default=Path(
            r"C:\ec\_deltar_scan_service_data\validated\scan_20260818_092411_000"
        ),
    )
    value.add_argument("--base-url", default="http://127.0.0.1:8030")
    value.add_argument("--timeout-seconds", type=float, default=900.0)
    value.add_argument(
        "--replay-scan-id",
        help="submit identical retained bytes under a fresh scan id for a real non-idempotent replay",
    )
    value.add_argument("--summary-only", action="store_true")
    return value


def main() -> int:
    args = parser().parse_args()
    scan_dir = args.scan_dir.resolve()
    session = requests.Session()
    session.trust_env = False
    opened: list[BinaryIO] = []
    files: dict[str, tuple[str, BinaryIO, str]] = {}

    def add(field: str, path: Path, content_type: str) -> None:
        handle = path.open("rb")
        opened.append(handle)
        files[field] = (path.name, handle, content_type)

    try:
        if args.replay_scan_id:
            scan = json.loads((scan_dir / "scan.json").read_text(encoding="utf-8"))
            scan["scan_id"] = args.replay_scan_id
            files["scan_json"] = (
                "scan.json",
                json.dumps(scan, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                "application/json",
            )
        else:
            add("scan_json", scan_dir / "scan.json", "application/json")
        for index in range(11):
            frame = scan_dir / "frames" / f"frame_{index:06d}"
            prefix = f"frame_{index:06d}"
            add(f"{prefix}_frame_json", frame / "frame.json", "application/json")
            add(f"{prefix}_tof_json", frame / "tof.json", "application/json")
            add(
                f"{prefix}_image_rgb565",
                frame / "image.rgb565",
                "application/octet-stream",
            )
        submitted_at = time.perf_counter()
        response = session.post(
            f"{args.base_url}/api/v1/scan/jobs?mode=full_pipeline",
            files=files,
            timeout=60,
        )
        if not response.ok:
            raise RuntimeError(
                f"G001 upload rejected: HTTP {response.status_code}: "
                f"{response.text[:2000]}"
            )
        job = response.json()
    finally:
        for handle in opened:
            handle.close()

    deadline = time.monotonic() + args.timeout_seconds
    history: list[dict[str, object]] = []
    last_status = None
    while time.monotonic() < deadline:
        response = session.get(
            f"{args.base_url}{job['status_url']}",
            timeout=10,
        )
        response.raise_for_status()
        current = response.json()
        if current.get("status") != last_status:
            last_status = current.get("status")
            history.append(
                {
                    "status": last_status,
                    "observed_after_seconds": time.perf_counter() - submitted_at,
                }
            )
        if last_status in {"completed", "failed"}:
            break
        time.sleep(0.5)
    else:
        raise TimeoutError("G001 service replay did not reach a terminal state")

    package_response = session.get(
        f"{args.base_url}/api/v1/scans/{job['scan_id']}/decision-package",
        timeout=10,
    )
    package = package_response.json() if package_response.ok else None
    result_response = session.get(
        f"{args.base_url}{job['result_url']}",
        timeout=10,
    )
    output = {
        "submitted_scan_dir": str(scan_dir),
        "base_url": args.base_url,
        "job": current,
        "history": history,
        "elapsed_seconds": time.perf_counter() - submitted_at,
        "package_http_status": package_response.status_code,
        "package": package,
        "result_http_status": result_response.status_code,
        "result": result_response.json() if result_response.ok else None,
    }
    if args.summary_only:
        result = output["result"] if isinstance(output.get("result"), dict) else {}
        statuses = result.get("frame_processing_status", [])
        compact = {
            "scan_id": current["scan_id"],
            "status": last_status,
            "pipeline_result_status": current.get("pipeline_result_status"),
            "elapsed_seconds": output["elapsed_seconds"],
            "package_http_status": output["package_http_status"],
            "result_http_status": output["result_http_status"],
            "frame_status_count": len(statuses),
            "captured_rgb_count": sum(item.get("capture_status") == "CAPTURE_OK" for item in statuses),
            "tof_unavailable_count": sum(item.get("tof_status") == "TOF_UNAVAILABLE" for item in statuses),
            "geometry_unavailable_count": sum(item.get("geometry_status") == "GEOMETRY_UNAVAILABLE" for item in statuses),
            "rejected_frame_count": (result.get("input") or {}).get("rejected_frame_count"),
            "visualization_html_path": current.get("visualization_html_path"),
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if last_status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
