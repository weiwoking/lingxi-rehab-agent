import copy
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response


LEFT_BOUNDARY = 0.20
RIGHT_BOUNDARY = 0.80
VIEW_REFRESH_MS = 400


def zone_for_center(center_x: float) -> str:
    if center_x < LEFT_BOUNDARY:
        return "LEFT"
    if center_x > RIGHT_BOUNDARY:
        return "RIGHT"
    return "CENTER"


def boundary_pixel_positions(image_width: int) -> tuple[int, int]:
    return (
        int(round(LEFT_BOUNDARY * image_width)),
        int(round(RIGHT_BOUNDARY * image_width)),
    )


@dataclass(frozen=True)
class LatestPersonFollowFrame:
    original_jpeg_bytes: bytes
    annotated_jpeg_bytes: bytes
    metadata: dict[str, Any]


class LatestPersonFollowFrameStore:
    """Thread-safe, in-memory storage for only the most recent successful frame."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: LatestPersonFollowFrame | None = None

    def update(
        self,
        original_jpeg_bytes: bytes,
        image_bgr: np.ndarray,
        response: dict[str, Any],
    ) -> None:
        detections = [self._display_detection(item) for item in response["detections"]]
        annotated = self._annotate(image_bgr, detections)
        ok, encoded = cv2.imencode(".jpg", annotated)
        if not ok:
            raise RuntimeError("Failed to encode annotated person-follow JPEG.")

        metadata = {
            "frame_id": response["frame_id"],
            "target_found": bool(response["target_found"]),
            "detection_count": len(detections),
            "detections": detections,
            "timing": copy.deepcopy(response["processing_time_ms"]),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }
        frame = LatestPersonFollowFrame(
            original_jpeg_bytes=bytes(original_jpeg_bytes),
            annotated_jpeg_bytes=encoded.tobytes(),
            metadata=metadata,
        )
        with self._lock:
            self._latest = frame

    def get(self) -> LatestPersonFollowFrame | None:
        with self._lock:
            frame = self._latest
            if frame is None:
                return None
            return LatestPersonFollowFrame(
                original_jpeg_bytes=frame.original_jpeg_bytes,
                annotated_jpeg_bytes=frame.annotated_jpeg_bytes,
                metadata=copy.deepcopy(frame.metadata),
            )

    @staticmethod
    def _display_detection(item: dict[str, Any]) -> dict[str, Any]:
        center = item["center_normalized"]
        center_x = float(center["x"])
        center_y = float(center["y"])
        return {
            "class_name": str(item["class_name"]),
            "confidence": float(item["confidence"]),
            "bbox_px": copy.deepcopy(item["bbox_px"]),
            "center_x": center_x,
            "center_y": center_y,
            "zone": zone_for_center(center_x),
        }

    @staticmethod
    def _annotate(image_bgr: np.ndarray, detections: list[dict[str, Any]]) -> np.ndarray:
        annotated = image_bgr.copy()
        height, width = annotated.shape[:2]
        line_color = (0, 215, 255)
        for x in boundary_pixel_positions(width):
            cv2.line(annotated, (x, 0), (x, height - 1), line_color, 2)

        for item in detections:
            box = item["bbox_px"]
            x1 = max(0, min(width - 1, int(round(float(box["x1"])))))
            y1 = max(0, min(height - 1, int(round(float(box["y1"])))))
            x2 = max(0, min(width - 1, int(round(float(box["x2"])))))
            y2 = max(0, min(height - 1, int(round(float(box["y2"])))))
            center = (
                max(0, min(width - 1, int(round(item["center_x"] * width)))),
                max(0, min(height - 1, int(round(item["center_y"] * height)))),
            )
            color = (0, 255, 0)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            cv2.circle(annotated, center, 5, (0, 0, 255), -1)
            label_y = max(18, y1 - 24)
            cv2.putText(
                annotated,
                f'{item["class_name"]} {item["confidence"]:.3f}',
                (x1, label_y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )
            cv2.putText(
                annotated,
                f'cx={item["center_x"]:.3f} zone={item["zone"]}',
                (x1, min(height - 6, label_y + 18)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )

        if not detections:
            cv2.putText(
                annotated,
                "NO TARGET",
                (20, 50),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.2,
                (0, 0, 255),
                3,
                cv2.LINE_AA,
            )
        return annotated


VIEW_HTML = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Person-Follow 实时查看</title>
  <style>
    body {{ margin: 0; padding: 20px; background: #10151d; color: #e8eef7; font: 14px system-ui, sans-serif; }}
    h1 {{ margin: 0 0 8px; }} .status {{ color: #9eb1c7; margin-bottom: 16px; }}
    .images {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; }}
    figure, section {{ margin: 0; padding: 12px; border: 1px solid #304056; border-radius: 8px; background: #17202b; }}
    img {{ display: block; width: 100%; height: auto; background: #080b10; }} figcaption {{ margin-bottom: 8px; font-weight: 700; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 14px; margin: 16px 0; }} .summary span {{ background: #243245; padding: 7px 10px; border-radius: 6px; }}
    table {{ width: 100%; border-collapse: collapse; }} th, td {{ padding: 7px; border-bottom: 1px solid #304056; text-align: left; }}
    pre {{ white-space: pre-wrap; word-break: break-word; }} .empty {{ color: #f0c674; }}
  </style>
</head>
<body>
  <h1>Person-Follow 实时查看</h1>
  <div id="status" class="status">等待首帧……</div>
  <div class="images">
    <figure><figcaption>原图</figcaption><img id="original" alt="最新原图"></figure>
    <figure><figcaption>标注图</figcaption><img id="annotated" alt="最新标注图"></figure>
  </div>
  <div class="summary">
    <span>frame_id: <b id="frame">-</b></span><span>target_found: <b id="found">-</b></span>
    <span>detection_count: <b id="count">-</b></span><span>PC total: <b id="total">-</b> ms</span>
    <span>updated_at: <b id="updated">-</b></span>
  </div>
  <section><h2>Detections</h2><div id="detections" class="empty">尚无数据</div></section>
  <section><h2>PC timing (ms)</h2><pre id="timing">{{}}</pre></section>
  <script>
    const q = id => document.getElementById(id);
    function renderDetections(items) {{
      if (!items.length) {{ q('detections').innerHTML = '<span class="empty">NO TARGET</span>'; return; }}
      const rows = items.map((d, i) => `<tr><td>${{i + 1}}</td><td>${{d.class_name}}</td><td>${{d.confidence.toFixed(3)}}</td><td>${{JSON.stringify(d.bbox_px)}}</td><td>${{d.center_x.toFixed(3)}}</td><td>${{d.center_y.toFixed(3)}}</td><td>${{d.zone}}</td></tr>`).join('');
      q('detections').innerHTML = `<table><thead><tr><th>#</th><th>class</th><th>confidence</th><th>bbox_px</th><th>center_x</th><th>center_y</th><th>zone</th></tr></thead><tbody>${{rows}}</tbody></table>`;
    }}
    async function refresh() {{
      const stamp = Date.now();
      try {{
        const response = await fetch('/api/v1/person-follow/latest.json?t=' + stamp, {{cache: 'no-store'}});
        if (response.status === 404) {{ q('status').textContent = '等待首帧……'; return; }}
        if (!response.ok) throw new Error('latest.json HTTP ' + response.status);
        const data = await response.json();
        q('status').textContent = '每 {VIEW_REFRESH_MS} ms 刷新；页面仅展示 PC 最新成功帧，不参与控制。';
        q('frame').textContent = data.frame_id; q('found').textContent = data.target_found;
        q('count').textContent = data.detection_count; q('total').textContent = data.timing.total;
        q('updated').textContent = data.updated_at; q('timing').textContent = JSON.stringify(data.timing, null, 2);
        q('original').src = '/api/v1/person-follow/latest/original.jpg?t=' + stamp;
        q('annotated').src = '/api/v1/person-follow/latest/annotated.jpg?t=' + stamp;
        renderDetections(data.detections);
      }} catch (error) {{ q('status').textContent = '刷新失败：' + error.message; }}
    }}
    refresh(); setInterval(refresh, {VIEW_REFRESH_MS});
  </script>
</body>
</html>"""


def create_person_follow_viewer_router(store: LatestPersonFollowFrameStore) -> APIRouter:
    router = APIRouter()

    def latest() -> LatestPersonFollowFrame:
        frame = store.get()
        if frame is None:
            raise HTTPException(status_code=404, detail="No person-follow frame has been received yet.")
        return frame

    @router.get("/person-follow-view", response_class=HTMLResponse)
    def view() -> HTMLResponse:
        return HTMLResponse(VIEW_HTML, headers={"Cache-Control": "no-store"})

    @router.get("/api/v1/person-follow/latest/original.jpg")
    def original() -> Response:
        return Response(latest().original_jpeg_bytes, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @router.get("/api/v1/person-follow/latest/annotated.jpg")
    def annotated() -> Response:
        return Response(latest().annotated_jpeg_bytes, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @router.get("/api/v1/person-follow/latest.json")
    def metadata() -> JSONResponse:
        return JSONResponse(latest().metadata, headers={"Cache-Control": "no-store"})

    return router
