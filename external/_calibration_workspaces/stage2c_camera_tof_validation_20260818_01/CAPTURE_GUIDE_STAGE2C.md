# Stage2C 现场采集指南

Stage2A Camera intrinsic、Stage2B Camera↔ToF candidate、D4 gauge、raw-zone Camera ray mapping 与 `axial_z` 语义已经冻结。Stage2C 新数据只用于独立验证，不会重新估计或更新这些参数。

## 现场原则

- 第一轮可先采约 6～8 组，但这不是上限；registry 接受任意数量的安全唯一 `pose_id`。
- 姿态应相对 Stage2B 训练姿态有新颖性；采完后用 `coverage` 和 `suggest-next` 决定是否补拍，不预先冻结路线。
- 当前刚性白色柜门可以继续使用，只要 ChArUco 平面与 ToF 覆盖充分；墙不是强制条件。
- Camera+ToF 刚性结构不得改变。
- 每组 11 帧期间设备与平面保持不动；11 帧合为一个 physical validation pose，不是 11 个独立姿态。
- 采用蛇形移动；先左右、再上下；一次尽量只改变一个方向；距离变化尽量单独进行。
- 每拍一组立即 `register`，再拍下一组，避免 `--latest` 指向混淆。
- 不因单帧弱检测或单个背景长尾删除整组，不 blacklist zone，不修改 broad/strict runtime policy。

## 每组最短流程

```powershell
$python = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$tool = 'C:\ec\_calibration_workspaces\stage2c_camera_tof_validation_20260818_01\stage2c_camera_tof_validate.py'

& $python $tool register --pose V01 --latest
& $python $tool inspect
& $python $tool coverage
& $python $tool validate
& $python $tool summary
& $python $tool suggest-next
```

若不用 latest，请改为：

```powershell
& $python $tool register --pose V01 --scan-id scan_YYYYMMDD_HHMMSS_000
```

重复 `pose_id` 会报错，不会覆盖。若 frozen candidate 的路径或 SHA-256 改变，所有命令会停止并要求创建新的 Stage2C workspace。
