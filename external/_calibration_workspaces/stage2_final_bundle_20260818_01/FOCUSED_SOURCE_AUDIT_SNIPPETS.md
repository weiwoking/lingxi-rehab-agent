# Focused Source Audit Snippets

Read-only excerpts supporting the consumer matrix and dynamic-`fr` plan. Line numbers are snapshot-local.

### `C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\final_all16_analysis.py`

SHA-256: `7cc38a79f7e9ad69d04c187aef128320c8689509c048978838169a2271bf6139`; lines 474–488

```text
0474:     values, centers = {}, {}
0475:     solution = solution_doc(params)
0476:     for distance in DISTANCES:
0477:         center, rectangles, _ = advanced.project_raw(solution, distance)
0478:         values[str(distance)] = rectangles.tolist(); centers[str(distance)] = center.tolist()
0479:         core.save_projection_png(params, CANONICAL_GAUGE, SEMANTICS, distance)
0480:     fr = {"schema_version": "deltar_fr.stage2b_all16_candidate.v2", "status": "CANDIDATE_NOT_PROMOTED",
0481:           "selected_solver_variant": args.selected, "reference_candidate": str(CANDIDATE_PATH),
0482:           "reference_candidate_sha256": sha256_file(CANDIDATE_PATH), "shape": [64, 4],
0483:           "dtype": "float32 at runtime", "units": "source-camera pixels", "field_order": ["sy", "sx", "ey", "ex"],
0484:           "rectangle_semantics": "model-derived continuous half-open patch bounds", "zone_order": "raw index 0..63 (identity canonical gauge)",
0485:           "measurement_semantics": SEMANTICS, "zone_center_projection_by_reference_distance_mm": centers,
0486:           "model_derived_rectangle_by_reference_distance_mm": values,
0487:           "depth_dependence": "translation causes parallax; one static fr cannot exactly represent 400..1000 mm",
0488:           "runtime_replaced": False, "promotion": False}
```

### `C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\advanced_diagnostics.py`

SHA-256: `39eabac781918f8221f203d1a193adda99a27463a983b6209f2af10f1ed9da57`; lines 71–92

```text
0071: def project_raw(solution: dict[str, Any], distance_mm: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
0072:     params = np.asarray(solution["params"], dtype=float)
0073:     rays = core.tof_rays(params); boundaries = np.asarray(core.ray_boundaries(params.tolist()), dtype=float)
0074:     inverse = inverse_orientation(solution["orientation"])
0075:     rotation, translation = core.params_to_transform(params.tolist()); k, d = core.camera_parameters()
0076:     centers, rectangles, camera_points = [], [], []
0077:     for raw_index in range(64):
0078:         canonical = int(inverse[raw_index]); ray = rays[canonical]
0079:         scale = distance_mm if solution["range_semantics"] == "radial_range" else distance_mm / ray[2]
0080:         camera = rotation @ (ray * scale) + translation
0081:         uv, _ = cv2.projectPoints(camera.reshape(1, 3), np.zeros(3), np.zeros(3), k, d)
0082:         centers.append(uv.reshape(2)); camera_points.append(camera)
0083:         corners = []
0084:         for boundary_ray in boundaries[canonical]:
0085:             scale = distance_mm if solution["range_semantics"] == "radial_range" else distance_mm / boundary_ray[2]
0086:             point = rotation @ (boundary_ray * scale) + translation
0087:             buv, _ = cv2.projectPoints(point.reshape(1, 3), np.zeros(3), np.zeros(3), k, d)
0088:             corners.append(buv.reshape(2))
0089:         a = np.asarray(corners)
0090:         rectangles.append([float(a[:, 1].min()), float(a[:, 0].min()),
0091:                            float(a[:, 1].max()), float(a[:, 0].max())])
0092:     return np.asarray(centers), np.asarray(rectangles), np.asarray(camera_points)
```

### `C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\stage2b_camera_tof.py`

SHA-256: `a4627dba994d9ceb9a03084e2164668438f3b6c0d827ce148c72590d933c8eb3`; lines 702–711

```text
0702: def ray_boundaries(params: list[float]) -> list[list[list[float]]]:
0703:     p=np.asarray(params,float); fx,fy=math.exp(p[6]),math.exp(p[7]); cx,cy=p[8],p[9]; result=[]
0704:     for row in range(8):
0705:         cells=[]
0706:         for col in range(8):
0707:             rays=[]
0708:             for u,v in ((col,row),(col+1,row),(col+1,row+1),(col,row+1)):
0709:                 ray=np.array([(u-cx)/fx,(v-cy)/fy,1.0]); ray/=np.linalg.norm(ray); rays.append(ray.tolist())
0710:             cells.append(rays)
0711:         result.extend(cells)
```

