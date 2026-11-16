# Stage2B 工具使用说明

```powershell
$python = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$tool = 'C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\stage2b_camera_tof.py'

& $python $tool register --pose S2B_001 --latest
& $python $tool status
& $python $tool inspect
& $python $tool coverage
& $python $tool solve
& $python $tool validate
& $python $tool stability-diagnostics
& $python $tool advanced-diagnostics --subset-trials 100 --seed 20260817
& $python $tool suggest-next
& $python $tool export-candidate
```

`register` 验证完整 11-frame contract，优先 hardlink，并在导入前后重算 formal source SHA-256。默认拒绝重复 ID；本版没有 replace，避免误覆盖。`--dry-run` 只验证/哈希，不注册。

`inspect` 解码 RGB565_LE，逐帧做 ChArUco/PnP，保存 plane、corner/marker count、PnP RMS、board coverage/center、pose 与原始 ToF zone/status/count/timing。它不因一帧弱检测而删除整组。

`solve` 枚举 8 个 D4 × radial/axial × Model A/B；Model C 是 B 上的 regularized/smoothed small per-zone tangent-ray refinement，只有 scan-level heldout 提升超过 `max(0.5 mm, 5%)` 且 correction RMS < 2° 才可进入候选。D4 的弱共向先验只用于 deterministic ranking，`AMBIGUOUS` 判定使用 data-only gap。

`validate` 的 heldout/LOPO 以整个 scan 为单位，避免 11 帧泄漏。每个 scan 内 frame 权重为 `1/sqrt(usable frames)`，避免把一次物理 pose 当成 11 个独立姿态。

`advanced-diagnostics` 对 8 个 D4 做同条件 Model B 重拟合，以 raw zone 0..63 比较 Camera-frame ray/像素/footprint；同时执行 axial/radial 的 LOPO 与 deterministic unique-pose 80% subsets，并把 `core_reference_plane_metrics` 和 `all_broad_robustness_metrics` 分开。该命令只写 `advanced_diagnostics/`，运行前后逐文件核对 manifest/imports/observations/candidate 哈希。

`export-candidate` 生成 Camera↔ToF candidate、DELTAR fr candidate 和 400/600/1000 mm projection PNG。zone boundaries 是低维模型的 geometric cell boundaries，不宣称是独立测得的光学 sensitivity contour。

工具没有 `promote` 命令。
