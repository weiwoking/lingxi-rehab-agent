---
{
  "name": "rehab-control",
  "description": "开始、停止或查询康复训练，查询最近一次或指定历史 Session 的评分，也可查看系统状态或切换演示模式。",
  "metadata": {
    "cap_groups": [
      "rehab_integration"
    ],
    "manage_mode": "readonly"
  }
}
---

# Rehab Control

当用户要开始、停止或查看康复训练，查询训练结果/评分，查看系统就绪状态，或切换演示模式时使用本 Skill。微信与 WebIM/控制台必须使用同一组下列 Capability，不要为任何渠道另建命令或绕过 Agent。

## 实时工具调用纪律

### 设备状态只相信本轮工具结果

- 聊天历史、上一轮助手回复、旧 Session ID、长期记忆、最近评分、`last_completed_session` 和 fatigue `latest_session` 都不是当前设备状态真值。
- 当前训练状态只能来自本轮 `rehab_training_status({})` 的 `tool_result`；停止结果只能来自本轮 `rehab_training_stop` 的 `tool_result`；评分结果只能来自本轮 `rehab_score_query` 的 `tool_result`。
- 本轮没有成功调用相应工具或没有收到相应 `tool_result` 时，不得声称设备正在训练、已经停止、当前空闲或评分属于某个 Session。最终回复中的任务名、Session ID、时长、写入行数等也只能来自本轮相应 `tool_result`。

### 控制和查询意图必须实时调用工具

- 用户说“开始训练”“开始本次训练”“按当前任务开始”或同义表达时，本轮必须调用 `rehab_training_start_selected({})`；不得仅根据聊天历史声称已经开始。继续遵守下文 selected task、疲劳门禁和 start 返回值规则。
- 用户说“停止训练”“结束训练”“完成训练”“训练完成”“结束本次训练”“完成本次训练”“可以停了”“停下来”“不练了”“完成了”“结束了”“先停一下”或同义表达时，均视为停止当前训练。本轮必须立即调用 `rehab_training_stop({"reason":"user_stop"})`，无需先调用 `rehab_training_status({})`。
- 不得根据聊天历史判断“此前已经停止”而跳过 stop，不得使用旧 Session ID 决定停止对象，也不得向 `rehab_training_stop` 传 `session_id`。停止接口始终作用于设备当前唯一 Recorder；UI 可能在聊天之外启动新训练，旧聊天说 TASK018 已停止不代表设备当前没有 TASK019，直接调用幂等 stop 比根据旧历史推断更可靠。
- 停止后的最终回复只能根据本轮 `rehab_training_stop` 返回值生成，并明确区分“当前确实停止了一次训练”“当前原本就是 idle”和“调用失败或结果不确定”。tool call 失败或没有 `tool_result` 时，只能说“无法确认是否已停止，请重试”，不得说“已经停止”或“当前没有活跃训练”，也不得引用旧 TASK ID。
- 用户说“查看训练状态”“当前训练状态”“还在训练吗”“现在是否正在录制”“当前是哪个 Session”或同义表达时，本轮必须调用 `rehab_training_status({})`。当 `state=recording` 时以 `current_session` / active Session 为准；当 `state=idle` 时，`last_completed_session` 只表示历史完成记录，绝不能描述为当前正在录制的 Session。
- 用户说“查看评分”“最近评分”“看看训练结果”或同义表达时，本轮必须调用 `rehab_score_query({})`，最终标题或首句必须明确写“最近一次已完成评分”，不得描述成当前正在录制任务的评分。recording Session 尚未 finalized 时，空参数 latest 合法地可能返回更早的 Session。
- 用户明确指定完整 Session（例如“查看 20260722_TASK019 的评分”）时，本轮必须调用 `rehab_score_query({"session_id":"20260722_TASK019"})`。最终回复中的 Session ID 必须与本轮 `tool_result` 解析得到的 `session_id` 一致；不得从旧聊天中自动复用 TASK006、TASK013、TASK018 等 Session ID。

### 示例

```text
历史：助手曾说 TASK018 已停止
当前：UI 在聊天外启动了 TASK019
用户：完成训练
正确：立即调用 rehab_training_stop({"reason":"user_stop"})，使用本轮 tool_result 回复
错误：根据历史直接回复“TASK018 已停止，无需操作”

用户：还在训练吗
正确：调用 rehab_training_status({})，以 current_session 为当前真值

用户：查看评分
正确：调用 rehab_score_query({})，回复“最近一次已完成评分 — <本轮 resolved session_id>”
```

## 训练采集

- 用户说“开始训练”“开始康复训练”“执行当前推荐任务”或同义表达时，只调用 `rehab_training_start_selected({})`。任务名必须由该 Capability 从当前合法 selected task 读取；不要从聊天历史猜测、拼接、翻译或改写 `task_name`，也不要使用任何硬编码默认任务作为 fallback。
- 没有 selected task 时，如实说明 `no_selected_task`，不得改调 `rehab_training_start`。原 `rehab_training_start({"task_name":...})` 仅保留给明确调试或受控调用，日常对话路径不得使用。只依据返回结果确认是否开始，不要把 `accepted=false` 或 `training_started=false` 说成已开始。
- 用户询问当前训练状态、传感器状态、采集进度或最近一次本次启动期间完成的训练时，调用 `rehab_training_status({})`。`idle` 只表示当前没有正在录制；必须结合 `last_completed_session.exists` 回答。
- 用户要求停止当前训练时，调用 `rehab_training_stop({"reason":"user_stop"})`。除非用户语义明确对应其他枚举值，不要改用 `completed`、`write_error` 或 `sensor_timeout`。
- 训练状态中的记录队列丢弃、SD 写错误与各节点 UDP 序号缺口是不同指标；不能把队列/写错误为零表述成“没有丢包”。

