from __future__ import annotations

import html
import json
import math
import os
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "pc_scene_ui_input.v1"
DISPLAY_LEVELS = frozenset({"L1", "L2", "L3", "L4"})
GATE1_OUTCOMES = frozenset(
    {"pass_success", "pass_failure_fallback", "blocked_failure", "blocked_success"}
)
FRAME_STATUS_ENUMS = {
    "capture_status": {"CAPTURE_OK", "CAPTURE_FAILURE"},
    "tof_status": {"TOF_NORMAL", "TOF_DEGRADED", "TOF_DEGRADED_RAW", "TOF_BORROWED", "TOF_UNAVAILABLE"},
    "deltar_status": {"DELTAR_OK", "DELTAR_UNAVAILABLE"},
    "detection_status": {"DETECTION_OK", "DETECTION_UNAVAILABLE"},
    "geometry_status": {"GEOMETRY_OK", "GEOMETRY_DEGRADED", "GEOMETRY_UNAVAILABLE"},
}
VIEWER_ASSET_MODE = "portable_relative_v1"


def validate_pc_scene_ui_input(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("UI input root must be a JSON object")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"UI input schema_version must be {SCHEMA_VERSION}")
    if not isinstance(document.get("scan_id"), str) or not document["scan_id"]:
        raise ValueError("UI input scan_id must be a non-empty string")
    if document.get("frame_count") != 11:
        raise ValueError("UI input frame_count must be exactly 11")
    frames = document.get("frames")
    if not isinstance(frames, list) or len(frames) != 11:
        raise ValueError("UI input frames must contain exactly 11 entries")
    if [frame.get("sequence") for frame in frames if isinstance(frame, dict)] != list(range(11)):
        raise ValueError("UI input frames must be ordered by sequence 0..10")
    identities: set[tuple[str, str]] = set()
    for frame_index, frame in enumerate(frames):
        if frame.get("frame_id") != f"frame_{frame_index:06d}":
            raise ValueError(f"frames[{frame_index}].frame_id/sequence mismatch")
        if not isinstance(frame.get("objects"), list):
            raise ValueError(f"frames[{frame_index}].objects must be an array")
        processing_status = frame.get("processing_status")
        if not isinstance(processing_status, dict):
            raise ValueError(f"frames[{frame_index}].processing_status must be an object")
        required_status = set(FRAME_STATUS_ENUMS) | {"degradation_mode", "reason_codes", "tof_diagnostics"}
        if set(processing_status) != required_status:
            raise ValueError(f"frames[{frame_index}].processing_status keys mismatch")
        for name, choices in FRAME_STATUS_ENUMS.items():
            if processing_status[name] not in choices:
                raise ValueError(f"frames[{frame_index}].processing_status.{name} is invalid")
        if not isinstance(processing_status["degradation_mode"], str) or not processing_status["degradation_mode"]:
            raise ValueError(f"frames[{frame_index}].processing_status.degradation_mode is invalid")
        if not isinstance(processing_status["reason_codes"], list) or not all(
            isinstance(value, str) and value for value in processing_status["reason_codes"]
        ):
            raise ValueError(f"frames[{frame_index}].processing_status.reason_codes is invalid")
        if not isinstance(processing_status["tof_diagnostics"], dict):
            raise ValueError(f"frames[{frame_index}].processing_status.tof_diagnostics is invalid")
        for object_index, item in enumerate(frame["objects"]):
            prefix = f"frames[{frame_index}].objects[{object_index}]"
            if not isinstance(item, dict):
                raise ValueError(f"{prefix} must be an object")
            identity = (frame["frame_id"], str(item.get("logical_object_id", "")))
            if not identity[1] or identity in identities:
                raise ValueError("UI object identities must be unique per frame")
            identities.add(identity)
            bbox = item.get("bbox_xyxy")
            if not isinstance(bbox, list) or len(bbox) != 4 or not all(
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                for value in bbox
            ):
                raise ValueError(f"{prefix}.bbox_xyxy must contain four finite numbers")
            if item.get("display_level") not in DISPLAY_LEVELS:
                raise ValueError(f"{prefix}.display_level must be L1/L2/L3/L4")
            if item.get("gate1_outcome") not in GATE1_OUTCOMES:
                raise ValueError(f"{prefix}.gate1_outcome is invalid")
            if any(
                type(item.get(name)) is not bool
                for name in ("gate1_passed", "gate2_passed", "selected_for_p4")
            ):
                raise ValueError(f"{prefix} gate/selection flags must be boolean")
            if item.get("geometry_status") not in {"ok", "degraded", "unavailable"}:
                raise ValueError(f"{prefix}.geometry_status must be ok/degraded/unavailable")
            if type(item.get("numeric_available")) is not bool:
                raise ValueError(f"{prefix}.numeric_available must be boolean")
            if item["numeric_available"] != (item["geometry_status"] in {"ok", "degraded"}):
                raise ValueError(f"{prefix} numeric/status mismatch")
            if item["numeric_available"]:
                for field in (
                    "distance_mm",
                    "object_height_mm",
                    "support_height_mm",
                    "top_height_above_floor_mm",
                    "geometry_confidence",
                ):
                    value = item.get(field)
                    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(float(value)):
                        raise ValueError(f"{prefix}.{field} must be finite for numeric geometry")
            if item["geometry_status"] == "degraded" and not isinstance(item.get("degradation_reason"), str):
                raise ValueError(f"{prefix}.degradation_reason is required for degraded geometry")
    return document


