# 旧 Stage7C source-first 审查结论

审查对象为 `C:\ec\_deltar_p4_pipeline` 中的只读实现，以及旧结果 `C:\calibration\stage7c_world_result_robustfix_20260724_044035\camera_world_pose.v1.json`。旧结果 SHA-256 精确匹配，但全部旧物理数值标为 `STALE_PRE_REMOUNT_REFERENCE_ONLY`。

## 复用的成熟语义

- ChArUco/PnP：OpenCV board `+X` 为纸面向右，`+Y` 为纸面向下，`+Z` 穿板远离车辆；PnP 必须 `tvec.z > 0`。
- Board→World：`R_world_from_board = [[1,0,0],[0,0,1],[0,-1,0]]`。因此 board `+X→+X_world`、`+Y→-Z_world`、`+Z→+Y_world`，纸面正面法向 `-Y_world`。
- 每帧方向：`R_world_from_camera = R_world_from_board @ R_camera_from_board.T`，再用 SVD 投影到 SO(3)。
- 聚合：geodesic medoid 初始化、MAD rejection、chordal SVD mean。
- 输出：`T_world_from_camera`、`R_world_from_camera`、`t_world_from_camera` 为矩阵优先；JSON row-major、数学使用 column-vector。
- 世界原点：Camera 光心正下方地面点，因此 `t=[0,0,camera_height_mm]`。
- Stage7D binding：下游直接校验并加载 frozen Camera World profile/hash；这确认 matrix/profile 是下游权威，不应从 RPY 重建。

## Stage3A 有意且必要的边界差异

- 旧实现无足够帧时会回退到 level rotation；Stage3A 禁止任何旧姿态或 level fallback，少于 3 个物理 pose 直接拒绝 solve。
- 旧实现先对全部帧聚合；Stage3A 先在每个物理 pose 内聚合 11 帧，再对 W01 near、W02 mid、W03 far 的 pose mean 等权聚合。固定 ChArUco 不参与 pose 间移动；W04 是独立车辆 reposition holdout，不进入主解。
- ToF 不参与 Stage3A Camera 姿态拟合，仅保留 source hash/provenance。
- 旧相机 K/D 不使用；新 K/D 只从 Stage2 Final Bundle 读取。
- 旧 969±5 mm 不使用；新高度只来自 972±10 mm manual tape input。

## 源位置与哈希

- `camera_world_calibration.py` SHA-256 `6b6726082a97b7a071f3cd994d1ead80c2ccfee9b9f235bea847b1ccdacaadd0`
  - board/world convention：lines 62–77
  - matrix construction：lines 120–132
  - board pose→world rotation：lines 135–171
  - robust orientation/pose diagnostics：lines 777–891
  - matrix-first output and SO(3) checks：lines 1091–1164
- `stage7d_contracts.py` SHA-256 `3fe94e4198fdfe081e7a67a5644f8b7351aae3754040e9a35b7f138b29e79f73`
  - formal binding constants：lines 25–45
  - hash/path/profile validation：lines 121–220

旧实现只作为上述软件合同的来源，未导入其 R、t、RPY、profile ID 或 969 mm 数值。
