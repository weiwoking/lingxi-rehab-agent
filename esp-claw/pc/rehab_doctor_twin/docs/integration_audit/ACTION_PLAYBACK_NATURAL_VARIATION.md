# 动作播放自然变化静态审计

## 调整范围

本轮继续复用：

```text
AnatomyScene.applyDiagnosticPose()
-> SegmentRigAdapter.applyDiagnosticPose()
-> shoulderQuaternion() / elbowQuaternion() / wristQuaternion()
```

没有新增动画系统、模型、SVG、人体资源、材质结构、摄像机预设或大型依赖。浅绿色数据驱动入口、真实 `updateTelemetry()`、真实 `updateEmg()`、四路肌肉业务 ID 和训练记录选择流程保持原样。

## 原时间轴

原固定播放在 `AnatomyScene.startExistingArmMotion()` 中使用离散 timeout：

```text
0 ms PREPARE
500 ms REACH
1000 ms LIFT
1500 ms APPROACH_MOUTH
2200 ms RETURN
2800 ms PREPARE
3300 ms 完成本轮
```

因此单轮固定为约 3.30 秒。各姿态目标之间由 `SegmentRigAdapter.render()` 的统一指数 slerp 追赶，没有按阶段定义持续时间或 easing，每轮参数完全相同。

## 新时间轴

新基础时间如下：

| 阶段 | 基础时长 |
| --- | ---: |
| PREPARE 起始停顿 | 300 ms |
| REACH | 750 ms |
| LIFT | 900 ms |
| APPROACH_MOUTH | 800 ms |
| 口部停留 | 350 ms |
| RETURN（含回到 PREPARE） | 1100 ms |
| PREPARE 结束停顿 | 300 ms |

基础总时长为 4500 ms。移动阶段按基础时长 ±6% 变化，三个停顿按 ±28 ms 变化；按全部边界计算，单轮理论范围约为 4203～4797 ms。

RETURN 前 68% 到达现有 RETURN 关键姿态，后 32% 从当时实际姿态回到 PREPARE。RETURN 肌肉高亮保持到第二段真实返回完成，随后清除。

## 阶段插值

`SegmentRigAdapter` 在每个播放阶段开始时捕获肩、肘、腕当前实际 quaternion，并在现有唯一 render loop 中插值到仍由原 quaternion 函数产生的目标：

- REACH：`easeInOutSine`
- LIFT：`easeInOutCubic`
- APPROACH_MOUTH：`easeOutCubic`，末段减速
- RETURN 前段：`easeInOutCubic`
- RETURN 回到 PREPARE：`smootherstep`

阶段切换不会直接覆盖当前关节 quaternion。实时数据调用 `updateFromPose()` 时会取消播放阶段插值，保留真实数据驱动入口的既有所有权。

## 确定性逐轮变化

每轮种子文本为：

```text
session_id + ":" + repetitionIndex + ":playback-natural-v1"
```

种子先通过 32 位 FNV-1a 风格散列，再由轻量整数伪随机序列一次性生成本轮参数。渲染帧中不生成随机数，源码没有使用 `Math.random()`。

同一 session 的同一 repetition 始终得到相同参数；重新播放、页面刷新后重新选择相同 session，参数不会改变。不同 repetition 因 index 不同而产生细微差异。

## 姿态变化与限幅

以现有 `DRINK_CUP_KEYFRAMES` 为基准，每轮最多生成：

- 肩屈曲：±3°
- 肩外展：±2°
- 肩旋转：±2.5°
- 肘屈曲：±5°
- 腕伸展：±3°
- 前臂/腕旋前：±4°

REACH 使用 45% 偏移、LIFT 使用 80%、APPROACH_MOUTH 使用完整偏移、RETURN 使用 35%，避免各阶段机械复制同时保持动作方向。最终姿态另有限幅：

- 肩屈曲 4°～78°
- 肩外展 1°～18°
- 肩旋转 -8°～18°
- 肘屈曲 10°～124°
- 腕伸展 -12°～14°
- 旋前 4°～31°

PREPARE 不叠加偏移，确保每轮都回到同一安全起始姿态。

## 肌肉高亮

阶段激活表和四路映射未修改：

- EMG1：指伸肌
- EMG2：肱二头肌
- EMG3：肱三头肌
- EMG4：尺侧腕伸肌

动作姿态和高亮由同一个阶段调度调用。APPROACH_MOUTH 的高亮自然覆盖口部停留；RETURN 高亮覆盖 RETURN 关键姿态和回到 PREPARE 的完整 1.10 秒；返回完成才清除。这里仍只是已有固定播放视觉强度，不构造 sEMG payload，不写入曲线、CSV、session、报告或评分。

## 次数与清理

`TASK01` 和其他记录仍为 5 次，`TASK02` 仍为 3 次。计数只在 APPROACH_MOUTH、口部停留、RETURN 以及回到 PREPARE 全部结束后增加一次；结束停顿不重复计数。

重新播放、返回记录、切换记录、点击绿色入口和卸载继续使 generation 失效、清除全部 playback timeout、恢复 PREPARE 并清除播放高亮。页面卸载的 `dispose()` 继续取消场景唯一的 `requestAnimationFrame`。没有新增第二个 animation frame loop。

## 验证边界

本轮只进行了源码静态修改、搜索和差异审查。未启动前后端、评分服务、浏览器、HTTP、WebSocket、UDP、硬件，未运行构建或测试。实际 3/5 次播放、视觉自然度、模型穿插和肌肉亮度仍需用户运行验证。

本轮未修改 P4/S3、PC-only 接收、评分服务、真实训练数据、动作次数规则、模型资源、肌肉映射或浅绿色数据驱动入口。
