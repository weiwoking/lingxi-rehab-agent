---
{
  "name": "rehab-environment-scan",
  "description": "环境扫描的唯一入口：扫描环境、开始扫描、查看周围环境、查询进度，以及停止或取消扫描；不处理任务决策。",
  "metadata": {
    "cap_groups": [
      "rehab_environment_scan"
    ],
    "manage_mode": "readonly",
    "category": [
      "utility"
    ],
    "peripherals": [],
    "tags": [
      "rehab",
      "environment-scan"
    ]
  }
}
---

# Rehab Environment Scan

使用本 Skill 只处理环境原始数据采集、上传/分析、进度查询和取消请求，不负责选择康复任务。微信、WebIM 或其他 Agent 消息入口都必须调用这里列出的正式 Capability；不要在消息接收层匹配关键词，也不要直接操作 Camera、ToF、车辆、采集、SD 或上传组件。

## 工具映射

- 用户说“扫描环境”“开始环境扫描”“开始扫描”“重新扫描环境”“重新扫描”“采集环境”“帮我扫描一下环境”“帮我扫描周围”“帮我看看周围环境”或同类启动意图时，只调用一次 `rehab_environment_scan_start({})`。
- 用户说“环境扫描到哪了”“查看扫描进度”“扫描状态”“扫描完成了吗”或同类进度意图时，只调用 `rehab_environment_scan_status({})`，不要再次调用 start。
- 用户说“停止扫描”“取消环境扫描”“不要扫了”或同类停止意图时，只调用 `rehab_environment_scan_cancel({})`，不要再次调用 start，也无需用户提供 scan_id。
- 不要把“扫描环境”路由到任务决策或训练任务选择。
- “开始决策”“决策任务”“推荐任务”“重新决策”“请帮我决定一个任务”“根据刚才的环境安排任务”“给我推荐康复任务”不是环境扫描启动或状态意图，不得调用 scan start/status；应交给独立的正式任务决策 Skill，不调用任何旧任务决策能力。
- 环境扫描完成只表示场景数据已更新，不等于康复任务已经决策。

## 返回结果纪律

- start 返回 `accepted=true` 时，立即简洁确认已经开始，并尽量包含 `scan_id`；固定回复应包含：“正在进行5帧预热和11方向RGB+ToF采集，完成后将自动上传至云端。”扫描和 Upload-Only 会在设备上异步继续。
- start 返回 `busy=true` 或 `reason=already_running` 时，不重试 start；说明同一环境扫描已在进行，并使用返回的 `scan_id`、`state` 和进度。
- status 必须以本轮工具结果为准：`WARMING_CAMERA` 表示预热，`CAPTURING` / `WRITING` 表示采集，`UPLOADING` 表示上传，固定回复“11组采集已完成，正在上传至云端分析服务。”；`COMPLETED` 表示原始采集完成并上传，`CANCELLED` 表示已取消，`FAILED` 表示失败。其他运行中状态如实概括为正在准备或正在采集。
- cancel 返回 `accepted=true` 或 `reason=cancel_requested` 时，说明已请求取消；返回 `already_idle=true` 或 `reason=already_inactive` 时，如实报告当前状态，不把它说成系统错误。
- 工具失败或本轮没有相应 tool_result 时，不得猜测 scan_id、进度或完成状态。
- 扫描和上传完成时，只回复：“原始环境数据已完成采集并上传至云端。场景识别将在云端继续处理。”不得询问是否需要任务决策，也不得宣称场景识别或任务决策已经完成。

## Stage 8A 边界

本阶段只完成 5 帧预热、11 方向 RGB+ToF 原始采集、SD 完整提交和 Upload-Only。用户侧统一称“云端”和“云端分析服务”；当前演示实际由 Windows PC 本地服务充当 cloud-reference backend，不代表公网云服务器已经收到数据。自动生成 compact scene 属于后续 Stage 8B 链路；不得宣称已经识别出桌子、瓶子、书等物体，不得宣称场景分析或任务决策已经完成。

任何启动、运行、取消、失败或上传完成都不得删除、覆盖或改写 `/sdcard/rehab/selected_task.json`，也不得创建 `latest_successful_scene_result`，不得触发自动任务决策。
