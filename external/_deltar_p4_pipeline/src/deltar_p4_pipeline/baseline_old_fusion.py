from __future__ import annotations

from statistics import median
from typing import Any

import numpy as np

from .calibration import transform_points
from .camera_geometry import backproject_pixels
from .errors import ErrorCode, PipelineError
from .zone_orientation import apply_consumer_map, consumer_index_map, inverse_index_map


def covered_zones(bbox_xyxy: list[float], width: int = 640, height: int = 360, rows: int = 8, cols: int = 8) -> list[int]:
    x1,y1,x2,y2=bbox_xyxy; zones=[]
    for row in range(rows):
        for col in range(cols):
            if x1/width <= (col+.5)/cols <= x2/width and y1/height <= (row+.5)/rows <= y2/height:
                zones.append(row*cols+col)
    if not zones:
        col=min(cols-1,max(0,int(((x1+x2)/2/width)*cols))); row=min(rows-1,max(0,int(((y1+y2)/2/height)*rows)))
        zones=[row*cols+col]
    return sorted(set(zones))


def mad_filter(values: list[int], scale: float = 3.0) -> list[int]:
    if len(values)<3:return values
    med=median(values); mad=median(abs(v-med) for v in values)
    if mad==0:return values
    kept=[v for v in values if abs(v-med)<=scale*1.4826*mad]
    return kept or values


def old_geometry(bbox: list[float], distance_m: float, calibration: dict[str, Any]) -> dict[str, float]:
    camera=calibration["camera_model"]; x1,y1,x2,y2=bbox; u=(x1+x2)/2
    uv=np.array([[u,y1],[u,y2],[(x1+x2)/2,(y1+y2)/2]],dtype=np.float64)
    rays=backproject_pixels(uv,np.ones(3),camera,"axial_z")
    unit=rays[2]/np.linalg.norm(rays[2]); z=distance_m*unit[2]
    points_camera=rays[:2]*z
    points_ground=transform_points(points_camera,calibration["transforms"]["T_ground_from_camera"])
    top,bottom=float(points_ground[0,2]),float(points_ground[1,2])
    if top<bottom:top,bottom=bottom,top
    if abs(bottom-float(calibration["ground"]["plane_z_m"]))<=.03:bottom=float(calibration["ground"]["plane_z_m"])
    return {"distance_m":distance_m,"object_height_m":top-bottom,"bottom_height_m":bottom,"top_height_m":top}


def evaluate_old_detection(detection: dict[str, Any], tof: dict[str, Any], calibration: dict[str, Any], orientation: dict[str, Any] | None = None) -> dict[str, Any]:
    orientation = orientation or {"strategy": "identity"}
    mapping=consumer_index_map(orientation["strategy"],orientation.get("explicit_index_map_64")); inverse=inverse_index_map(mapping)
    bbox=list(detection["bbox_xyxy"]); canonical_zones=covered_zones(bbox); zones=[int(inverse[z]) for z in canonical_zones]
    oriented={key:apply_consumer_map(tof[key],orientation) for key in ("distance_mm","target_status","nb_target_detected")}
    values=[int(oriented["distance_mm"][z]) for z in zones if int(oriented["target_status"][z])!=255 and int(oriented["nb_target_detected"][z])>0 and 100<=int(oriented["distance_mm"][z])<=4000]
    values=mad_filter(values)
    if not values:return {"status":"rejected","reason":"no_valid_tof_zone","tof_zone_ids":[]}
    geometry=old_geometry(bbox,float(median(values))/1000.0,calibration)
    return {"status":"valid","geometry":geometry,"tof_zone_ids":zones,"algorithm_provenance":{
        "source":"C:/ec/esp-claw/pc/vision_interpreter/app/fusion.py and geometry.py faithful repository-external port",
        "bbox_zone_centres":True,"status_range_filter":True,"mad_scale":3.0,"median":True,"gt_mask_used":False,
        "orientation_strategy":orientation["strategy"],"canonical_zones":canonical_zones,"consumer_zones":zones}}
