# 动作播放肌肉同步高亮审计

## 现有高亮能力

原肌肉高亮文件为 `frontend/src/twin/MuscleHighlighter.ts`。`bind()` 遍历 `modelManifest.ts` 的业务映射，通过 Mesh 的 `userData.anatomyBusinessId` 或 `manifestItemForObjectName()` 将当前 GLB 的实际 Mesh 按 business id 保存。

当前 manifest 明确支持的右上肢肌肉为：

- `deltoid_right`：右侧三角肌
- `finger_extensors_right`：右侧指伸肌
- `biceps_brachii_right`：右侧肱二头肌
- `triceps_brachii_right`：右侧肱三头肌
- `extensor_carpi_ulnaris_right`：右侧尺侧腕伸肌

肱二头肌、肱三头肌和三角肌具有相互独立的 business id 和 Mesh 集合，可以独立控制。当前 manifest 没有肱肌或肱桡肌的独立业务项，本轮没有猜测或新增对应 Mesh 名称。

`frontend/public/models/body_model_audit.json` 的 `runtime_mapping_validation.mapped_muscles` 已记录 `deltoid_right`、`biceps_brachii_right`、`triceps_brachii_right`。当前 manifest 对应的 GLB 节点候选来自实际模型命名规则：

- 三角肌：`Acromial part of deltoid muscle`、`Clavicular part of deltoid muscle`、`Scapular spinal part of deltoid muscle`、`Deltoid muscle` 的右侧变体；
- 肱二头肌：`Long head of biceps brachii`、`Short head of biceps brachii`、`Biceps brachii muscle` 的右侧变体；
- 肱三头肌：`Medial head of triceps brachii`、`Lateral head of triceps brachii`、`Long head of triceps brachii`、`Triceps brachii muscle` 的右侧变体。

现有高亮使用 `MeshStandardMaterial.emissive`、`emissiveIntensity`，在透明叠加显示时同时平滑调整 `opacity`。颜色继续使用原 `MuscleHighlighter` 的暗红、橙红和金橙色阶。

## 两条独立入口

真实 sEMG 入口保持不变：

```text
AnatomyScene.updateEmg()
-> MuscleHighlighter.apply(EmgPayload)
```

固定动作视觉入口为：

```text
AnatomyScene.applyPlaybackPhase()
-> MuscleHighlighter.applyPlaybackActivation()
-> MuscleHighlighter.renderPlaybackActivation()
```

固定动作入口只接收 manifest business id 对应的确定性视觉强度，没有创建、包装或发送任何 `EmgPayload`，也没有调用 `updateEmg()`。它不读取或修改真实 sEMG 缓存、日志、WebSocket、训练报告或评分结果。

## 阶段对应

姿态和肌肉状态在同一个阶段调度节点更新：

| 阶段 | 播放视觉状态 |
| --- | --- |
| `PREPARE` | 所有本轮播放高亮目标归零 |
| `REACH` | 三角肌 0.35，肱三头肌 0.25 |
| `LIFT` | 三角肌 0.65，肱二头肌 0.50 |
| `APPROACH_MOUTH` | 肱二头肌 0.90，三角肌 0.55，肱三头肌 0.10 |
| `RETURN` | 肱二头肌 0.15，肱三头肌 0.80，三角肌 0.30 |
| 最终 `PREPARE` | 平滑恢复默认材质，完整周期结束后精确清除 |

这些数值仅为 Three.js 材质视觉强度，不表示肌电测量值，不显示在页面，也不进入任何业务数据。

## Mesh 与材质边界

播放基线在现有模型完成 `setDisplayMode("muscle_bone")` 后捕获。`renderPlaybackActivation()` 只处理 manifest 中 `type=muscle` 且 `side=right` 的已绑定 Mesh；骨骼和左侧肌肉不会被播放入口高亮。

`clearPlaybackActivation()` 恢复捕获的 emissive、emissive intensity 与 opacity，不清除或改写任何真实 sEMG 数据。后续真实 `updateEmg()` 仍可按原逻辑更新同一模型。

## 停止与重复

`stopExistingArmMotion()` 同时取消阶段 timeout、恢复 PREPARE 并清除播放高亮。重新播放、返回记录、切换记录、浅绿色入口和页面卸载都会进入该清理路径。模型加载失败和有限循环完成时也会清除播放高亮。

TASK01 仍播放 5 次，TASK02 仍播放 3 次，其他记录仍播放 5 次。每一轮重新从 PREPARE 的空高亮状态开始，最终计数规则没有变化。

## 后续数据驱动入口

浅绿色入口继续调用独立 `startDataDrivenReconstruction(selectedSession)`，该函数会停止普通动作及其播放高亮，不调用固定阶段高亮。后续真实 CSV 数据驱动还原应使用真实动作或真实 sEMG 的独立控制链，不复用本轮确定性阶段强度。

## 验证边界

本轮仅完成源码审查、修改和静态搜索。未启动前后端、浏览器、HTTP、WebSocket、P4，未读取真实 sEMG，未构建或运行测试。实际 Mesh 亮度、阶段平滑度和 3/5 轮效果需用户自行运行验证。
