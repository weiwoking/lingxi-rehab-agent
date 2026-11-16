# 动作还原现有模型复用审计

## 原有可动模型

- 场景入口：`frontend/src/twin/AnatomyScene.ts`
- 模型加载：`frontend/src/twin/AnatomyModelLoader.ts`
- 右上肢网格关节层级：`frontend/src/twin/SegmentRigAdapter.ts`
- 动作关键姿态与关节轴：`frontend/src/twin/MotionAxisMapping.ts`
- 最初使用页面：`frontend/src/pages/LiveMonitorPage.tsx`
- 当前动作还原页面：`frontend/src/pages/ReconstructionPage.tsx`

现有实现使用原生 Three.js、`WebGLRenderer`、`OrbitControls` 和 `requestAnimationFrame`。未使用 React Three Fiber、SVG 或 Canvas 二维骨架。模型资源继续使用 `/models/body.glb`，没有改变模型材质、相机预设或关节层级。

## 原有动作控制

`AnatomyScene.applyDiagnosticPose()` 调用 `SegmentRigAdapter.applyDiagnosticPose()`。后者读取 `DRINK_CUP_KEYFRAMES`，并通过 `shoulderQuaternion()`、`elbowQuaternion()`、`wristQuaternion()`设置原有关节目标姿态。其中肘关节屈伸目标由 `elbowQuaternion()` 产生。

`AnatomyScene.animate()` 持续执行唯一的 `requestAnimationFrame` 循环，调用 `SegmentRigAdapter.render(deltaSeconds)`，由 quaternion slerp 平滑移动肩、肘、腕。原实现可以反复触发不同姿态，但没有单次完整动作、有限循环、完成回调、次数和进度状态。

原场景卸载由 `AnatomyScene.dispose()` 取消 animation frame、断开 ResizeObserver、移除 pointer 监听、释放 controls、segment rig、模型、场景和 renderer。

## 本轮复用

在 `AnatomyScene` 上增加 `playExistingArmMotion()` 与 `stopExistingArmMotion()`。播放控制只按时间依次调用原有 `applyDiagnosticPose()`：

```text
PREPARE -> REACH -> LIFT -> APPROACH_MOUTH -> RETURN -> PREPARE
```

返回最终 PREPARE 并完成完整时间段后才增加一次计数。没有创建第二个模型实例、替代手臂、SVG 骨架、材质、摄像机或三维引擎。

有限循环使用可追踪 timeout 集合和 generation 标识。切换记录、返回记录、重新播放、绿色入口和页面卸载都会取消旧 timeout；`dispose()` 仍负责取消原有 requestAnimationFrame 和释放原有资源。

## 动作与肌肉同步

每个播放阶段由 `AnatomyScene.applyPlaybackPhase()` 同时分派原有动作姿态和播放视觉高亮。高亮继续复用 `MuscleHighlighter`、现有 GLB Mesh、manifest 业务 ID 以及材质 emissive 能力，没有创建覆盖模型的替代图层。

固定播放只使用右侧 `finger_extensors_right`、`biceps_brachii_right`、`triceps_brachii_right` 和 `extensor_carpi_ulnaris_right`。三角肌已从固定播放类型和阶段激活表移除。PREPARE 清除四块肌肉的播放强度；REACH 侧重指伸肌、肱三头肌和尺侧腕伸肌；LIFT 侧重肱二头肌；APPROACH_MOUTH 侧重肱二头肌与尺侧腕伸肌；RETURN 侧重肱三头肌。

固定播放的视觉强度不经过 `updateEmg()`，不构造 EMG payload，不写入实时曲线、日志、session、报告、评分或 CSV。真实 `updateEmg(realPayload)` 到 `MuscleHighlighter.apply()` 的数据入口保持不变。完整映射与静态模型证据见 `ACTION_PLAYBACK_EMG_MUSCLE_MAPPING.md`。

## 页面流程

页面初始只显示训练记录，不自动选中第一条。正式训练记录继续来自现有 sessions 接口；前端内存中补充 `TASK01` 和 `TASK02`，若已有同 id 记录则不重复。

- `TASK01`：5 次
- `TASK02`：3 次
- 其他及后续记录：5 次

未选记录时“动作还原”按钮禁用。用户选择并点击后才创建原有 `AnatomyScene` 并调用 `playExistingArmMotion()`。重新播放先停止旧序列、恢复 PREPARE、次数归零，再按当前记录启动。返回列表会停止并清零，选择另一记录后等待再次点击。

## 数据驱动入口

浅绿色 16px 方块位于播放区域右下角，仅保留 `aria-label="数据驱动动作还原入口"`。它调用独立的 `startDataDrivenReconstruction(selectedSession)`，不会调用普通固定次数播放函数，也不会失败后转入普通播放。

当前数据驱动函数只停止普通动画并保留当前 session 入口参数，尚未读取或解析 CSV，也未连接动作还原算法、接口、评分服务或 P4。普通播放同样不读取 CSV，不修改 session、report、score result、训练次数或 Agent 事件。

## 验证边界

本轮仅完成源码审查、修改和静态搜索。未启动前后端、浏览器、HTTP、WebSocket、P4、npm、构建或测试，实际模型加载、四块右侧肌肉运行绑定与三次/五次播放需用户自行验证。

## 2026-07-26 自然播放时间轴修订

固定播放继续使用 `AnatomyScene.applyDiagnosticPose()` 到 `SegmentRigAdapter.applyDiagnosticPose()` 的原链路。原单轮约 3.30 秒，现调整为 4.50 秒基础时间；确定性参数边界下理论范围约 4.20～4.80 秒。

基础阶段为 PREPARE 300 ms、REACH 750 ms、LIFT 900 ms、APPROACH_MOUTH 800 ms、口部停留 350 ms、RETURN 1100 ms、返回 PREPARE 后停顿 300 ms。REACH、LIFT、APPROACH_MOUTH 和 RETURN 分别使用适合阶段语义的 easing，并从阶段开始时的实际 quaternion 连续插值。

逐轮细微差异由 `session_id + repetitionIndex + playback-natural-v1` 的稳定种子生成。时长、肩肘腕目标均有限幅，未使用 `Math.random()`。同一记录重新播放会复用同一组逐轮参数。

四路肌肉阶段表未修改，但高亮时长改为跟随本轮实际阶段：APPROACH_MOUTH 覆盖口部停留，RETURN 覆盖返回关键帧和回到 PREPARE，完整返回后才清除并计数。TASK01 仍为 5 次，TASK02 仍为 3 次，其他记录仍为 5 次。

没有新增播放专用 animation frame；旧 timeout、generation、PREPARE 恢复、高亮清除和 `dispose()` 中的场景 animation frame 清理继续负责重播、返回、切换、绿色入口和卸载边界。详细参数见 `ACTION_PLAYBACK_NATURAL_VARIATION.md`。
