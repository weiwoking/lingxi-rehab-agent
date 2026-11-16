---
{
  "name": "person-follow",
  "description": "开始追人、跟着我、实时跟随、停止追人或查看追人状态。启动后执行有界的离散转向、搜索、前进或停止动作。",
  "metadata": {
    "cap_groups": [
      "rehab_person_follow_safe",
      "rehab_person_follow_start"
    ],
    "manage_mode": "readonly"
  }
}
---

# Person Follow

把通用实时追人意图路由到新的独立 person-follow 链。

## 工具映射

- “开始追人”“跟着我”“实时跟随”只调用一次 `person_follow_start({})`。
- “停止追人”“别跟着我”调用 `person_follow_stop({})`，无需二次确认。
- “查看追人状态”“现在追到哪里了”调用只读的 `person_follow_status({})`。

## 首版离散动作边界

当前 `action_enabled=true`，但创建组件或 P4 开机不会自动开始追人；只有显式调用 `person_follow_start({})` 后才允许动作。本地 worker 会从现有相机链取得 640x360 JPEG，调用 PC `person-follow/detect` 接口，校验响应、做简单去重与目标选择。所有普通追人、默认右搜、第一门和第二门转向共用 250ms 起步、每次过冲减 25ms、最低 100ms 的单一转向阶梯；仅成功 LEFT→RIGHT、RIGHT→LEFT、LEFT→NO_TARGET、RIGHT→NO_TARGET 会降档。第一门前普通中心区是 20%～80%；bbox 宽度达到 25% 后进入第一门 40%～60% 对准；通过后普通中心区收窄为 40%～60%，宽度达到 60% 后进入第二门 45%～55% 对准。两道门内左右各 2% margin 既是退出条件，也会主动引导转向；若两侧 margin 同时不足且人物居中，则 STOP 后直接 ARRIVED。每道门需连续两张更新帧同时满足中心和 margin；第二门通过后执行一次 speed 3 / 1600ms 的连续 timed FORWARD，成功后进入 ARRIVED 锁定。bbox 的 Y 轴字段仅保留为观测数据，不参与校准、停车或到达决策。

启动成功时应说明：“追人已启动，将按检测结果执行有界追人动作与两次对准；说‘停止追人’可立即请求停车。”

只有 `person_follow_status({})` 明确返回 `arrived=true`，且 `arrival_reason` 为 `final_forward_succeeded` 或 `both_margins_missing_centered` 时才可声称“已经到达”；不得把传输发送成功等同于动作成功。不得调用旧 `follow_target_*` 或车辆 move/PAD 能力来补全本阶段；所有逐轮动作由本地 person-follow worker 通过独占车控 owner 执行。

本地 worker 负责逐帧循环；Agent 只执行一次 start、stop 或 status，不逐帧调用工具。