def _image_uri(
    value: Any,
    input_root: Path,
    output_root: Path,
    artifact_root: Path,
) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if value.startswith(("data:", "http://", "https://", "file:")):
        raise ValueError("source image must be a path inside the visualization artifact")
    path = Path(value)
    if not path.is_absolute():
        path = (input_root / path).resolve()
    else:
        path = path.resolve()
    root = artifact_root.resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("source image escapes the visualization artifact root") from exc
    canonical_frames = {f"frame_{index:06d}" for index in range(11)}
    if (
        path.name != "source_rgb.png"
        or path.parent.name not in canonical_frames
        or path.parent.parent.name != "logs"
    ):
        raise ValueError("source image is not a canonical logs/frame/source_rgb.png artifact")
    if not path.is_file():
        raise ValueError(f"source image is missing: {path}")
    try:
        relative = os.path.relpath(path, start=output_root.resolve())
    except ValueError as exc:
        raise ValueError("source image and visualization output are on different roots") from exc
    normalized = Path(relative).as_posix()
    if normalized.startswith("/") or ":" in normalized or normalized.startswith("file:"):
        raise ValueError("source image reference is not portable")
    if (output_root.resolve() / Path(relative)).resolve() != path:
        raise ValueError("source image relative reference does not resolve canonically")
    return normalized


def _escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _object_class(item: dict[str, Any]) -> str:
    """The renderer's only visual-policy input is display_level."""

    return f"level-{item['display_level'].lower()}"


def _frame_section(frame: dict[str, Any], frame_index: int) -> str:
    objects = frame["objects"]
    selected_count = sum(bool(item["selected_for_p4"]) for item in objects)
    image_uri = frame.get("source_image_uri")
    status = frame["processing_status"]
    badges = "".join(
        f'<span class="status-badge">{_escape(status[name])}</span>'
        for name in ("capture_status", "tof_status", "deltar_status", "detection_status", "geometry_status")
    )
    image = (
        f'<img src="{_escape(image_uri)}" alt="{_escape(frame["frame_id"])} source image">'
        if image_uri
        else '<div class="image-empty">source image unavailable</div>'
    )
    boxes: list[str] = []
    chips: list[str] = []
    for object_index, item in enumerate(objects):
        left, top, right, bottom = (float(value) for value in item["bbox_xyxy"])
        style = (
            f"left:{left / 640 * 100:.6f}%;top:{top / 360 * 100:.6f}%;"
            f"width:{max(0.0, right - left) / 640 * 100:.6f}%;"
            f"height:{max(0.0, bottom - top) / 360 * 100:.6f}%"
        )
        state = _object_class(item)
        data = f'data-frame="{frame_index}" data-object="{object_index}"'
        boxes.append(
            f'<button type="button" class="object-box {state}" style="{style}" {data} '
            f'aria-pressed="false"><span>{_escape(item["class_name"])}</span></button>'
        )
        chips.append(
            f'<button type="button" class="object-chip {state}" {data} aria-pressed="false">'
            f'{_escape(item["class_name"])} · {item["display_level"]} · {_escape(item["geometry_status"])}'
            f'<small>numeric={_escape(item["numeric_available"])} · support={_escape(item["support_height_mm"])} · object={_escape(item["object_height_mm"])} · conf={_escape(item["geometry_confidence"])}</small>'
            f'<small>{_escape(item["degradation_reason"] or item["failure_reason"] or "normal")}</small>'
            f'<small>{_escape(item["support_method"])} → {_escape(item["shape_method"])}</small>'
            f'<small>{_escape(item["logical_object_id"])}</small></button>'
        )
    if not chips:
        chips.append('<span class="no-objects">No objects in this frame</span>')
    return f'''<section class="frame-section" id="frame-{frame_index}">
  <div class="frame-heading"><div><span>FRAME {frame_index:02d}</span><h2>{_escape(frame['frame_id'])}</h2><div class="status-row">{badges}</div></div><div>{len(objects)} objects · {selected_count} selected<br><small>{_escape(status['degradation_mode'])}</small></div></div>
  <div class="image-wrap">{image}{''.join(boxes)}</div>
  <div class="object-list">{''.join(chips)}</div>
</section>'''