## 评分查询

评分查询只读取 SD 卡中已经持久化的记录，不重新提交、不重新计算、不产生 PC Job，也不修改任何 Session 文件。

### 查询最近一次

用户说“查看评分”“查看训练结果”“刚才练得怎么样”“最近一次评分”或同义表达时，调用：

```json
rehab_score_query({})
```

这里的“最近一次”是合法 Session ID 按 `YYYYMMDD_TASKNNN` 语义排序后的最大值，不按文件修改时间选择。最新记录即使评分失败也必须返回该记录，不能跳过它去寻找更早的成功记录。

### 查询指定历史 Session

完整 Session ID：

```json
rehab_score_query({"session_id":"20260714_TASK006"})
```

简写：

```json
rehab_score_query({"session_id":"TASK006"})
```

`TASKNNN` 只在跨日期唯一匹配时使用；若返回 `session_ref_ambiguous`，请用户提供完整 `YYYYMMDD_TASKNNN`，不要根据当前日期猜测。任意历史 Session 都通过同一个 `rehab_score_query` 查询。

### 评分状态回答

- `pending`、`uploading`、`accepted`、`scoring`：说明该训练仍在处理，并请用户稍后再次查询。不要宣称已经得到分数。
- `succeeded`：用自然中文紧凑总结 Session、完成次数/目标次数、平均/最高/最低分、一致性、数据质量、全部 warnings 摘要和代偿特征检测结果。
- `failed`：只说明 SD 训练数据仍然保留、评分流程未完成、翻译后的简短原因，并建议重新开始一次新训练。本系统不对历史失败记录重新评分。
- `score_status_missing`：说明已有训练采集数据，但还没有评分状态记录；不能自动触发评分。
- `score_result_missing` 或 `score_result_invalid`：说明评分结果记录缺失或无效，不能编造分数。

常见 `last_error` 的用户可理解翻译：

- `pc_gateway_unavailable`：当前没有可用的 PC 网关地址。
- `pc_unreachable`：当时无法连接 PC 评分服务。
- `pc_service_not_ready`：当时 PC 评分服务尚未就绪。
- `pc_service_invalid`：当时 PC 评分服务响应无效。
- `unsupported_task`：训练数据已保存，当前任务暂未接入评分模型。
- `invalid_task_contract`：训练任务记录不符合评分输入合同。
- `job_create_failed`：评分任务创建失败。
- `csv_upload_failed`：训练数据上传失败。
- `job_status_failed`：评分任务状态查询失败。
- `job_not_found`：PC 端未找到对应评分任务。
- `result_download_failed`：评分结果下载失败。
- `result_contract_invalid`：评分结果格式或身份校验失败。
- `sd_write_failed`：评分结果写入 SD 卡失败。
- `session_content_conflict`：同一 Session 的内容与已有任务冲突。
- `scoring_timeout`：评分处理超时。
- `p4_restart_during_upload`：设备在上传过程中重启，原流程未完成。

### 评分表述边界

- 分数仅描述评分算法的输出，不是医学诊断，也不能据此宣称“恢复良好”“病情改善”或给出治疗结论。
- `use_with_caution` 必须翻译为“数据质量需要谨慎使用”，并说明返回的 warnings，不能忽略警告。
- `compensation_detected` 只能表述为“算法检测到/未检测到代偿特征”，不能扩展为医学判断。
- 若分数字段为 `null`，如实说明该项没有可用数值，不得补零或猜测。
- 不向用户暴露 SD/PC 绝对路径、原始 CSV、完整 HTTP 正文、内部堆栈或底层 JSON。

## 系统控制

- `rehab_system_status({})`：查询 Rehab 控制面状态、模式、generation、存储基址、错误或组件可用性。
- `rehab_demo_mode_set({"mode":"mock"})` 或 `rehab_demo_mode_set({"mode":"real"})`：仅在用户明确要求切换模式时调用。`real` 只是模式标志，不代表硬件或云服务已就绪。
- `rehab_system_reset({})`：仅在用户要求重置 Phase 1 控制状态时调用；它不删除文件、不操作硬件，也不修改 Wi-Fi、微信、Agent 或 LLM 配置。

## 通用规则

- 所有回答必须以实际 Capability 返回值为依据，不猜测或编造状态、分数或结论。
- 如实报告 `not_initialized`，不能重解释为 ready。
- 模式未变化时保留并报告返回的 `changed=false` 与 generation。
- 不调用原始 PAD、GPIO、UDP、Camera、ToF、S3、CSV、STOP、HTTP 或其他底层能力；不直接读取或修改 SD 文件。
