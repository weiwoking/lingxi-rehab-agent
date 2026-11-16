# Stage2B source-first 合同审查摘要

## Formal scan / Camera / ToF

- Formal scan root：`C:\ec\_deltar_scan_service_data\validated`。
- `scan.json`：`deltar_scan_input.v1`，`frame_count=11`；frames 是 `frame_000000..frame_000010`。
- 每帧：`frame.json`、`image.rgb565`、`tof.json`。Camera 是 640×360、RGB565_LE、stride 1280、460800 bytes；解码为 RGB，R=bits 11..15、G=5..10、B=0..4，无 crop/resize/rotate。
- ToF 是 `rehab_tof_raw.v1`，8×8 row-major raw linear index，字段为 `sequence/timestamp_us/distance_mm/range_sigma_mm/target_status/nb_target_detected`，每个数组 64 项。P4 原样复制 zone index；物理 scene orientation 未由源码确定。
- `camera_timestamp_us/tof_timestamp_us/pair_delta_us` 来自 `frame.json`；contract 要求 delta 等于两时间戳绝对差。当前链没有 max delta gate。
- Runtime broad DELTAR mask 位于 `tof_adapter.py`：`nb_target_detected!=0 && target_status!=255`。工程自身 usable summary 使用 `{5,6,9}`；Stage2B strict 诊断采用 count>0、status∈{5,6,9}、distance>0，但不修改正式策略。

## Camera plane

- Stage2A candidate SHA-256：`99e5fe45e56b9f9a5a05f26179a9ee124610c3d4524b4e257b88c7b149979f6b`。
- Active board geometry：7×5 squares、DICT_5X5_100、25 mm square、18 mm marker、175×125 mm active board；不依赖纸张外边距。

## Historical Stage7B

- 只读路径：`C:\calibration\stage7b_result_unitfix_20260724_012041\camera_tof_extrinsics.v1.json`。
- 合同：`T_camera_from_tof`、row-major serialization、column-vector math、translation mm。
- historical orientation 是 `rotate_90_cw`，但文件明确 `canonicalized_gauge; physical_orientation_unresolved`；nominal 45° rays 与 `axial_z` 也不是新硬件真值。

## DELTAR fr

- `tof_adapter.py` 当前通过 `canvas.regular_grid_source()` 生成 full-image uniform 8×8 rectangles，并未消费 Stage7B。
- `fr` shape 是 64×4（batch 后 1×64×4），field order `[sy,sx,ey,ex]`，source/canvas pixel units，half-open rectangles；运行时转 float tensor 并放到 model device。
- 当前 active orientation profile 是未验证 identity，与 historical Stage7B rotate_90_cw 不一致。
- Stage2B `deltar_fr` candidate 会给出 400/600/1000 mm 的 depth-dependent source-camera rectangles；translation 导致视差是预期。现有 `fr` 本身只能表达每 zone 一个 RGB patch rectangle，不能承载完整 extrinsic 或任意 ray semantics。

## VL53L5CX 可确认与不可确认

- 工程配置是 VL53L5CX、8×8、15 Hz、单 target/zone。ST 资料说明 lens 会对 scene 做水平+垂直光学翻转，datasheet/UM2884 给出 array zone numbering 和约 45°×45° detection FoV。
- 源码与官方 nominal FoV 无法唯一确认新装配的 D4 方向、实际 per-zone center rays、光学 sensitivity boundary、radial-vs-axial reading semantics、当前新相机外参。这些必须由 Stage2B 多距离/多角度实测和 gauge/observability 报告解决。
