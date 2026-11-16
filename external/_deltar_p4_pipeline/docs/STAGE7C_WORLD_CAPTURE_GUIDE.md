# Stage 7C-1 Camera 世界位姿实采指南

## 目标与冻结前提

本指南用于最终车体安装状态下的 Camera 世界姿态采集。此轮只采集
3 个距离 × 5 帧，并运行 PC 工具；不重新标定 Camera–ToF 相对外参，
不修改 P4，也不把 `absolute_geometry_valid` 直接改成 `true`。

冻结值：

- Camera 光学中心距最终地面：**969 ± 5 mm**（不是 1100 mm）；
- Camera 图像：640×360 RGB565_LE，物理画面 upright，软件旋转 0；
- Camera–ToF profile：
  `C:\calibration\stage7b_result_unitfix_20260724_012041\camera_tof_extrinsics.v1.json`；
- 上述 Stage 7B 文件 SHA256：
  `dbcb5792338f50cca953c365e436e17d9f044b2f47bcca716e697308dda7f8b5`；
- Camera 和 ToF 的刚性相对安装不得再变化。任何相对位移都会使 Stage 7B
  和本次 Stage 7C 全部失效。

## 冻结世界坐标

- `+X_world`：车辆右侧；
- `+Y_world`：车辆前进方向；
- `+Z_world`：竖直向上；
- 原点：最终安装状态下 Camera 光学中心正下方的地面点；
- `translation_world_from_camera_mm = [0, 0, 969]`；
- 所有矩阵为 row-major JSON，使用 column-vector，平移单位为 mm；
- `T_world_from_tof = T_world_from_camera @ T_camera_from_tof`。

禁止把世界原点放到标定板、车体几何中心或 ToF 下方，也禁止换成其他
ROS/REP-103 轴定义。

## 标定板坐标与正反面

OpenCV ChArUco 物点合同经源码和 synthetic probe 固定如下：

- `+X_board`：印刷板面向右；
- `+Y_board`：印刷板面向下；
- 右手 `+Z_board`：从车辆/Camera 一侧穿过板面，指向板后方；
- 标定板印刷正面朝车辆，因此正面法向是 `-Z_board`。

世界映射：

- `+X_board -> +X_world`；
- `+Y_board -> -Z_world`；
- `+Z_board -> +Y_world`；
- 板正面法向 `-Z_board -> -Y_world`。

工具同时要求 `solvePnP` 的 `tvec.z > 0`。正反面处理是上述确定性合同，
不会根据残差偷偷翻转。

## 白板与 ChArUco 的物理安装

继续使用现有 175×125 mm ChArUco 和约 900×600 mm 哑光白色背板。

1. 把白板/背板竖直放置，ChArUco 平面与背板贴合且同样竖直。
2. 让 ChArUco 上边沿水平，板正面朝向车辆。
3. 使用气泡水平仪或手机水平仪分别检查：
   - board roll relative to gravity 尽量不超过 0.5°；
   - board pitch from vertical 尽量不超过 0.5°。
4. 在 manifest 中填写保守的
   `board_alignment.setup_angle_uncertainty_deg`，正式输入必须 `<= 0.5`。

若能可靠测量 OpenCV board origin（印刷板左上角）到地面的高度，可填写
`board_origin_height_above_floor_mm`。工具会给出独立 Camera 高度 sanity
check；若没有可靠测量，必须保留 `null`，不得估造数值。该检查只做诊断，
永远不会覆盖实测的 969±5 mm。

## 车辆与板面对齐

1. 车辆放在平整的最终地面，轮胎正常着地。
2. 用地砖缝、激光线、长直尺或胶带中心线标出车辆前进中心线。
3. 让车辆中心线垂直指向标定板平面。
4. 车辆不得左右偏航，Camera 正前方大致指向 ChArUco 中心。
5. 三个距离之间只沿车辆前进中心线前后移动整车；板方向、Camera/ToF
   安装和车辆相对板面的方向关系都不变。

肉眼对齐不是绝对真值。工具会用 15 帧检查 Camera orientation 的一致性，
并把 setup alignment uncertainty 保留在 capture manifest 中。

## 3 距离 × 5 帧实采

推荐 Camera/车辆到板的近似距离：

- `world_front_near`：约 600–700 mm；
- `world_front_mid`：约 800–900 mm；
- `world_front_far`：约 1000–1100 mm。

每个距离：

