# Camera Intrinsics Operations

所有命令使用冻结 Python 环境，且不安装依赖：

```powershell
$py = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$env:PYTHONPATH = 'C:\ec\_deltar_p4_pipeline\src'
& $py -m deltar_p4_pipeline.cli generate-charuco-board --output-dir C:\calibration\board
& $py -m deltar_p4_pipeline.cli camera-orientation-check --image C:\calibration\images\charuco_001.jpg --confirm-upright --output-manifest C:\calibration\camera_orientation_manifest.v1.json
& $py -m deltar_p4_pipeline.cli camera-calibrate-charuco --images-dir C:\calibration\images --board-manifest C:\calibration\board\board_manifest.json --orientation-manifest C:\calibration\camera_orientation_manifest.v1.json --output-dir C:\calibration\result --seed 7 --heldout-ratio 0.2 --min-valid-images 25 --min-charuco-corners 8
```

标定使用 OpenCV Brown 五参数 `k1,k2,p1,p2,k3`，flags 为 0，不固定任何畸变系数。检测层优先使用 `CharucoDetector`，旧 OpenCV 可回退到 `detectMarkers + interpolateCornersCharuco`。拟合与 held-out 完全隔离。

held-out 输出 mean、median、RMS、P90、P95、max 及每图统计。`PASS` 的工程目标为 RMS ≤1.0 px；1.0–2.0 px 是 `PASS_DEMO_ONLY`，只有显式 `--accept-demo-only` 才能生成 measured profile；>2.0 px 为 FAIL。图像数量、覆盖、姿态、距离、正对退化、非有限参数、主点/焦距越界任一门禁失败时都不生成 measured profile。

生成的 v2 profile 只证明 Camera 内参。Camera–ToF 外参和 world pose 仍为 unresolved，`absolute_geometry_valid=false`。只有真实合格图像才会生成 `calibration_profile.v2.json`。
