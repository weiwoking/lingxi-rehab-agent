# 动作播放 EMG 与肌肉映射审计

## 实现位置

- 肌肉高亮器：`frontend/src/twin/MuscleHighlighter.ts`
- 动作阶段调度：`frontend/src/twin/AnatomyScene.ts`
- 模型业务映射：`frontend/src/twin/modelManifest.ts`
- 模型加载及右侧筛选：`frontend/src/twin/AnatomyModelLoader.ts`
- 静态模型节点审计：`frontend/public/models/body_model_audit.json`

## 四通道最终映射

| 通道 | 可见名称 | 业务 ID | `body.glb` 静态审计中存在的节点名称 |
| --- | --- | --- | --- |
| EMG1 / CH1 | 指伸肌，手指伸展激活 | `finger_extensors_right` | `Extensor digitorum`、`Extensor digitorum.001`、`Extensor digitorum.016`、`Extensor digitorum.017` |
| EMG2 / CH2 | 肱二头肌，肘屈曲募集 | `biceps_brachii_right` | `Long head of biceps brachii`、`Short head of biceps brachii` 及其编号变体 |
| EMG3 / CH3 | 肱三头肌，肘伸展募集 | `triceps_brachii_right` | `Medial head of triceps brachii`、`Lateral head of triceps brachii`、`Long head of triceps brachii` 及其编号变体 |
| EMG4 / CH4 | 尺侧腕伸肌，腕伸、手腕稳定 | `extensor_carpi_ulnaris_right` | `Ulnar head of extensor carpi ulnaris`、`Humeral head of extensor carpi ulnaris` 及其编号变体 |

上述节点名称来自现有静态模型审计文件。`modelManifest.ts` 通过节点名、编号变体和别名映射到业务 ID；`AnatomyModelLoader.meshMatchesSide()` 再以模型作者坐标系中的 `center.x < -0.0005` 只保留临床展示右侧 Mesh。`MuscleHighlighter` 还要求 manifest 项为 `type="muscle"` 且 `side="right"`，因此骨骼和左侧对象不进入播放高亮。

旧 `body_model_audit.json` 的 `runtime_mapping_validation` 仍记录上一版业务 ID，不能作为本轮四通道映射的运行证明。本轮未启动浏览器或模型加载器，四块目标肌肉的实际运行绑定数量仍需用户复核。

## 固定播放阶段

| 阶段 | EMG1 指伸肌 | EMG2 肱二头肌 | EMG3 肱三头肌 | EMG4 尺侧腕伸肌 |
| --- | ---: | ---: | ---: | ---: |
| PREPARE | 0.00 | 0.00 | 0.00 | 0.00 |
| REACH | 0.45 | 0.15 | 0.35 | 0.55 |
| LIFT | 0.25 | 0.70 | 0.10 | 0.50 |
| APPROACH_MOUTH | 0.20 | 0.95 | 0.05 | 0.60 |
| RETURN | 0.35 | 0.10 | 0.85 | 0.45 |

`AnatomyScene.applyPlaybackPhase()` 在同一个阶段调度点先调用现有 `applyDiagnosticPose()`，再调用 `MuscleHighlighter.applyPlaybackActivation()`。三角肌不在播放类型或阶段激活表中。

## 播放入口与实时入口

- 固定播放只向 `applyPlaybackActivation()` 传递业务 ID 与视觉强度，并由 `renderPlaybackActivation()` 调整现有 `MeshStandardMaterial` 的 `emissive`、`emissiveIntensity` 和透明材质 `opacity`。
- 真实实时入口保持 `AnatomyScene.updateEmg(realPayload)` 到 `MuscleHighlighter.apply(emg)`，继续读取真实 `EmgPayload.CH1` 至 `CH4`。
- 固定播放没有构造 `rms`、`mav`、`peak_abs`、`latest`、`node_id`、`sequence`、`timestamp` 或 WebSocket 数据，也没有调用 `updateEmg(fakePayload)`。
- 停止、完成、重新播放、返回记录、绿色入口、加载失败或场景销毁时，`clearPlaybackActivation()` 恢复播放前保存的材质状态。
- 浅绿色入口仍只保留真实 CSV 数据驱动动作还原的独立入口；本轮没有实现 CSV 读取或动作算法。

## 验证边界

本轮只修改 PC 医生端源码和审计文档。未修改或操作 P4、S3、固件、协议、评分服务或视觉服务；未启动服务、调用接口、连接硬件、构建或运行测试。
