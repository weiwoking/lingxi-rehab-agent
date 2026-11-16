# 历史方案废弃说明

本文原先描述 HTTP frame 与自动会话方案。该方案已于 2026-07-26 废弃，不代表当前正式实现。

当前事实请阅读：

- `PC_STREAM_INGEST_AND_MANUAL_TRAINING.md`
- `PC_ONLY_RUNTIME_ARCHITECTURE.md`

当前源码没有 `POST /api/pc-stream/frames`，不会收到若干帧后自动开始，也不会因断流自动正常结束训练。
