# Stage 2A 使用说明（Windows PowerShell）

工具不会启动 P4 scan，也不会修改正式 scan、正式 Stage7A 或任何正式仓库。推荐使用已验证含 OpenCV 4.10 的只读 Python 环境：

```powershell
$python = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$tool = 'C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01\stage2a_camera_intrinsic.py'
```

查看帮助、正式 scan 根与最新 scan（只读）：

```powershell
& $python $tool --help
& $python $tool inspect
& $python $tool inspect --latest
& $python $tool inspect --latest --detect-charuco
```

每采完一个 pose，用安全且唯一的 `pose_id` 和明确 scan ID 注册（推荐）。`pose_id` 只是数据组标签，不编码物理姿态；支持 1–64 位字母、数字以及内部的 `_`/`-`：

```powershell
& $python $tool register --pose C09 --scan-id 'scan_YYYYMMDD_HHMMSS_NNN'
& $python $tool status
```

可先只验证而不注册：

```powershell
& $python $tool register --pose C09 --scan-id 'scan_YYYYMMDD_HHMMSS_NNN' --dry-run
```

只有现场确定“最新 scan 就是该 pose”且无并行采集时，才可用：

```powershell
& $python $tool register --pose C09 --latest
```

现有 `A01..A04`、`B01..B08`、`C01..C08` 仅是 20 个 legacy/planned 标签，不是上限，也不再代表固定拍摄动作。可继续注册 `C09`、`C10`、`D01`、`EX01`、`LOW_UP_01` 等，不需改源码。注册会验证 11 帧合同并将全部 raw 硬链接进 workspace；硬链接不可用时复制。正式 source 不会修改。

已有 `pose_id` 默认明确报错且不访问/覆盖新 scan。误绑时只有显式 `--replace` 才能改工作区绑定：

```powershell
& $python $tool register --pose A01 --scan-id 'scan_CORRECT' --replace
```

任何时候可做检测、排名、contact sheet 与 coverage 诊断：

```powershell
& $python $tool rank
& $python $tool status
```

一条命令对当前所有实际已注册 pose 完成主 fit、top-2 sensitivity、outlier diagnostics、held-out validation、candidate schema 验证和旧 Stage7A 对比。没有最大组数；仍保留至少 12 个主 fit 与 8 个 held-out frame 的既有下限：

```powershell
& $python $tool run-all
```

如只有少数 pose 被标为 `CHARUCO_WEAK`，先看预览并优先补拍。确需继续且仍至少有 12 个主 fit 与 8 个 held-out frame 时，才显式运行：

```powershell
& $python $tool run-all --allow-weak
```

单独执行各阶段：

```powershell
& $python $tool calibrate
& $python $tool validate
& $python $tool compare
& $python $tool synthetic-test
```

Candidate 只写到：

`C:\ec\_calibration_workspaces\stage2a_camera_intrinsic_20260817_01\candidate\camera_intrinsics.stage2a_candidate.v1.json`

它始终标记 `CANDIDATE_NOT_PROMOTED`，绝不写入 `C:\calibration` 或正式 profile。

## 只读稳定性诊断

对当前 candidate/manifest/ranking 中全部实际 selected pose 动态执行 full-N vs top-2、no-A04、历史子集重建、N 次 LOPO 和固定 seed 的 100 次 pose 子集诊断。没有最大 pose 数；为保证 LOPO 后仍保留至少 12 个视图，稳定性诊断下限是 13 个 selected pose：

```powershell
& $python $tool stability-diagnostics
```

可复现实验参数：

```powershell
& $python $tool stability-diagnostics --subsample-runs 100 --subsample-fraction 0.8 --seed 20260817 --grid-step 16
```

`--subsample-fraction` 默认是 `0.8`。subset size 明确定义为 `ceil(N * fraction)`，因此 N=25/28/30 时分别使用 20/23/24 个 pose；summary 同时记录 requested fraction、实际 subset size 与 actual fraction。历史 `pre-edge22` 使用 A01–A04、B01–B08、C01–C10 显式重建，`pre-supplement25` 使用 A01–A04、B01–B08、C01–C13 显式重建，两者都不是当前 N 的 gate，也不会吞掉 C14 之后的新 pose。

输出仅写入 `stability_diagnostics\`。命令不会调用 `rank`、`register`、`run-all` 或主 `calibrate` 命令，不会修改 manifest、ranking 或 candidate，也不会 promotion。旧 Stage7A 属于另一颗物理相机，仅保留为历史参考，不参与本诊断结论。
