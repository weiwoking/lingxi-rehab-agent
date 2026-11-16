# SC2336 ChArUco 用户采集指南

当前 Stage 7A 工具已经就绪，但没有真实 SC2336 内参；请只在开发阶段完成以下一次性采集。完成真实内参前不要进入 Stage 7B。

## 1. 固定最终硬件与画面

1. 物理转正 Camera，使最终画面横屏、场景正立；不要用 PC 固定旋转补偿。
2. 保持 P4 最终输出 640×360，固定镜头、焦点、ISP、曝光链和支架。
3. 访问设备现有 `/camera-preview`，确认文字、竖线和非对称物体方向正常。
4. Stage 7A 完成前 ToF 位置仍可调整；Stage 7B 开始前 Camera 与 ToF 必须最终刚性固定。

## 2. 打印并实测板

打开 `board_assets/board.png`，A4 按 100% / actual size 打印，关闭“适合页面”和一切缩放。用尺或卡尺测量一个方格边长、整板有效宽、整板有效高。把数值写入 `board_manifest.json` 的 `print_measurement`，填入时间并设 `confirmed=true`。不要只相信打印机设置。

## 3. 保存 25–40 张有效图

可在浏览器逐张保存 JPG，命名为 `charuco_001.jpg` 至 `charuco_040.jpg`；也可运行：

```powershell
$py = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
& $py C:\ec\_deltar_p4_pipeline\scripts\collect_camera_calibration_images.py --url http://<设备地址>/camera-preview --output-dir C:\calibration\images --count 30 --interval-seconds 2
```

采集姿态清单：中心近/中/远各 2–3 张；左上、右上、左下、右下各 2–3 张；左/右边缘、上/下边缘各 1–2 张；绕水平轴向上/下倾斜各 3 张；绕垂直轴向左/右倾斜各 3 张；顺/逆时针 roll 各 2–3 张；至少三档明显不同距离。

无效图：不是 640×360、竖图或带旋转 EXIF、板太小或角点不足、全程正对且同距离、只在中心、运动模糊、过曝、反光、板弯曲、被手大面积遮挡。移动板，不移动 Camera；不要改变 ISP 或镜头。

## 4. 冻结方向并运行标定

```powershell
$py = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$env:PYTHONPATH = 'C:\ec\_deltar_p4_pipeline\src'
& $py -m deltar_p4_pipeline.cli camera-orientation-check --image C:\calibration\images\charuco_001.jpg --confirm-upright --output-manifest C:\calibration\camera_orientation_manifest.v1.json
& $py -m deltar_p4_pipeline.cli camera-calibrate-charuco --images-dir C:\calibration\images --board-manifest C:\calibration\board\board_manifest.json --orientation-manifest C:\calibration\camera_orientation_manifest.v1.json --output-dir C:\calibration\result --seed 7 --heldout-ratio 0.2 --min-valid-images 25 --min-charuco-corners 8
```

## 5. 判断结果

- `PASS`: held-out RMS ≤1.0 px，且数量、覆盖、姿态、距离及参数门禁全部通过。
- `PASS_DEMO_ONLY`: RMS >1.0 且 ≤2.0 px；只有明确接受 demo-only 并加 `--accept-demo-only` 才生成 profile。
- `FAIL`: 不生成 measured profile；按报告补拍或重拍。

检查 `camera_intrinsics_report.json` 的每图残差、拒绝原因、覆盖热图和退化警告。真实质量通过后仍要保持 Camera–ToF 外参和 world pose unresolved，直到 Stage 7B/后续阶段完成。