### `C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\tof_adapter.py`

SHA-256: `2db8874504d006fa5e9ee3f5724ea1b11ad8cfe5162dc4c1912d66de3f06e9af`; lines 180–156

```text

```

### `C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\canvas.py`

SHA-256: `d4ae1af888ac1f26b122b3093c0e2477b36c4e28aaa423b7287fa1ae12dda8b3`; lines 120–135

```text
0120: def transform_fr(fr: np.ndarray, matrix: np.ndarray) -> np.ndarray:
0121:     arr = np.asarray(fr, dtype=np.float64)
0122:     if arr.shape != (64, 4):
0123:         raise PipelineError(ErrorCode.FR_SHAPE, "fr must have shape 64x4", "fr.values")
0124:     out = np.empty_like(arr)
0125:     for index, (sy, sx, ey, ex) in enumerate(arr):
0126:         p0 = matrix @ np.array([sx, sy, 1.0])
0127:         p1 = matrix @ np.array([ex, ey, 1.0])
0128:         out[index] = [p0[1], p0[0], p1[1], p1[0]]
0129:     return out
0130: 
0131: 
0132: def regular_grid_source() -> np.ndarray:
0133:     ys = np.linspace(0.0, 360.0, 9)
0134:     xs = np.linspace(0.0, 640.0, 9)
0135:     return np.asarray([[ys[row], xs[col], ys[row + 1], xs[col + 1]] for row in range(8) for col in range(8)], dtype=np.float64)
```

### `C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\deltar_bridge.py`

SHA-256: `d83c5375bb2d3b30d8783c0808059c685a2cd51bc919cfa79f162dddc8b6e8db`; lines 350–375

```text
0350:             "checkpoint_sha256": EXPECTED_CHECKPOINT_SHA256,
0351:             "encoder_source_path": str(self.encoder_source) if self.encoder_source else None,
0352:             "processing_mode": "local_only",
0353:             "runtime_network_required": False,
0354:             "checkpoint_key_audit": (
0355:                 self.checkpoint_audit.as_dict() if self.checkpoint_audit else None
0356:             ),
0357:             **paths,
0358:         }
0359: 
0360:     @staticmethod
0361:     def _input_dict(inputs: DeltarInputs) -> dict[str, Any]:
0362:         return {
0363:             "rgb": inputs.rgb,
0364:             "additional": {
0365:                 "hist_data": inputs.hypotheses,
0366:                 "rect_data": inputs.fr,
0367:                 "mask": inputs.mask,
0368:                 "patch_info": inputs.patch_info,
0369:             },
0370:         }
0371: 
0372:     @staticmethod
0373:     def _postprocess(pred: torch.Tensor, shape: tuple[int, int]) -> np.ndarray:
0374:         clipped = torch.clamp(pred.detach().cpu(), min=1e-3, max=10.0)
0375:         dense = F.interpolate(clipped, size=shape, mode="bilinear", align_corners=True)
```

### `C:\ec\_deltar_formal\work\deltar_f3c5175_win_cu128\src\utils\dataloader.py`

SHA-256: `2a5910156c7c45c40469a5478258b018cc9957573021f8d72a65ffd826e5c1a4`; lines 35–90

