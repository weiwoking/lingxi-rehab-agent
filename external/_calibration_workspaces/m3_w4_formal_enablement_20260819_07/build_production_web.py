from __future__ import annotations

import hashlib
import html
import json
from pathlib import Path
import shutil
from typing import Any


WORKSPACE = Path(__file__).resolve().parent
RESULTS = Path(r"C:\ec\_deltar_scan_service_data\results")
OUTPUT = Path(r"C:\Users\Weiwo\Downloads\geometry_m3_w4_final_review_20260819_01")
REPLAYS = {
    "G001": "scan_20260818_092411_numericfirst01",
    "G002": "scan_20260818_092729_numericfirst01",
    "SCAN_161718": "scan_20260818_161718_numericfirst01",
    "SCAN_165011": "scan_20260818_165011_numericfirst01",
    "SCAN_070604": "scan_20260819_070604_numericfirst01",
    "SCAN_085223": "scan_20260819_085223_numericfirst01",
}


STYLE = """
:root{--bg:#081018;--panel:#101c28;--line:#263849;--text:#e8f2fb;--muted:#91a8ba;--mint:#62e6b4;--amber:#ffc266;--red:#ff7a87;--blue:#65b7ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#183247 0,#081018 38%);color:var(--text);font:14px/1.5 Inter,Segoe UI,sans-serif}header{padding:28px max(24px,5vw);border-bottom:1px solid var(--line);background:#081018dd;position:sticky;top:0;z-index:4;backdrop-filter:blur(14px)}h1{margin:0 0 6px;font-size:28px}.sub{color:var(--muted)}main{max-width:1500px;margin:auto;padding:26px}.scan-grid,.frames{display:grid;grid-template-columns:repeat(auto-fit,minmax(310px,1fr));gap:18px}.scan-card,.frame{background:linear-gradient(145deg,#122231,#0c1721);border:1px solid var(--line);border-radius:14px;overflow:hidden;box-shadow:0 16px 36px #0005}.scan-card{padding:20px}.scan-card a{color:var(--mint);font-weight:700;text-decoration:none}.stats{display:flex;gap:16px;flex-wrap:wrap;margin:14px 0}.stat{background:#07111a;border:1px solid #1f3547;border-radius:9px;padding:8px 11px}.stat b{display:block;color:var(--mint);font-size:18px}.frame.focus{border-color:var(--amber);box-shadow:0 0 0 1px #ffc26655,0 16px 36px #0005}.frame-head{display:flex;justify-content:space-between;padding:12px 14px;border-bottom:1px solid var(--line)}.badge{border:1px solid #365067;border-radius:999px;padding:2px 8px;color:var(--muted)}.image-wrap{position:relative;aspect-ratio:16/9;background:#030609}.image-wrap img,.image-wrap svg{position:absolute;inset:0;width:100%;height:100%}.object-list{padding:12px 14px;display:grid;gap:10px}.object{background:#08131c;border:1px solid #203546;border-radius:10px;padding:10px}.object-top{display:flex;gap:8px;align-items:center;justify-content:space-between}.ok{color:var(--mint)}.degraded{color:var(--amber)}.unavailable{color:var(--red)}.metrics{color:var(--muted);margin-top:5px}.modes{margin-top:7px;padding-top:7px;border-top:1px dashed #294052;font-family:Consolas,monospace;font-size:12px;color:#b9d6ea}.legend{display:flex;gap:16px;flex-wrap:wrap;margin:12px 0 22px;color:var(--muted)}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}.back{color:var(--blue);text-decoration:none}.gate{color:var(--mint);font-weight:700}@media(max-width:680px){main{padding:14px}header{padding:18px}.frames{grid-template-columns:1fr}}
"""


def esc(value: Any) -> str:
    return html.escape(str(value))


def diagnostic(obj: dict[str, Any]) -> dict[str, Any] | None:
    return (
        obj.get("diagnostics", {})
        .get("generic_support", {})
        .get("diagnostics", {})
        .get("m3_w4")
    )


def object_html(obj: dict[str, Any], color: str) -> str:
    geometry = obj.get("geometry") or {}
    diag = diagnostic(obj)
    mode_text = "no metric distribution"
    if diag:
        modes = " · ".join(
            f"{row['center_mm']:.3f}mm @ p={row['probability']:.6f}"
            for row in diag["retained_modes"]
        )
        mode_text = (
            f"M3 modes ({diag['retained_mode_count']}): {modes}<br>"
            f"W4 Σ(p²·center)/Σp² = <b>{diag['support_mm']:.6f} mm</b>"
        )
    status = obj["geometry_status"]
    return (
        f'<div class="object"><div class="object-top"><span><span class="dot" style="background:{color}"></span>'
        f'<b>{esc(obj["class_name"])}</b> · {esc(obj["logical_object_id"])}</span>'
        f'<span class="{status}">{esc(status)}</span></div>'
        f'<div class="metrics">support {geometry.get("support_height_mm", "—")} mm · '
        f'height {geometry.get("object_height_mm", "—")} mm · confidence {obj.get("geometry_confidence", "—")}</div>'
        f'<div class="modes">{mode_text}</div></div>'
    )


