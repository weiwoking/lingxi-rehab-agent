# WebSocket 协议

连接地址：`/ws/live`

每条消息统一结构：

```json
{
  "schema_version": "1.0",
  "type": "rehab.state",
  "device_id": "P4_REHAB_01",
  "patient_id": "P-0217",
  "session_id": "SESSION_DEMO_001",
  "seq": 1,
  "timestamp_ms": 0,
  "source_mode": "demo",
  "payload": {}
}
```

消息类型：

- `rehab.state`：当前完整快照。
- `rehab.telemetry`：关节角、躯干角、轨迹匹配度和空间偏差。
- `rehab.emg`：CH1-CH4 表面肌电归一化活动强度。
- `rehab.score`：单次动作评分，字段保留 `ROM_score`。
- `rehab.alert`：代偿或风险提醒。
- `rehab.event`：业务事件链。
- `rehab.report_ready`：当前会话报告已生成。
- `device.status`：设备演示状态。

前端处理规则：

- 丢弃非当前 `session_id` 的迟到消息。
- 丢弃 `seq <= lastSeq` 的乱序或重复消息。
- 自动重连并同步 `/api/state/current`。
- 断线时保留最后一帧，不把 UI 清空。

兼容扩展：

- 本轮未删除或重命名任何 WebSocket 消息类型。
- 后端原始日志在消息生成时写入，不要求浏览器回传。
- payload 后续可增加 `raw`、`received_at_ms`、`logging_status` 等字段，但现有解析保持兼容。
