# Stage 7B Camera–ToF 真实采集操作指南

将命令中的 `<PROJECT_ROOT>` 替换为当前仓库根目录。

本指南用于已经固定好的 SC2336 Camera + VL53L5CX 8×8 ToF。目标是采集 **12 个静止姿态 × 每姿态 5 帧**，供 PC 工具求 `T_camera_from_tof`。这不是 Stage 7C 世界坐标或离地高度标定。

## 1. 准备标定目标

1. 保留 Stage 7A 使用的 175×125 mm ChArUco 板及其现有粘贴位置；不要重印、缩放、裁切、重新粘贴或移动。
2. 固定现有白板、四张哑光卡纸和 ChArUco。当前实际哑光区域约为 **900×600 mm**，纸面与白板尽量共面，不能起皱或翘边。
3. 不要使用玻璃、显示器、亮面塑料、透光纸或镜面材料；避开强太阳光。
4. 采集时只移动整个 **P4 + Camera + ToF 刚性组件**；Camera 与 ToF 之间绝不相对移动，固定的白板/背板/ChArUco 也不移动。
5. 距离取约 0.35–0.65 m，确保 ChArUco 清晰且大背板覆盖整个 ToF FoV。

从数学上说，移动平面或移动刚性传感器组件都能产生所需相对姿态；但本项目现场方案已经冻结为“目标固定、整机移动”，不得改用移动背板。

## 2. 冻结 12 个姿态

训练集 8 个：正对近、中、远；左倾；右倾；上倾；下倾；一个 yaw+pitch 组合。  
held-out 4 个：不要复制训练姿态，分别使用新的左/右/上/下或组合倾斜，并改变距离/画面位置。

通过移动整个刚性组件，让固定板中心出现在画面左/中/右、上/中/下。每个姿态把整机放稳并等待完全静止后，再采集 5 帧；不要保存移动中的帧。

建议命名：

```text
front_near front_mid front_far tilt_left tilt_right tilt_up tilt_down yaw_pitch
heldout_left heldout_right heldout_up heldout_down
```

## 3. 每个姿态在 P4 控制台采集并发送

先确认 PC scan service 已在 `192.168.137.1:8030` 运行：

```powershell
& "<PROJECT_ROOT>\external\_conda_envs\vision_interpreter\python.exe" `
  "<PROJECT_ROOT>\external\_deltar_p4_pipeline\scripts\run_scan_service.py" `
  --host 192.168.137.1 --port 8030
```

对每个姿态执行一次以下 P4 控制台序列：

```text
rehab_scan start --frames 5 --interval-ms 1000
rehab_scan status
```

等待状态明确完成，并记录输出的 `scan_<timestamp>_<seq>`。然后：

```text
rehab_scan_send latest
rehab_scan_send status
```

等待发送/服务处理完成再移动整个刚性组件。若捕获或发送失败，先记录错误；不要把 `.incomplete` 目录或不完整上传放进 manifest，也不要用旧 scan 冒充该姿态。`latest` 是字典序最大的尚无正式结果的完整 scan；若现场有并行采集，改用明确的：

```text
rehab_scan_send scan_<timestamp>_<seq>
```

## 4. PC 目录整理与逐 scan 验证

服务端的完整上传通常位于：

```text
<PROJECT_ROOT>\external\_deltar_scan_service_data\validated\scan_<timestamp>_<seq>
```

为本次采集建立独立目录；可以让 manifest 引用 validated 中的绝对路径，无需修改或复制冻结的 `scan.json`：

```powershell
New-Item -ItemType Directory -Path 'C:\calibration\stage7b_capture_YYYYMMDD_HHMMSS'
$env:PYTHONPATH='<PROJECT_ROOT>\external\_deltar_p4_pipeline\src'
$python='<PROJECT_ROOT>\external\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
& $python -m deltar_p4_pipeline.cli scan-validate `
  --scan-dir '<PROJECT_ROOT>\external\_deltar_scan_service_data\validated\scan_REPLACE' `
  --profile-bundle '<PROJECT_ROOT>\external\_deltar_p4_pipeline\profiles'
```

