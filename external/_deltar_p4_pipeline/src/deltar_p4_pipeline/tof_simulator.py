from __future__ import annotations

from typing import Any

import numpy as np

from .calibration import invert_transform, transform_points
from .errors import ErrorCode, PipelineError
from .scene_geometry import intersect_scene, normalize
from .stage5_contracts import validate_tof_payload

MEASUREMENT_SEMANTICS = {"radial_range", "camera_axial_z"}


def _rot_z(degrees: float) -> np.ndarray:
    angle = np.radians(degrees); c, s = np.cos(angle), np.sin(angle)
    return np.array([[c,-s,0],[s,c,0],[0,0,1]], dtype=np.float64)


def tof_rays(scene: dict[str, Any]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cfg = scene["tof"]; samples = int(round(np.sqrt(int(cfg.get("rays_per_zone", 25)))))
    if samples < 1:
        raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE, "rays_per_zone must be positive")
    hfov, vfov = np.radians([float(cfg["horizontal_fov_deg"]), float(cfg["vertical_fov_deg"])]); offsets=(np.arange(samples)+.5)/samples
    rays=[]; zone_ids=[]
    for row in range(8):
        for col in range(8):
            for oy in offsets:
                for ox in offsets:
                    x=np.tan(((col+ox)/8-.5)*hfov); y=np.tan(((row+oy)/8-.5)*vfov)
                    rays.append([x,y,1]); zone_ids.append(row*8+col)
    rays_tof=normalize(np.asarray(rays)) @ _rot_z(float(cfg.get("in_plane_rotation_deg",0))).T
    t_camera_from_tof=np.asarray(cfg["T_camera_from_tof"],dtype=np.float64)
    t_ground_from_camera=np.asarray(scene["camera"]["T_ground_from_camera"],dtype=np.float64)
    origin_camera=t_camera_from_tof[:3,3]
    origin_ground=t_ground_from_camera[:3,:3]@origin_camera+t_ground_from_camera[:3,3]
    rotation_ground_from_tof=t_ground_from_camera[:3,:3]@t_camera_from_tof[:3,:3]
    return origin_ground, normalize(rays_tof@rotation_ground_from_tof.T), np.asarray(zone_ids,dtype=np.int32)


def _selection_mask(values: np.ndarray, mode: str) -> tuple[np.ndarray, int, float]:
    finite=np.isfinite(values); clean=values[finite]
    selected=np.zeros(values.shape,dtype=bool)
    if not clean.size:return selected,0,0.0
    if mode=="nearest_surface":
        threshold=np.percentile(clean,25);selected=finite&(values<=threshold)
    elif mode=="median_surface":selected=finite
    elif mode in {"dominant_surface","mixture_mean_sigma"}:
        if np.ptp(clean)<.05:selected=finite
        else:
            edges=np.arange(clean.min(),clean.max()+.051,.05)
            counts,edges=np.histogram(clean,bins=edges);best=int(np.argmax(counts));selected=finite&(values>=edges[best])&(values<=edges[best+1])
            if mode=="mixture_mean_sigma":selected=finite
    else:raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE,"unsupported aggregation",details=mode)
    selected_count=int(selected.sum());return selected,1 if np.ptp(clean)<.10 else 2,selected_count/max(int(finite.sum()),1)


def _point_list(points: np.ndarray) -> list[list[float]]:
    return np.asarray(points,dtype=np.float64).round(9).tolist()


def _safe_transform(points: np.ndarray, transform: np.ndarray) -> np.ndarray:
    output = np.full(np.asarray(points).shape, np.nan, dtype=np.float64)
    finite = np.all(np.isfinite(points), axis=1)
    if finite.any(): output[finite] = transform_points(np.asarray(points)[finite], transform)
    return output


