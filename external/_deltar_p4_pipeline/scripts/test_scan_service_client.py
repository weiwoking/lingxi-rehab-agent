from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import httpx


def multipart(scan_dir: Path) -> tuple[list[tuple[str, tuple[str, object, str]]], list[object]]:
    scan = json.loads((scan_dir / "scan.json").read_text(encoding="utf-8"))
    files: list[tuple[str, tuple[str, object, str]]] = []
    handles: list[object] = []

    def add(field: str, path: Path, content_type: str) -> None:
        handle = path.open("rb")
        handles.append(handle)
        files.append((field, (path.name, handle, content_type)))

    add("scan_json", scan_dir / "scan.json", "application/json")
    for index in range(scan["frame_count"]):
        frame = scan_dir / "frames" / f"frame_{index:06d}"
        prefix = f"frame_{index:06d}_"
        add(prefix + "frame_json", frame / "frame.json", "application/json")
        add(prefix + "tof_json", frame / "tof.json", "application/json")
        add(prefix + "image_rgb565", frame / "image.rgb565", "application/octet-stream")
    return files, handles


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload, poll, and download one frozen scan")
    parser.add_argument("--base-url", default="http://127.0.0.1:8030")
    parser.add_argument("--scan-dir", type=Path, required=True)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=180.0)
    args = parser.parse_args()
    files, handles = multipart(args.scan_dir)
    try:
        with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
            response = client.post(
                "/api/v1/scan/jobs", files=files,
                headers={"X-VisionPC-Job-Role": "test"},
            )
            response.raise_for_status()
            job = response.json()
            deadline = time.monotonic() + args.timeout_seconds
            while job["status"] not in {"completed", "failed"} and time.monotonic() < deadline:
                time.sleep(args.poll_seconds)
                job = client.get(job["status_url"]).json()
            if job["status"] != "completed":
                raise RuntimeError(f"job did not complete: {job}")
            result_response = client.get(job["result_url"])
            result_response.raise_for_status()
            raw = result_response.content
            result = json.loads(raw.decode("utf-8"))
            if result["scan_id"] != job["scan_id"] or result["job_id"] != job["pipeline_job_id"]:
                raise RuntimeError("downloaded result identifiers do not match status")
            print(json.dumps({"status": "pass", "service_job_id": job["service_job_id"],
                              "pipeline_job_id": job["pipeline_job_id"], "result_status": result["status"],
                              "result_bytes": len(raw)}, ensure_ascii=False))
    finally:
        for handle in handles:
            handle.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