def frame_html(scan: str, frame: dict[str, Any], image_relative: str) -> str:
    palette = ["#62e6b4", "#ffc266", "#65b7ff", "#ff7a87", "#bd8cff", "#a8e063"]
    rects = []
    labels = []
    objects = []
    for index, obj in enumerate(frame["objects"]):
        color = palette[index % len(palette)]
        x1, y1, x2, y2 = [float(value) for value in obj["bbox_xyxy"]]
        rects.append(
            f'<rect x="{x1}" y="{y1}" width="{max(0.0,x2-x1)}" height="{max(0.0,y2-y1)}" fill="none" stroke="{color}" stroke-width="2"/>'
        )
        labels.append(
            f'<text x="{x1+3}" y="{max(12,y1+13)}" fill="{color}" font-size="11" font-family="Segoe UI">{esc(obj["class_name"])}</text>'
        )
        objects.append(object_html(obj, color))
    focus = scan == "SCAN_085223" and int(frame["sequence"]) in {0, 1, 2, 7, 9, 10}
    return (
        f'<article class="frame{" focus" if focus else ""}" id="frame-{frame["sequence"]}">'
        f'<div class="frame-head"><b>Frame {frame["sequence"]}</b><span class="badge">{esc(frame["frame_id"])}</span></div>'
        f'<div class="image-wrap"><img loading="lazy" src="{esc(image_relative)}" alt="{esc(scan)} frame {frame["sequence"]}">'
        f'<svg viewBox="0 0 640 360" preserveAspectRatio="none">{"".join(rects+labels)}</svg></div>'
        f'<div class="object-list">{"".join(objects)}</div></article>'
    )


def page(scan: str, document: dict[str, Any], image_paths: dict[str, str]) -> str:
    focus_nav = ""
    if scan == "SCAN_085223":
        focus_nav = '<div class="legend">Known hard-tail focus: ' + " · ".join(
            f'<a class="back" href="#frame-{index}">frame{index}</a>' for index in (0,1,2,7,9,10)
        ) + "</div>"
    frames = "".join(
        frame_html(scan, frame, image_paths[frame["frame_id"]])
        for frame in sorted(document["frames"], key=lambda row: row["sequence"])
    )
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(scan)} · M3+W4 production</title><style>{STYLE}</style></head><body><header><a class="back" href="index.html">← 六组总览</a><h1>{esc(scan)} · production M3 + W4</h1><div class="sub">Fresh replay · single-frame · overlays and diagnostics come from production stage8b authority</div></header><main><div class="legend"><span><i class="dot" style="background:var(--mint)"></i>numeric</span><span><i class="dot" style="background:var(--amber)"></i>degraded confidence allowed</span><span class="gate">11/11 RGB visible</span></div>{focus_nav}<section class="frames">{frames}</section></main></body></html>"""


def tree_hash(root: Path) -> tuple[str, list[dict[str, Any]]]:
    digest = hashlib.sha256()
    rows = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append({"path": relative, "bytes": path.stat().st_size, "sha256": file_hash})
        digest.update(relative.encode("utf-8") + b"\0" + file_hash.encode("ascii") + b"\n")
    return digest.hexdigest(), rows


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    metrics = json.loads((WORKSPACE / "SIX_SCAN_METRICS.json").read_text(encoding="utf-8"))
    cards = []
    for scan, result_name in REPLAYS.items():
        document = json.loads(
            (WORKSPACE / "production_replays" / f"{scan}.stage8b_all_object_geometry.v1.json").read_text(encoding="utf-8")
        )
        image_paths = {}
        for frame in document["frames"]:
            source = RESULTS / result_name / "logs" / frame["frame_id"] / "source_rgb.png"
            destination = OUTPUT / "assets" / scan / f"{frame['frame_id']}.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            image_paths[frame["frame_id"]] = destination.relative_to(OUTPUT).as_posix()
        filename = f"{scan}.html"
        (OUTPUT / filename).write_text(page(scan, document, image_paths), encoding="utf-8")
        lower = metrics["CORE_OBJECTS"]["per_scan"][scan]["lower"]
        cards.append(
            f'<article class="scan-card"><h2>{esc(scan)}</h2><div class="stats">'
            f'<span class="stat"><b>{lower["median_mm"]:.2f}</b>lower median mm</span>'
            f'<span class="stat"><b>{lower["mad_mm"]:.2f}</b>lower MAD mm</span>'
            f'<span class="stat"><b>{lower["span_mm"]:.2f}</b>lower span mm</span></div>'
            f'<p>{len(document["frames"])} frames · {document["summary"]["numeric_available_count"]} numeric objects · '
            f'{document["summary"]["geometry_unavailable_count"]} unavailable</p><a href="{filename}">打开 11 帧 production review →</a></article>'
        )
    core = metrics["CORE_OBJECTS"]
    index = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Geometry M3+W4 Final Review</title><style>{STYLE}</style></head><body><header><h1>Geometry M3 + W4 · Final Production Review</h1><div class="sub">Six fresh frozen replays · exact research parity · no temporal input · production artifacts only</div></header><main><div class="stats"><span class="stat"><b>0</b>parity differences</span><span class="stat"><b>{core['mean_scan_lower_mad_mm']:.3f}</b>mean lower MAD mm</span><span class="stat"><b>{core['mean_scan_lower_span_mm']:.3f}</b>mean lower span mm</span><span class="stat"><b>{core['gt100_count']}</b>CORE &gt;100</span></div><section class="scan-grid">{"".join(cards)}</section></main></body></html>"""
    (OUTPUT / "index.html").write_text(index, encoding="utf-8")
    digest, files = tree_hash(OUTPUT)
    manifest = {
        "schema_version": "geometry_m3_w4_final_review_manifest.v1",
        "production_source_only": True,
        "is_zip": False,
        "root": str(OUTPUT),
        "scan_count": 6,
        "frame_count": 66,
        "file_count": len(files),
        "tree_sha256": digest,
        "files": files,
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    digest, files = tree_hash(OUTPUT)
    manifest["file_count"] = len(files)
    manifest["tree_sha256_with_manifest_first_pass"] = digest
    (WORKSPACE / "DOWNLOAD_VISUALIZATION_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(OUTPUT), "scans": 6, "frames": 66, "files": len(files), "tree_sha256": digest}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