```text
0035:             'patch_size': torch.tensor([p1, p2], dtype=torch.int),
0036:             'index_wo_pad': torch.tensor([sy_wo_pad, sx_wo_pad, ey_wo_pad, ex_wo_pad], dtype=torch.int),
0037:         }
0038:     ret['zone_num'] = zone_num
0039: 
0040:     return ret
0041: 
0042: 
0043: def tensor_linspace(start, end, steps=10):
0044:     assert start.size() == end.size()
0045:     view_size = start.size() + (1,)
0046:     w_size = (1,) * start.dim() + (steps,)
0047:     out_size = start.size() + (steps,)
0048: 
0049:     start_w = torch.linspace(1, 0, steps=steps).to(start)
0050:     start_w = start_w.view(w_size).expand(out_size)
0051:     end_w = torch.linspace(0, 1, steps=steps).to(start)
0052:     end_w = end_w.view(w_size).expand(out_size)
0053: 
0054:     start = start.contiguous().view(view_size).expand(out_size)
0055:     end = end.contiguous().view(view_size).expand(out_size)
0056: 
0057:     out = start_w * start + end_w * end
0058:     return out
0059: 
0060: 
0061: def line(x, x1, y1, x2, y2):
0062:     return (x-x1)/(x2-x1)*(y2-y1)+y1
0063: 
0064: 
0065: def sample_point_from_hist_parallel(hist_data, mask, config):
0066:     znum = int(math.sqrt(mask.numel()))
0067:     fh = torch.zeros([znum**2, config.zone_sample_num], dtype=torch.float32)
0068:     zone_sample_num = config.zone_sample_num
0069:     if not config.sample_uniform:
0070:         delta = 1e-3
0071:         sample_ppf = torch.Tensor(np.arange(delta, 1, (1-2*delta)/(zone_sample_num-1)).tolist()).unsqueeze(0)
0072:         d = torch.distributions.Normal(hist_data[mask, 0:1], hist_data[mask, 1:2])
0073:         fh[mask] = d.icdf(sample_ppf).to(torch.float32)
0074:     else:
0075:         sigma = hist_data[mask, 1]
0076:         start = hist_data[mask, 0] - 3.0*sigma
0077:         end = hist_data[mask, 0] + 3.0*sigma
0078:         depth = tensor_linspace(start, end, steps=config.zone_sample_num)
0079:         fh[mask] = depth.to(torch.float32)
0080:     return fh
0081: 
0082: 
0083: def get_hist_parallel(rgb, dep, config):
0084:     # share same interval/area
0085:     height, width = rgb.shape[1], rgb.shape[2]
0086:     max_distance = config.simu_max_distance
0087:     range_margin = list(np.arange(0, max_distance+1e-9, 0.04))
0088:     if config.mode == 'train':
0089:         patch_height, patch_width = 64, 64
0090:     else:
```

### `C:\ec\esp-claw\application\edge_agent\components\rehab_tof\src\rehab_tof_provider.c`

SHA-256: `6d2f4feb782fbc8b6c445d4dd671dcde15a901f1568922d172119dee48ad4bce`; lines 490–510

```text
0490: 
0491: static bool tof_zone_usable(int16_t distance_mm, uint8_t status_code)
0492: {
0493:     return distance_mm > 0 &&
0494:            (status_code == 5U || status_code == 6U || status_code == 9U);
0495: }
0496: 
0497: static void tof_payload_from_results(rehab_tof_frame_payload_t *payload)
0498: {
0499:     for (size_t i = 0; i < REHAB_TOF_ZONE_COUNT; ++i) {
0500:         size_t target_index = i * VL53L5CX_NB_TARGET_PER_ZONE;
0501:         payload->distance_mm[i] = s_tof.results.distance_mm[target_index];
0502:         payload->range_sigma_mm[i] = s_tof.results.range_sigma_mm[target_index];
0503:         payload->target_status[i] = s_tof.results.target_status[target_index];
0504:         payload->nb_target_detected[i] = s_tof.results.nb_target_detected[i];
0505:     }
0506: }
0507: 
0508: static void tof_candidate_from_extended(
0509:     rehab_tof_candidate_frame_t *candidate,
0510:     const rehab_tof_extended_frame_t *frame)
```

### `C:\ec\esp-claw\application\edge_agent\components\rehab_scan_capture\src\rehab_scan_capture_priv.h`

SHA-256: `a68d7ea2dc3b92fb362e9252bc8f6bc6a021ff1cde2d95d6bbb589868d3c9bd9`; lines 30–40

```text
0030: #define REHAB_SCAN_SCHEMA "deltar_scan_input.v1"
0031: #define REHAB_FRAME_SCHEMA "deltar_scan_frame.v1"
0032: #define REHAB_TOF_SCHEMA "rehab_tof_raw.v1"
0033: #define REHAB_CAMERA_PROFILE_ID "camera_sc2336_640x360_rgb565_v1"
0034: #define REHAB_TOF_PROFILE_ID "tof_vl53l5cx_orientation_identity_v1"
0035: #define REHAB_CALIBRATION_PROFILE_ID "calibration_unverified_stage6_v1"
0036: #define REHAB_ALGORITHM_PROFILE_ID "algorithm_stage6_official_v1"
0037: #define REHAB_SCAN_STRATEGY_PROFILE_ID "scan_strategy_stage6_guidance_v1"
0038: 
0039: typedef struct {
0040:     uint32_t sequence;
```