1. 确认车辆、板和 Camera/ToF 全部静止；
2. 检查 ChArUco 完整、清晰、无强反光，至少能稳定检测 8 个角点；
3. 检查 ToF 前方无手、尺、线缆或其他遮挡，板覆盖 Camera–ToF overlap；
4. 在 P4 上执行：

   ```text
   rehab_scan start --frames 5 --interval-ms 1000
   ```

5. 记录命令返回的明确 `scan_id`，再执行：

   ```text
   rehab_scan_send <explicit_scan_id>
   ```

6. 禁止用“最新 scan”模糊匹配；为 near/mid/far 分别记录明确 scan 目录。

## 生成并填写 manifest

先生成模板：

```powershell
deltar-p4 camera-world-manifest-template `
  --output C:\calibration\stage7c_capture\camera_world_capture.v1.json
```

把模板中的三个 `scan_dir` 替换为明确的 near/mid/far scan 目录，并确认：

- 每组 5 帧、合计至少 15 帧；
- `stationary_confirmed = true`；
- `alignment_confirmed = true`；
- `approximate_distance_mm` 是合理近似值；
- Camera/Stage 7B/board 路径、ID 和 SHA256 未修改；
- 高度仍为 `969`、不确定度仍为 `5`。

如一组分成多个明确 scan，可把 `scan_dir` 改为 `scan_dirs` 数组；不得同时
保留两个字段。

## 运行标定与验证

```powershell
deltar-p4 camera-world-calibrate `
  --capture-manifest C:\calibration\stage7c_capture\camera_world_capture.v1.json `
  --camera-profile C:\calibration\result_20260719_172703\calibration_profile.v2.json `
  --camera-tof-profile C:\calibration\stage7b_result_unitfix_20260724_012041\camera_tof_extrinsics.v1.json `
  --board-manifest C:\ec\_deltar_p4_pipeline\board_assets\board_manifest.json `
  --output-dir C:\calibration\stage7c_result `
  --profile-bundle C:\ec\_deltar_p4_pipeline\profiles
```

输出目录必须为空或不存在。PASS 后可独立复核：

```powershell
deltar-p4 camera-world-validate `
  --profile C:\calibration\stage7c_result\camera_world_pose.v1.json `
  --capture-manifest C:\calibration\stage7c_capture\camera_world_capture.v1.json `
  --output-dir C:\calibration\stage7c_validation `
  --profile-bundle C:\ec\_deltar_p4_pipeline\profiles
```

## roll / pitch / yaw 诊断符号

矩阵 `R_world_from_camera` 是变换真值；Euler-like 角只用于人读诊断：

- roll 正：Camera `+X`（图像向右）朝 Camera `+Y`（图像向下）旋转，即沿
  `+Z_camera` 看是图像顺时针；
- pitch 正：Camera 光轴 `+Z_camera` 朝 `+Z_world` 上仰；
- yaw 正：Camera 光轴从车辆 `+Y_world` 前方朝 `+X_world` 右侧偏转。

正式机械合理性门禁为三者绝对值分别 `<= 5°`，不是要求人工写成 0°。

## PASS / FAIL

主要门禁包括：

- 3 个 pose、每组至少 5 帧、总计至少 15 帧；
- 每帧至少 8 个 ChArUco 角点，PnP reprojection RMS `<= 0.5 px`；
- 15 个稳健 orientation inlier；
- orientation residual RMS `<= 0.5°`、P95 `<= 1.0°`、max `<= 1.5°`；
- near/mid/far pose mean 两两夹角 `<= 1.0°`；
- roll/pitch/yaw 绝对值分别 `<= 5°`；
- 每组 ToF overlap inlier zones `>= 16`；
- ToF 世界板法向误差 `<= 5°`、plane RMS `<= 15 mm`、P95 `<= 30 mm`。

FAIL 时工具：

- 返回非零；
- 保留 report、diagnostics、CSV、overlay 和明确标记的 candidate；
- 不写 `camera_world_pose.v1.json`；
- candidate 不能冒充 measured profile。

PASS 时 `world_pose.status = measured` 且 `world_pose_resolved = true`，但
`absolute_geometry_valid` **仍必须为 false**。必须等后续真实地面/已知高度
物体验证通过后，才能由另一个受控阶段生成派生/更新 profile 并切换为 true。

本轮完成后停止：不进入 Stage 7D/7E，不立即实采之外的数据，不 build、
flash、monitor 或修改 P4。
