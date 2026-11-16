from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    from ultralytics import YOLO, __version__
    root = Path(__file__).resolve().parents[1]
    models = root / "models"; models.mkdir(parents=True, exist_ok=True)
    old = Path.cwd()
    try:
        os.chdir(models)
        model = YOLO("yolo11n-seg.pt")
    finally:
        os.chdir(old)
    path = (models / "yolo11n-seg.pt").resolve()
    if model.task != "segment" or not path.is_file():
        raise RuntimeError("official segmentation model did not load as task=segment")
    manifest = {"schema_version": "deltar_stage3_model_manifest.v1", "models": [{"name": path.name, "path": str(path),
        "size_bytes": path.stat().st_size, "sha256": sha256(path), "task": model.task, "source": "Ultralytics official auto-download",
        "download_command": "vision_interpreter/python.exe scripts/download_stage3_models.py", "ultralytics_version": __version__,
        "class_names": {str(k): v for k,v in model.names.items()}, "license": "Ultralytics AGPL-3.0; research PoC asset; no independent commercial-license conclusion"}]}
    (models / "MODEL_MANIFEST.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