def _html(
    document: dict[str, Any],
    input_root: Path,
    output_root: Path,
    artifact_root: Path,
) -> str:
    scene = json.loads(json.dumps(document, ensure_ascii=False, allow_nan=False))
    for frame in scene["frames"]:
        frame["source_image_uri"] = _image_uri(
            frame.get("source_image_path"), input_root, output_root, artifact_root
        )
    payload_scene = json.loads(json.dumps(scene, ensure_ascii=False, allow_nan=False))
    payload_scene["viewer_asset_mode"] = VIEWER_ASSET_MODE
    for frame in payload_scene["frames"]:
        frame.pop("source_image_path", None)
        frame.pop("source_image_uri", None)
    payload = json.dumps(
        payload_scene, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).replace("</", "<\\/")
    sections = "\n".join(_frame_section(frame, index) for index, frame in enumerate(scene["frames"]))
    anchors = "".join(f'<a href="#frame-{index}">{index + 1:02d}</a>' for index in range(11))
    summary = scene.get("scan_summary") or {}
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewer-asset-mode" content="{VIEWER_ASSET_MODE}"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>PC dual-gate scene · {_escape(scene['scan_id'])}</title>
<style>
:root{{--bg:#09111d;--panel:#101d2d;--line:#263b52;--muted:#91a4ba;--text:#edf6ff;--l1:#28df75;--l2:#ff9f43;--l3:#4db5ff;--l4:#8c98a7}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth;scroll-padding-top:84px}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 "Segoe UI",Arial,sans-serif}}.app{{max-width:1480px;margin:auto;padding:22px}}header{{display:flex;justify-content:space-between;gap:18px;align-items:end}}h1,h2{{margin:0}}.meta,.summary{{color:var(--muted)}}.legend{{display:flex;gap:12px;flex-wrap:wrap}}.legend i{{display:inline-block;width:16px;height:10px;border:2px solid;margin-right:4px}}.legend .l1{{border-color:var(--l1)}}.legend .l2{{border-color:var(--l2)}}.legend .l3{{border-color:var(--l3)}}.legend .l4{{border-color:var(--l4);border-style:dashed}}
.frame-nav{{position:sticky;top:0;z-index:20;display:grid;grid-template-columns:repeat(11,1fr);gap:6px;padding:9px;margin:18px 0;background:#09111ded;border:1px solid var(--line);border-radius:11px}}.frame-nav a{{text-align:center;color:var(--muted);text-decoration:none;border:1px solid #29405a;border-radius:7px;padding:7px}}.frame-section{{scroll-margin-top:82px;background:var(--panel);border:1px solid var(--line);border-radius:15px;padding:16px;margin-bottom:34px}}.frame-heading{{display:flex;justify-content:space-between;align-items:end;margin-bottom:12px;color:var(--muted)}}.frame-heading span{{color:var(--l3);font-size:12px;letter-spacing:.12em}}.frame-heading h2{{color:var(--text)}}.image-wrap{{position:relative;aspect-ratio:16/9;background:#05090f;overflow:hidden;border-radius:10px}}.image-wrap img{{width:100%;height:100%;object-fit:contain}}.image-empty{{display:grid;height:100%;place-items:center;color:var(--muted)}}
.status-row{{display:flex;gap:5px;flex-wrap:wrap;margin-top:7px}}.status-badge{{border:1px solid #39536b;border-radius:999px;padding:2px 7px;color:#c7d9ea!important;font-size:10px!important;letter-spacing:0!important;background:#0a1725}}
.object-box{{position:absolute;border:2px solid;background:#4db5ff18;cursor:pointer;padding:0}}.object-box span{{position:absolute;top:-22px;left:-2px;background:#07111ee6;color:white;padding:2px 5px;border-radius:4px}}.level-l1{{border-color:var(--l1)!important}}.level-l2{{border-color:var(--l2)!important}}.level-l3{{border-color:var(--l3)!important}}.level-l4{{border-color:var(--l4)!important;border-style:dashed!important}}.object-box.active,.object-chip.active{{outline:2px solid white;z-index:4}}.object-list{{display:flex;gap:7px;overflow-x:auto;padding-top:9px}}.object-chip{{min-width:max-content;text-align:left;border:1px solid;border-radius:8px;background:#0c1928;color:var(--text);padding:6px 8px;cursor:pointer}}.object-chip small{{display:block;color:var(--muted)}}.no-objects{{color:var(--muted)}}
.detail-panel{{position:fixed;z-index:50;top:84px;right:20px;width:min(390px,calc(100vw - 40px));max-height:calc(100vh - 104px);overflow:auto;border:1px solid #466178;border-radius:14px;background:#0d1722f7;box-shadow:0 22px 70px #000b}}.detail-panel>header{{position:sticky;top:0;background:#0d1722;padding:14px;border-bottom:1px solid var(--line)}}.detail-body{{padding:14px}}.grid{{display:grid;grid-template-columns:1fr auto;gap:7px 11px}}.grid div{{color:var(--muted)}}.detail-close{{background:#142231;color:white;border:1px solid #40556a;border-radius:7px;font-size:22px}}pre{{white-space:pre-wrap;word-break:break-word;background:#07111d;padding:8px}}[hidden]{{display:none!important}}
@media(max-width:680px){{.app{{padding:10px}}.frame-nav{{grid-template-columns:repeat(6,1fr)}}header{{align-items:start;flex-direction:column}}.detail-panel{{top:auto;bottom:10px;right:10px;width:calc(100vw - 20px);max-height:70vh}}}}
</style></head><body><div class="app">
<header><div><h1>PC 全对象双门视图</h1><div class="meta">{_escape(scene['scan_id'])} · 11 frames · {_escape(scene['generated_at'])}</div></div><div class="legend"><span><i class="l1"></i>L1 final</span><span><i class="l2"></i>L2 Gate 1</span><span><i class="l3"></i>L3 geometry</span><span><i class="l4"></i>L4 failure</span></div></header>
<p class="summary">{_escape(summary.get('object_count', 0))} objects · {_escape(summary.get('geometry_success_count', 0))} geometry success · {_escape(summary.get('geometry_failure_count', 0))} geometry failures</p>
<nav class="frame-nav">{anchors}</nav><main>{sections}</main></div>
<aside id="detail" class="detail-panel" hidden><header><h3 id="detail-title">Object detail</h3><button id="detail-close" class="detail-close" type="button">×</button></header><div id="detail-body" class="detail-body"></div></aside>
<script>const SCENE={payload};const panel=document.getElementById('detail'),title=document.getElementById('detail-title'),body=document.getElementById('detail-body');const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));const fmt=v=>typeof v==='number'?Number(v.toFixed(2)):v??'—';
function closeDetails(){{panel.hidden=true;document.querySelectorAll('[data-frame]').forEach(n=>n.classList.remove('active'))}}function showDetails(fi,oi){{const o=SCENE.frames[fi].objects[oi];title.textContent=`${{o.class_name}} · ${{o.logical_object_id}}`;body.innerHTML=`<div class="grid"><div>display level</div><b>${{o.display_level}}</b><div>Gate 1 outcome</div><b>${{o.gate1_outcome}}</b><div>Gate 1 passed</div><b>${{o.gate1_passed}}</b><div>Gate 2 passed</div><b>${{o.gate2_passed}}</b><div>selected_for_p4</div><b>${{o.selected_for_p4}}</b><div>detection confidence</div><b>${{fmt(o.confidence)}}</b><div>geometry status</div><b>${{o.geometry_status}}</b><div>numeric_available</div><b>${{o.numeric_available}}</b><div>geometry confidence</div><b>${{fmt(o.geometry_confidence)}}</b><div>distance_mm</div><b>${{fmt(o.distance_mm)}}</b><div>support_height_mm</div><b>${{fmt(o.support_height_mm)}}</b><div>object_height_mm</div><b>${{fmt(o.object_height_mm)}}</b><div>degradation reason</div><b>${{esc(o.degradation_reason)}}</b><div>support method</div><b>${{esc(o.support_method)}}</b><div>shape method</div><b>${{esc(o.shape_method)}}</b><div>failure reason</div><b>${{esc(o.failure_reason)}}</b></div><details><summary>diagnostics</summary><pre>${{esc(JSON.stringify(o.diagnostics||{{}},null,2))}}</pre></details>`;panel.hidden=false;document.querySelectorAll('[data-frame]').forEach(n=>n.classList.toggle('active',Number(n.dataset.frame)===fi&&Number(n.dataset.object)===oi))}}document.querySelectorAll('[data-frame]').forEach(n=>n.addEventListener('click',()=>showDetails(Number(n.dataset.frame),Number(n.dataset.object))));document.getElementById('detail-close').addEventListener('click',closeDetails);document.addEventListener('keydown',e=>{{if(e.key==='Escape')closeDetails()}});
</script></body></html>'''


def render_pc_scene_ui(
    input_path: Path,
    output_path: Path,
    *,
    artifact_root: Path | None = None,
) -> Path:
    source = input_path.resolve()
    try:
        document = json.loads(
            source.read_text(encoding="utf-8"),
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"cannot read strict UI input JSON: {exc}") from exc
    validate_pc_scene_ui_input(document)
    destination = output_path.resolve()
    root = artifact_root.resolve() if artifact_root is not None else source.parent
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        _html(document, source.parent, destination.parent, root),
        encoding="utf-8",
        newline="\n",
    )
    return destination
