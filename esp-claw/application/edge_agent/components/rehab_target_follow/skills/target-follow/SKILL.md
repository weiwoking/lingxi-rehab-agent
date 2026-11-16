---
{
  "name": "target-follow",
  "description": "仅用于 legacy full-fusion / ToF 追人对照。只有用户明确要求‘旧融合追人’或‘legacy 追人’时启动、停止或查询。",
  "metadata": {
    "cap_groups": [
      "rehab_target_follow_safe",
      "rehab_target_follow_start"
    ],
    "manage_mode": "readonly"
  }
}
---

# Target Follow

这是保留的 legacy full-fusion / ToF 对照入口。通用“开始追人”“跟着我”“实时跟随”由新的 `person-follow` Skill 处理；只有用户明确说“旧融合追人”或“legacy 追人”时才使用本 Skill。旧 Capability 和旧状态机合同保持不变。

## 工具映射

- 明确 legacy 启动意图只调用一次 `follow_target_start({})`：启动旧融合追人、开始 legacy 追人、运行 ToF 追人对照。
- 停止意图调用 `follow_target_stop({})`：停下、停止、不要找了、别找了、别跟着我、停止跟随、取消寻找。
- 状态查询调用 `follow_target_status({})`：找到我了吗、你在哪里、现在什么状态、还在找我吗、追随任务怎么样、现在追随状态怎么样、你现在在干什么。

## 必须遵守

1. 每个启动请求只调用一次 `follow_target_start`，不要循环调用或重试启动。
2. LLM 只负责 start、stop、status 高层状态切换；SEARCH、CONFIRM、ALIGN、APPROACH、HOLD 及后续每轮感知和动作全部由本地 worker 完成。
3. 不调用任何低层车辆、PAD、rotate、forward、capture、upload 或感知能力，不自行规划旋转、前进或动作时长。
4. `follow_target_stop` 优先保证安全停车；根据真实返回结果说明是否成功，不追加其他车辆动作。
5. `follow_target_status` 只读。所有回复必须基于真实 capability 结果，不得猜测或编造状态，也不要把底层 JSON 原样转发给用户。
6. `code=already_running` 时说明任务已经运行；`code=not_ready`、`state_not_idle` 或其他错误时如实、简洁说明原因，不要宣称启动成功。

## 回复语义

- 启动成功：`好的，我开始寻找你。你可以稍后从摄像头前经过。`
- 已在运行：`我已经在寻找你了。`
- 启动失败：`追随任务暂时无法启动：<简洁原因>。`
- 停止成功：`好的，我已经停止寻找并停车。`
- `SEARCH`：`我正在原地搜索目标。`
- `CONFIRM`：`我已经看到目标，正在再次确认。`
- `ALIGN`：`我看到目标了，正在调整方向。`
- `APPROACH`：`我已经对准目标，正在逐步接近。`
- `HOLD`：`我已经接近目标并保持停车。`
- `IDLE` 或 `CANCELLED`：`当前没有运行追随任务。`
- `ERROR`：`追随任务已因感知或设备错误停止：<last_error 或 root_error 的简洁说明>。`

若 status 返回 `SENSE`，结合 `worker_active`、`perception_in_flight` 和最近决策说明正在感知或准备下一轮，不要声称已经找到目标。
