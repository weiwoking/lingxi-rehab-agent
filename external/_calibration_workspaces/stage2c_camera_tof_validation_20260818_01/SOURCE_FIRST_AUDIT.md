# Stage2C source-first audit

审查日期：2026-08-18。所有输入只读；未执行 build、flash、monitor、reconfigure、clean、promotion 或采集。

## Frozen authorities

- Stage2A schema 是 `stage2a_camera_intrinsics_candidate.v1`。Camera K/D 位于 `camera.intrinsics`：`fx/fy/cx/cy` 与 `distortion.k1/k2/p1/p2/k3`。图像 provenance 明确为 640×360、`RGB565_LE`、stride 1280、payload 460800，无 crop/resize。ChArUco board 字段指定 `DICT_5X5_100`、7×5、square 25 mm、marker 18 mm。
- Stage2B schema 是 `camera_tof.stage2b_all16_candidate.v2`。状态为 `CANDIDATE_NOT_PROMOTED`，variant 为 `R3_core_square_centered`。实际 candidate JSON 没有 `readiness` 字段；`READY_FOR_STAGE2C_VALIDATION` 是同 workspace 的 `build_final_all16_evidence.py`/最终报告给出的结论，不伪装成 candidate 内字段。Stage2C 绑定 candidate 的真实 path+hash，并对实际存在的 schema/status/variant/gauge/semantics 做 sanity check。
- 64 个 raw-zone authority 直接存储于 `raw_zone_camera_mapping[0..63]`，每项含 `raw_index/raw_row/raw_col/canonical_index/tof_frame_center_ray/camera_frame_center_ray/tof_frame_corner_rays_model_derived`。工具按 `raw_index` 严格检查 0..63 顺序，直接读取 frozen rays，不枚举 D4、不重新生成另一套 gauge。
- `T_camera_from_tof` 明确声明 direction 为同名字段，column-vector convention 为 `p_camera=R*p_tof+t`，translation 单位 mm。
- range semantics 明确冻结为 `axial_z`。Stage2B source 的实际路径为 `scale=distance/selected[:,2]`，所以重建 `p_tof=ray*(distance_mm/ray_z)`。
- DELTAR FR schema 是 `deltar_fr.stage2b_all16_candidate.v2`；shape 64×4、zone order 是 raw zone，包含 400/600/1000 mm 的 frozen projection/rectangle，`runtime_replaced=false`、`promotion=false`。Stage2C 只用于 footprint consistency 参考，不写入该文件。

## Reference-plane classifier and residual path

Stage2B `apply_plane_residual_diagnostics` 的实际 classifier 使用 broad observations 的 orthogonal point-to-plane absolute residual：

`threshold=max(10 mm, median(abs(r))+4.5*1.4826*MAD(abs(r)))`。

这是 zone-frame classification，不是 candidate parameter estimation，也不做 whole-scan rejection。Stage2C 原样公开 method 与 threshold；Layer A 报 classification 后的 core，Layer B/C 始终报告未裁剪 all-broad/strict，因此不会只展示筛后好看的结果。

Stage2B solver 的 residual 路径也是先按 semantics 还原 ToF point，再用 `p_camera=R*p_tof+t`，最后计算 `n·p_camera+offset`。Stage2C 独立实现同一 forward path，但不导入 Stage2B 模块，避免 optimizer 路径可达。

## Formal scan contract

正式数据根为 `C:\ec\_deltar_scan_service_data\validated`。现场抽查 `scan_20260817_161520_000`，并对照：

- PC source `C:\ec\_deltar_p4_pipeline\src\deltar_p4_pipeline\scan_contracts.py`
- JSON schema `C:\ec\_deltar_p4_pipeline\schemas\deltar_scan_input.v1.schema.json`
- capture header `C:\ec\p4\application\edge_agent\components\rehab_scan_capture\src\rehab_scan_capture_priv.h`
- Stage2B 实际 importer `stage2b_camera_tof.py::validate_scan`

确认 scan schema 为 `deltar_scan_input.v1`；frame schema 为 `deltar_scan_frame.v1`；ToF schema 为 `rehab_tof_raw.v1`。每帧实际字段包括 `camera_timestamp_us/tof_timestamp_us/pair_delta_us`，其中 delta 是绝对差；image contract 与上文一致；ToF 数组真实字段为 `distance_mm/range_sigma_mm/target_status/nb_target_detected`，各 64 项；raw key 是 `raw_row*8+raw_col`。

通用 PC JSON schema 允许 `frame_count` 1..64；但当前正式 calibration scan 与 Stage2B importer 的实际合同是精确 11 帧、目录 `frame_000000..frame_000010`。Stage2C registration 因此要求完整 11 帧后才允许 hardlink/copy。这是 formal calibration workflow 的约束，不误称为通用 schema 的全局限制。

## No-refit boundary

Stage2C CLI 只暴露 `register/status/inspect/coverage/validate/summary/suggest-next`。源码不导入 Stage2B tool 或 SciPy，不包含 parameter-estimation command；唯一的 OpenCV pose estimator 是 `solvePnP`，只估计新场景 ChArUco 平面。测试把 OpenCV Camera calibration entry points 替换为抛错 sentinel 后运行 validation，仍通过。Synthetic 扰动只验证可检出性，从不反向恢复参数。
