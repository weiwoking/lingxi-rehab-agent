from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any


MODE = "portable_relative_v1"
MODE_META = f'<meta name="viewer-asset-mode" content="{MODE}">'
SCENE_RE = re.compile(r"const SCENE=(\{.*?\});const panel=", re.DOTALL)
FRAME_IDS = tuple(f"frame_{index:06d}" for index in range(11))


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _legacy_path(value: str) -> Path:
    if value.startswith("file:///"):
        parsed = urllib.parse.urlsplit(value)
        raw = urllib.parse.unquote(parsed.path)
        if re.match(r"^/[A-Za-z]:/", raw):
            raw = raw[1:]
        return Path(raw.replace("/", "\\"))
    return Path(value)


def _validate_legacy_ref(value: Any, scan_id: str, frame_id: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{frame_id} has no legacy source reference")
    path = _legacy_path(value)
    lowered = {part.lower() for part in path.parts}
    if (
        not path.is_absolute()
        or path.name.lower() != "source_rgb.png"
        or path.parent.name != frame_id
        or path.parent.parent.name.lower() != "logs"
        or ".frozen_attempts" not in lowered
        or scan_id not in path.parts
    ):
        raise ValueError(f"{frame_id} reference is not a canonical frozen-attempt source")


def _canonical_sources(scan_root: Path) -> dict[str, Path]:
    root = scan_root.resolve()
    sources: dict[str, Path] = {}
    for frame_id in FRAME_IDS:
        frame_root = (root / "logs" / frame_id).resolve()
        source = (frame_root / "source_rgb.png").resolve()
        if frame_root.parent != (root / "logs").resolve() or source.parent != frame_root:
            raise ValueError(f"canonical source escaped the scan root: {frame_id}")
        if not source.is_file() or source.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError(f"canonical PNG is missing or invalid: {source}")
        sources[frame_id] = source
    return sources


def _scene_document(html: str) -> tuple[re.Match[str], dict[str, Any]]:
    match = SCENE_RE.search(html)
    if match is None:
        raise ValueError("viewer has no supported SCENE payload")
    try:
        scene = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("viewer SCENE payload is invalid") from exc
    frames = scene.get("frames") if isinstance(scene, dict) else None
    if not isinstance(frames, list) or [frame.get("frame_id") for frame in frames] != list(FRAME_IDS):
        raise ValueError("viewer SCENE frames are not canonical 0..10")
    return match, scene


def _validate_portable(html: str, scene: dict[str, Any]) -> None:
    if MODE_META not in html or scene.get("viewer_asset_mode") != MODE:
        raise ValueError("portable viewer marker is incomplete")
    for frame in scene["frames"]:
        if "source_image_path" in frame or "source_image_uri" in frame:
            raise ValueError("portable SCENE payload leaks runtime source paths")
    expected = [f"../logs/{frame_id}/source_rgb.png" for frame_id in FRAME_IDS]
    actual = re.findall(r'<img src="([^"]+)" alt="frame_\d{6} source image">', html)
    if actual != expected:
        raise ValueError("portable viewer image references are not canonical")
    if "file:///" in html or ".frozen_attempts" in html or re.search(r"[A-Za-z]:(?:\\\\|/)", html):
        raise ValueError("portable viewer contains a forbidden absolute path")


def repair_html(raw: bytes, scan_root: Path, scan_id: str) -> tuple[bytes, dict[str, Any]]:
    html = raw.decode("utf-8", errors="strict")
    _canonical_sources(scan_root)
    scene_match, scene = _scene_document(html)
    if MODE_META in html:
        _validate_portable(html, scene)
        return raw, {"changed": False, "repaired_frames": 0, "mode": MODE}

    repaired = html
    for frame in scene["frames"]:
        frame_id = frame["frame_id"]
        _validate_legacy_ref(frame.get("source_image_path"), scan_id, frame_id)
        _validate_legacy_ref(frame.get("source_image_uri"), scan_id, frame_id)
        pattern = re.compile(
            rf'(<img src=")([^"]+)(" alt="{re.escape(frame_id)} source image">)'
        )
        matches = list(pattern.finditer(repaired))
        if len(matches) != 1:
            raise ValueError(f"{frame_id} must have exactly one source img")
        _validate_legacy_ref(matches[0].group(2), scan_id, frame_id)
        relative = f"../logs/{frame_id}/source_rgb.png"
        repaired = pattern.sub(rf"\g<1>{relative}\g<3>", repaired, count=1)
        frame.pop("source_image_path", None)
        frame.pop("source_image_uri", None)

    scene["viewer_asset_mode"] = MODE
    payload = json.dumps(scene, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    current_match = SCENE_RE.search(repaired)
    if current_match is None:
        raise ValueError("viewer SCENE payload disappeared during repair")
    repaired = repaired[:current_match.start(1)] + payload + repaired[current_match.end(1):]
    charset = '<meta charset="utf-8">'
    if repaired.count(charset) != 1:
        raise ValueError("viewer charset marker is not unique")
    repaired = repaired.replace(charset, charset + MODE_META, 1)
    _, portable_scene = _scene_document(repaired)
    _validate_portable(repaired, portable_scene)
    return repaired.encode("utf-8"), {
        "changed": True,
        "repaired_frames": 11,
        "mode": MODE,
    }


def repair(scan_id: str, data_root: Path, *, apply: bool) -> dict[str, Any]:
    if not scan_id or Path(scan_id).name != scan_id or any(char in scan_id for char in "/\\"):
        raise ValueError("scan_id must be one path segment")
    scan_root = (data_root.resolve() / "results" / scan_id).resolve()
    if scan_root.parent != (data_root.resolve() / "results").resolve():
        raise ValueError("scan root escaped the formal results directory")
    html_path = scan_root / "visualization" / "index.html"
    if not html_path.is_file():
        raise ValueError("formal visualization/index.html does not exist")
    raw = html_path.read_bytes()
    updated, detail = repair_html(raw, scan_root, scan_id)
    result = {
        "scan_id": scan_id,
        "html_path": str(html_path),
        "operation": "apply" if apply else "dry-run",
        "before_sha256": _sha256(raw),
        "after_sha256": _sha256(updated),
        **detail,
    }
    if apply and updated != raw:
        fd, name = tempfile.mkstemp(prefix=".index.html.", suffix=".tmp", dir=str(html_path.parent))
        temporary = Path(name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(updated)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, html_path)
        finally:
            temporary.unlink(missing_ok=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strictly repair one formal VisionPC visualization's canonical source refs."
    )
    parser.add_argument("--scan-id", required=True)
    parser.add_argument(
        "--data-root", type=Path, default=Path(r"C:\ec\_deltar_scan_service_data")
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = repair(args.scan_id, args.data_root, apply=args.apply)
    except (OSError, UnicodeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({"ok": True, **result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
