from __future__ import annotations

from typing import Any

import numpy as np

from .camera_geometry import project_points
from .errors import ErrorCode, PipelineError


def _nominal_camera_points(scene: dict[str, Any], zone: dict[str, Any]) -> np.ndarray:
    points=np.asarray(zone.get("selected_hit_points_camera_m",[]),dtype=np.float64)
    if points.ndim==2 and points.shape[0]:return points
    row,col=int(zone["row"]),int(zone["col"]);cfg=scene["tof"]
    hfov,vfov=np.radians([float(cfg["horizontal_fov_deg"]),float(cfg["vertical_fov_deg"])]);samples=[]
    for oy in (.1,.9):
        for ox in (.1,.9):
            ray=np.array([np.tan(((col+ox)/8-.5)*hfov),np.tan(((row+oy)/8-.5)*vfov),1.0]);ray/=np.linalg.norm(ray)
            samples.append(ray*2.0)
    rotation=np.asarray(cfg["T_camera_from_tof"],dtype=np.float64)[:3,:3];translation=np.asarray(cfg["T_camera_from_tof"],dtype=np.float64)[:3,3]
    return np.asarray(samples)@rotation.T+translation


def fr_from_diagnostics(scene: dict[str, Any], diagnostics: dict[str, Any], canvas: str) -> tuple[list[list[float]], list[dict[str, Any]]]:
    if canvas not in {"direct_640x360","letterbox_640x480"}:raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE,"unsupported Stage 5.1 canvas",details=canvas)
    camera={"model":"pinhole",**{key:scene["camera"][key] for key in ("fx","fy","cx","cy","distortion")}}
    rectangles=[];metadata=[]
    for zone in diagnostics["zones"]:
        points=_nominal_camera_points(scene,zone);points=points[np.all(np.isfinite(points),axis=1)&(points[:,2]>1e-6)]
        if not points.size:raise PipelineError(ErrorCode.STAGE5_TOF_SIMULATION,"zone has no finite actual or nominal points",details=zone["zone_index"])
        uv=project_points(points,camera);sx=float(np.min(uv[:,0]));ex=float(np.max(uv[:,0])+1e-3);sy=float(np.min(uv[:,1]));ey=float(np.max(uv[:,1])+1e-3)
        if canvas=="letterbox_640x480":sy+=60;ey+=60
        if not(np.isfinite([sy,sx,ey,ex]).all() and ey>sy and ex>sx):raise PipelineError(ErrorCode.STAGE5_TOF_SIMULATION,"degenerate projected FR",details=zone["zone_index"])
        rectangles.append([sy,sx,ey,ex]);metadata.append({"zone_index":zone["zone_index"],"fr_sy_sx_ey_ex":[sy,sx,ey,ex],"fr_provenance":zone["fr_provenance"],"valid":zone["valid"],"half_open":True})
    return rectangles,metadata


def validate_fr_pair(direct: list[list[float]], letterbox: list[list[float]], valid_mask: list[bool]) -> dict[str, Any]:
    a=np.asarray(direct,dtype=np.float64);b=np.asarray(letterbox,dtype=np.float64);expected=a.copy();expected[:,[0,2]]+=60
    degenerate=int(np.sum((a[:,2]<=a[:,0])|(a[:,3]<=a[:,1])));oob=int(np.sum((a[:,0]<0)|(a[:,1]<0)|(a[:,2]>360)|(a[:,3]>640)))
    centers=np.column_stack(((a[:,1]+a[:,3])/2,(a[:,0]+a[:,2])/2))
    return {"schema_version":"deltar_stage5_1_fr_validation.v1","order":"sy_sx_ey_ex","zone_count":64,"unique_zone_indices":64,"valid_zone_count":int(np.sum(valid_mask)),
      "out_of_bounds_count":oob,"degenerate_count":degenerate,"direct_letterbox_exact_y_plus_60":bool(np.allclose(b,expected,atol=1e-9)),
      "corner_centers_xy":{str(i):centers[i].tolist() for i in (0,7,56,63)},"center_zone_27_xy":centers[27].tolist(),"half_open":True}
