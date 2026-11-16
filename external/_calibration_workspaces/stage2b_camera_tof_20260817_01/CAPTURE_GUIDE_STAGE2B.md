# Stage2B Camera↔ToF 现场采集指南

状态：工具链准备完成；实际路线尚未冻结。本文给的是首轮覆盖建议，不是 schema、pose 数上限或 promotion 决策。

## 平面与装置

- Camera 与 VL53L5CX 必须继续保持刚性固定，镜头/焦距不再调整。
- 大、平、刚性的白色柜门可以正式用于 Stage2B，只要在目标距离和角度下覆盖足够多的 ToF FoV；ChArUco 必须与柜门刚性共面。
- 墙面在远距离可能提供更大的连续平面，但不是强制条件。不要仅因“墙看起来更标准”而破坏当前稳定现场。
- 门缝、铰链、门外或背景可能只污染少数 zone。工具按 frame/zone 做 robust plane-consistency 诊断，不因少量 zone 偏离而整组删除。
- 每组 11 帧期间把刚性组件保持完全静止；拍完立即 register、inspect/coverage，再看 suggest-next。

## 推荐操作习惯

优先采用蛇形路线：先左右覆盖，再上下覆盖。每次尽量只改变一个物理方向；距离变化也尽量单独进行。避免同一次同时改变距离、高度、左右位置、yaw 和 pitch。

第一轮可围绕约 10 组建立 near/mid/far、左右 yaw、上下 pitch、组合斜视和一个 far-oblique 覆盖，但“约 10 组”只是初始建议：8 组若已经可观测可开始诊断，12/16/25 组也可继续注册。工具没有最大 pose 数，真实充分性由 coverage、heldout、LOPO 与 stability 决定。

建议的渐进路线：

1. 中距离、正视附近，先从左到右蛇形移动；每步保持距离和高度近似不变。
2. 保持水平位置，逐步增加正/负 yaw；一次只改 yaw。
3. 换一条高度带，从右到左返回；一次只改高度或 pitch。
4. 单独拍 near、mid、far 距离变化，尽量保持其他量不变。
5. 根据 `suggest-next` 补右/左边缘 zone、欠覆盖 tilt 或最能区分 D4 候选的非对称姿态。

## 每组最短流程

```powershell
$python = 'C:\ec\_deltar_formal\envs\deltar_py311_torch271_cu128\python.exe'
$tool = 'C:\ec\_calibration_workspaces\stage2b_camera_tof_20260817_01\stage2b_camera_tof.py'

& $python $tool register --pose S2B_001 --latest
& $python $tool inspect
& $python $tool coverage
& $python $tool status
& $python $tool suggest-next
```

需要精确绑定时使用 `--scan-id scan_...`。`pose_id` 只是安全唯一标签，算法绝不从名称推断距离、位置或角度。可选用 `--physical-position-note` 和 `--capture-note` 留人工备注。

不要在组件仍在移动时采集。工具不增加固定 33.3 ms pair gate；它保留原始时间差，并把同组 ChArUco plane stability、ToF residual stability 和 residual↔pair_delta 相关性作为证据。

## 何时进入 solve

没有固定 N 门槛。先查看：

- near/mid/far 实测距离分布；
- yaw-like、pitch-like 与 combined tilt；
- 64 zone 的 broad/strict 覆盖、距离 spread 与角度 spread；
- 同组 11 帧 plane pose 是否稳定；
- D4 orientation observability 与外参/ray 条件性。

覆盖看起来合理后依次运行 `solve`、`validate`、`stability-diagnostics`、`export-candidate`。导出的仍是 `CANDIDATE_NOT_PROMOTED`，不修改 Stage7B、DELTAR runtime 或正式 mask/status policy。