def simulate_tof(scene: dict[str, Any], sequence: int = 0, seed: int = 0,
                 measurement_semantics: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    cfg=scene["tof"];semantics=measurement_semantics or str(cfg.get("measurement_semantics","radial_range"))
    if semantics not in MEASUREMENT_SEMANTICS:
        raise PipelineError(ErrorCode.STAGE5_TOF_PROFILE,"unsupported ToF measurement semantics",details=semantics)
    origin,rays,zones=tof_rays(scene);radial_ranges,labels,_=intersect_scene(origin,rays,scene)
    points_ground=origin+radial_ranges[:,None]*rays
    t_ground_from_camera=np.asarray(scene["camera"]["T_ground_from_camera"],dtype=np.float64)
    t_ground_from_tof=t_ground_from_camera@np.asarray(cfg["T_camera_from_tof"],dtype=np.float64)
    points_camera=_safe_transform(points_ground,invert_transform(t_ground_from_camera));points_tof=_safe_transform(points_ground,invert_transform(t_ground_from_tof))
    measurement_values=radial_ranges if semantics=="radial_range" else points_camera[:,2]
    rng=np.random.default_rng(seed);distances=[];sigmas=[];statuses=[];counts=[];zone_diagnostics=[]
    invalid=set(int(x) for x in cfg.get("invalid_zone_indices",[]));mode=str(cfg.get("aggregation","dominant_surface"))
    for zone in range(64):
        index=np.flatnonzero(zones==zone);values=measurement_values[index];selection,count,support=_selection_mask(values,mode);selected_index=index[selection]
        selected_values=measurement_values[selected_index];selected_radial=radial_ranges[selected_index];selected_camera=points_camera[selected_index];selected_ground=points_ground[selected_index];selected_tof=points_tof[selected_index]
        finite_selected=selected_values[np.isfinite(selected_values)]
        mu=float(np.median(finite_selected)) if finite_selected.size else 0.0;spread=float(np.std(finite_selected)) if finite_selected.size else 0.0
        sigma=float(cfg.get("noise_sigma_mm",8.0))+float(cfg.get("distance_sigma_slope_mm_per_m",2.0))*mu+spread*1000
        valid=zone not in invalid and finite_selected.size>0 and mu>0
        if valid:
            noisy=mu*1000+float(cfg.get("bias_mm",0.0))+rng.normal(0,sigma)
            if rng.random()<float(cfg.get("outlier_probability",0.0)):noisy+=rng.choice([-1,1])*max(150,4*sigma)
            distances.append(max(1,int(round(noisy))));sigmas.append(max(1,int(round(sigma))));statuses.append(0);counts.append(count)
        else:
            distances.append(0);sigmas.append(max(1,int(round(sigma))));statuses.append(255);counts.append(0)
        finite_all=np.isfinite(values)
        selected_center=lambda p: np.median(p[np.all(np.isfinite(p),axis=1)],axis=0).tolist() if np.any(np.all(np.isfinite(p),axis=1)) else None
        zone_diagnostics.append({"zone_index":zone,"row":zone//8,"col":zone%8,"valid":valid,"fr_provenance":"selected_surface_hits" if valid else "nominal_invalid_zone",
          "ray_count":int(index.size),"finite_hit_count":int(finite_all.sum()),"selected_ray_indices_within_zone":np.flatnonzero(selection).astype(int).tolist(),
          "all_radial_ranges_m":radial_ranges[index][np.isfinite(radial_ranges[index])].round(9).tolist(),"all_measurement_values_m":values[finite_all].round(9).tolist(),
          "selected_measurement_values_m":selected_values[np.isfinite(selected_values)].round(9).tolist(),"selected_ranges_m":selected_radial[np.isfinite(selected_radial)].round(9).tolist(),
          "selected_camera_z_m":selected_camera[:,2][np.isfinite(selected_camera[:,2])].round(9).tolist(),"selected_hit_points_camera_m":_point_list(selected_camera[np.all(np.isfinite(selected_camera),axis=1)]),
          "selected_hit_points_ground_m":_point_list(selected_ground[np.all(np.isfinite(selected_ground),axis=1)]),"selected_hit_points_tof_m":_point_list(selected_tof[np.all(np.isfinite(selected_tof),axis=1)]),
          "selected_hit_point_camera_m":selected_center(selected_camera),"selected_hit_point_ground_m":selected_center(selected_ground),"selected_hit_point_tof_m":selected_center(selected_tof),
          "aggregated_mu_m_before_noise":mu,"aggregated_sigma_m_before_sensor_noise":spread,"output_sigma_mm":max(1,int(round(sigma))),"target_count":count,"dominant_support":support,"foreground_background":count>1,
          "hit_labels":labels[index].astype(int).tolist()})
    payload={"schema_version":"rehab_tof_raw.v1","sequence":sequence,"timestamp_us":sequence*33333,"distance_mm":distances,"range_sigma_mm":sigmas,"target_status":statuses,"nb_target_detected":counts}
    validate_tof_payload(payload)
    diagnostics={"schema_version":"deltar_stage5_1_tof_diagnostics.v1","approximate_sensor_model":True,"tof_viewpoint_used":True,"aggregation":mode,
      "measurement_semantics":semantics,"conversion_provenance":"ray_origin_to_hit_norm_in_tof_frame" if semantics=="radial_range" else "selected_hit_transformed_to_camera_then_z",
      "distance_mm_source":f"per-ray {semantics} distribution aggregated before noise/bias","sigma_mm_source":f"spread of selected per-ray {semantics} values plus configured sensor sigma",
      "canonical_zone_order":"row_major_top_left_to_bottom_right","physical_in_plane_rotation_deg":cfg.get("in_plane_rotation_deg",0),"T_camera_from_tof":cfg["T_camera_from_tof"],"zones":zone_diagnostics}
    return payload,diagnostics
