from __future__ import annotations

import json
from pathlib import Path

from scripts.repair_visualization_asset_refs import MODE, repair


def _legacy_viewer(scan_root: Path, scan_id: str) -> Path:
    attempt = scan_root.parent / ".frozen_attempts" / scan_id / "generation_0001_attempt_1"
    frames = []
    images = []
    for sequence in range(11):
        frame_id = f"frame_{sequence:06d}"
        canonical = scan_root / "logs" / frame_id / "source_rgb.png"
        canonical.parent.mkdir(parents=True, exist_ok=True)
        canonical.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
        legacy = attempt / "logs" / frame_id / "source_rgb.png"
        frames.append({
            "frame_id": frame_id,
            "source_image_path": str(legacy),
            "source_image_uri": legacy.as_uri(),
        })
        images.append(f'<img src="{legacy.as_uri()}" alt="{frame_id} source image">')
    scene = json.dumps({"frames": frames}, ensure_ascii=False, separators=(",", ":"))
    html = scan_root / "visualization" / "index.html"
    html.parent.mkdir()
    html.write_text(
        '<html><head><meta charset="utf-8"></head><body>'
        + "".join(images)
        + f"<script>const SCENE={scene};const panel=null;</script></body></html>",
        encoding="utf-8",
    )
    return html


def test_named_repair_dry_run_apply_and_idempotency(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    scan_id = "scan_repair"
    html = _legacy_viewer(data_root / "results" / scan_id, scan_id)
    before = html.read_bytes()

    dry_run = repair(scan_id, data_root, apply=False)
    assert dry_run["changed"] is True
    assert html.read_bytes() == before

    applied = repair(scan_id, data_root, apply=True)
    assert applied["changed"] is True
    repaired = html.read_text(encoding="utf-8")
    assert repaired.count("../logs/frame_") == 11
    assert "file:///" not in repaired
    assert ".frozen_attempts" not in repaired
    assert "source_image_path" not in repaired
    assert f'"viewer_asset_mode":"{MODE}"' in repaired

    second = repair(scan_id, data_root, apply=True)
    assert second["changed"] is False
    assert second["before_sha256"] == second["after_sha256"] == applied["after_sha256"]
