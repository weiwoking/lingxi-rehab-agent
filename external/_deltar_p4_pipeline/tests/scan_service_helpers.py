from __future__ import annotations

import json
import shutil
from pathlib import Path


def copy_scan(source: Path, target: Path, *, frame_count: int = 1, scan_id: str = "scan_service_test") -> Path:
    target.mkdir(parents=True)
    scan = json.loads((source / "scan.json").read_text(encoding="utf-8"))
    scan.update({"scan_id": scan_id, "frame_count": frame_count,
                 "capture_mode": "single_frame" if frame_count == 1 else "multi_frame"})
    (target / "scan.json").write_text(json.dumps(scan), encoding="utf-8")
    for index in range(frame_count):
        source_frame = source / "frames" / f"frame_{index:06d}"
        if not source_frame.is_dir():
            source_frame = source / "frames" / "frame_000000"
        shutil.copytree(source_frame, target / "frames" / f"frame_{index:06d}")
        for name in ("frame.json", "tof.json"):
            path = target / "frames" / f"frame_{index:06d}" / name
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["sequence"] = index
            if name == "frame.json":
                payload["frame_id"] = f"frame_{index:06d}"
            path.write_text(json.dumps(payload), encoding="utf-8")
    return target


def multipart_bytes(scan_dir: Path) -> list[tuple[str, tuple[str, bytes, str]]]:
    scan = json.loads((scan_dir / "scan.json").read_text(encoding="utf-8"))
    rows: list[tuple[str, tuple[str, bytes, str]]] = [
        ("scan_json", ("scan.json", (scan_dir / "scan.json").read_bytes(), "application/json"))
    ]
    for index in range(scan["frame_count"]):
        folder = scan_dir / "frames" / f"frame_{index:06d}"
        prefix = f"frame_{index:06d}_"
        rows.extend([
            (prefix + "frame_json", ("frame.json", (folder / "frame.json").read_bytes(), "application/json")),
            (prefix + "tof_json", ("tof.json", (folder / "tof.json").read_bytes(), "application/json")),
            (prefix + "image_rgb565", ("image.rgb565", (folder / "image.rgb565").read_bytes(), "application/octet-stream")),
        ])
    return rows
