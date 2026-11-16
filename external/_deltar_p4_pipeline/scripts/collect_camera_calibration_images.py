from __future__ import annotations

import argparse
import hashlib
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll the existing camera-preview HTTP image without changing P4")
    parser.add_argument("--url", required=True, help="Existing camera preview JPG URL")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--interval-seconds", type=float, default=2.0)
    args = parser.parse_args()
    if not 1 <= args.count <= 200 or args.interval_seconds < 0.5:
        parser.error("count must be 1..200 and interval must be >= 0.5 seconds")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for index in range(1, args.count + 1):
        with urllib.request.urlopen(args.url, timeout=15) as response:
            payload = response.read(5 * 1024 * 1024 + 1)
        if len(payload) > 5 * 1024 * 1024:
            raise RuntimeError("camera response exceeds 5 MiB")
        path = args.output_dir / f"charuco_{index:03d}.jpg"
        path.write_bytes(payload)
        with Image.open(path) as image:
            if image.size != (640, 360):
                path.unlink(missing_ok=True)
                raise RuntimeError(f"{path.name}: expected 640x360, got {image.size}")
        digest = hashlib.sha256(payload).hexdigest()
        timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        print(f"{index:03d} {timestamp} {path.name} sha256={digest}", flush=True)
        if index != args.count:
            time.sleep(args.interval_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