每个 scan 必须输出 `status=valid`、`frame_count=5`。检查每帧都含：

```text
frame.json
image.rgb565    # 必须正好 460800 bytes
tof.json        # 64 个 distance/sigma/status/target-count
```

## 5. 生成并填写 capture manifest

```powershell
$env:PYTHONPATH='<PROJECT_ROOT>\external\_deltar_p4_pipeline\src'
$python='<PROJECT_ROOT>\external\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
& $python -m deltar_p4_pipeline.cli camera-tof-manifest-template `
  --output 'C:\calibration\stage7b_capture_YYYYMMDD_HHMMSS\calibration_manifest.json'
```

编辑 manifest：

- 将 `capture_id` 和 `created_at` 改为本次采集值。
- 每个 `scan_dir` 填对应的真实完整 scan 路径；一个姿态若分为多个 scan，可填字符串数组。
- 保持前 8 个 `split=train`、后 4 个 `split=heldout`。不得把 held-out 自动混入 train。
- `stationary_confirmed` 只在人工确认静止后保持 `true`。
- `backer.width_mm` / `backer.height_mm` 填当前真实哑光区域，约为 `900` / `600`。若使用任何仍显示 600×600 mm 的旧模板，那只是占位值，必须改成实测尺寸。
- 填写距离粗估、倾斜方向、画面位置、灯光/背板说明。
- 不要改 `camera_profile_sha256` 或 `board_manifest_sha256`，除非明确换了源文件并重新生成模板。

## 6. 运行 PC 标定

输出目录必须不存在或为空：

```powershell
& $python -m deltar_p4_pipeline.cli camera-tof-calibrate `
  --capture-manifest 'C:\calibration\stage7b_capture_YYYYMMDD_HHMMSS\calibration_manifest.json' `
  --camera-profile 'C:\calibration\result_20260719_172703\calibration_profile.v2.json' `
  --board-manifest '<PROJECT_ROOT>\external\_deltar_p4_pipeline\board_assets\board_manifest.json' `
  --output-dir 'C:\calibration\stage7b_result_YYYYMMDD_HHMMSS'
```

默认只接受 ToF status 5/9；status 255、10、`nb_target_detected=0` 一律拒绝。`--include-status-6` 只用于显式消融，不能作为正式默认。

工具总会写诊断和候选结果。只有全部 gate 为 PASS 时才写：

```text
camera_tof_extrinsics.v1.json
```

若返回 FAIL/UNRESOLVED，不要手工复制或改名 candidate 文件冒充正式 profile。优先查看：

```text
calibration_report.json
candidate_ranking.json
pose_diagnostics.json
residuals.csv
overlays\*_zone_centres.png
```

确认 overlay 中 0/7/56/63 角 zone、raw/canonical index、板 polygon 和 inlier/outlier 方向合理。proper-rotation D4 候选并列可能只是 orientation/R 表示 gauge；报告会规范化到最接近机械“大致平行、ToF 在设备正面观察者右侧”的表示，而不会伪称物理 orientation 已唯一识别。

坐标合同固定为 Camera `+X` 向图像右、`+Y` 向图像下、`+Z` 向前，且 `p_camera_mm = R_camera_from_tof * p_tof_mm + t_camera_from_tof_mm`。因此“从设备正面观察时 ToF 在 Camera 右侧”映射到 Camera 自身的左侧，即 `T_camera_from_tof.translation.x < 0`；机械先验记录 `expected_tx_mm = -20.0`。观察者左右不能直接当作 Camera-X 正负。

## 7. 结果仍不代表什么

Stage 7B PASS 只表示 Camera–ToF 相对外参已用真实数据测得。本阶段不要求固定离地高度；`world_pose_resolved` 仍为 false，`absolute_geometry_valid` 仍为 false。世界坐标和最终安装高度由 Stage 7C 处理，不能用 Stage 7B 结果提前宣称已经能计算绝对离地高度。
