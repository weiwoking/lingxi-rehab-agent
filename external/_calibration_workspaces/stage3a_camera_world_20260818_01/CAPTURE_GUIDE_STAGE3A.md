# Stage3A 固定板 / 车辆多距离采集指南

## 冻结物理合同

- `board_mode = FIXED_VERTICAL_BOARD`
- `board_moved_between_poses = false`
- `capture_mode = VEHICLE_MULTI_DISTANCE`
- ChArUco 已正式贴死在刚性竖直柜门上，不跨拼缝，任何 pose 都不得触碰或重新粘贴。
- 有效矩形为 175×125 mm；固定有效底边高度 `912 ± 5 mm`，测量法 `manual_tape_visual`。
- Camera+ToF 固定在最终车体；Camera 光心高度 `972 ± 10 mm`。

可用下面的幂等命令确认冻结板高；不同数值会 fail closed：

```powershell
& $py $tool set-board-height --active-bottom-mm 912 --uncertainty-mm 5
```

## 每组车辆 yaw 对正

1. 四轮落在同一块平整硬地面。
2. 车头尽量正对柜门。
3. 选车体左、右两个对称且刚性的参考点。
4. 分别量两个参考点到柜门平面的距离，调整车辆 yaw，使两距离尽量相等。
5. 工程目标：条件允许时左右差 `≤2–3 mm`；`≤5 mm` 可接受并记录，不作为 hard rejection gate。
6. 车辆稳定 2–3 秒后采一组正式 11-frame scan。

## W01–W04 定义

- W01 near：车辆距板约 0.6 m。
- W02 mid：只让车辆后退到约 0.8 m，然后重新做 yaw 对正。
- W03 far：只让车辆继续后退到约 1.0 m，然后重新做 yaw 对正。
- W04 optional reposition holdout：先把车辆驶离 mid，再回到约 0.8 m，重新做 yaw 对正并采集。W04 默认不参与 W01/W02/W03 主解。

0.6/0.8/1.0 m 是现场 guidance；实际距离由每帧 PnP 估计并进入 coverage、固定高度随距离 residual 和 orientation-vs-distance 诊断。

## Exact W01 next instruction

保持 ChArUco 固定不动。把车辆放到距柜门约 0.6 m 的 near 位置；用车体左右两个对称刚性参考点量到柜门的距离并调整 yaw，左右差尽量 ≤2–3 mm、≤5 mm 可接受并记录；稳定 2–3 秒，采一组正式完整 11-frame scan，然后立即执行：

```powershell
& 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe' `
  'C:\ec\_calibration_workspaces\stage3a_camera_world_20260818_01\stage3a_camera_world.py' `
  register --pose W01 --latest --vehicle-alignment-delta-mm <LEFT_RIGHT_ABS_DIFF_MM>
```

随后执行 `inspect --pose W01` 和 `suggest-next`。

## 常用命令

```powershell
$py = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$tool = 'C:\ec\_calibration_workspaces\stage3a_camera_world_20260818_01\stage3a_camera_world.py'
& $py $tool status
& $py $tool set-height --mm 972 --uncertainty-mm 10
& $py $tool set-board-height --active-bottom-mm 912 --uncertainty-mm 5
& $py $tool register --pose W01 --latest --vehicle-alignment-delta-mm <MM>
& $py $tool inspect --pose W01
& $py $tool coverage
& $py $tool suggest-next
& $py $tool solve
& $py $tool validate
& $py $tool summary
& $py $tool export-candidate
```

兼容参数 `--board-active-bottom-height-mm` 仅用于断言；若提供，必须精确等于冻结的 912 mm，不能覆盖 manifest。重复 pose ID 或同一 scan 重复绑定会被拒绝。`export-candidate` 只在 `EVIDENCE_READY_FOR_OWNER_JUDGMENT` 时输出，且永不 promotion。
