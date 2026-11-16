# UDP 6006 接收与人工训练

## 接收

`backend/app/pc_udp_ingest_service.py` 使用 `asyncio.DatagramProtocol` 绑定 `0.0.0.0:6006`。每个 datagram 使用 `csv.reader` 解析，支持一行、多行、表头、CRLF、LF 和空行。

普通数据行必须恰好 86 列。服务保留原始字段，不插值、不平滑、不补包、不重复最后一帧。去重键为 `(source_ip, log_seq, time_ms)`，分别统计有效、无效和重复行。

## 人工训练

`POST /api/training/start` 不接受任务 ID。后端读取唯一启用且 active 的正式任务，原子预留 `YYYYMMDD_TASKNNN`，并从下一条有效 UDP 行开始缓存。

`POST /api/training/stop` 不接受 CSV、分数或报告。后端停止本轮缓存，校验至少 20 行和 2 秒；无效会话不调用评分服务且不生成正式报告。

成功输入写到：

```text
backend/data/patients/P-0217/sessions/<session_id>/
  DATA_SEQxxxxxxxxxx.csv
  task.json
  session.json
  runtime_summary.json
  score_result.json
  report.json
  agent_trace.json
```

`task.json` 只含七字段，`task_name` 固定映射为 `喝水动作模拟`，`end_reason=user_stop`。评分只调用 Job API。
